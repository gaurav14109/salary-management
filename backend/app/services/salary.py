"""Effective-dated salary writes.

A salary is never updated in place. A change closes the current row and inserts a new
one, so "what did we pay in Q2" has an answer that does not change when someone gets
a raise in Q4.

The USD figure is frozen on the row at write time, together with the rate and the
rate's date. That duplication is the point: converting at read time would mean
correcting one FX rate silently rewrites a payroll total Finance already received.

Everything below runs inside the caller's transaction. Resolve the rate, close the
open row, insert the new one, record the audit entry - and if any step fails, none of
them happened.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import BackdatedSalary, UnprocessableEntity
from app.models import Employee, Salary
from app.models.audit import Action, EntityType
from app.services import audit, fx

log = logging.getLogger("salary.salary")


@dataclass(frozen=True)
class SalaryChange:
    """The outcome of a write.

    A dataclass, not a Pydantic model: it never crosses the HTTP boundary. The API
    layer turns it into a response; the importer reads `changed` to count rows it
    skipped.

    `changed` exists because an identical amount is not a raise. Re-uploading an
    exported file must not produce a row and an audit entry per employee claiming
    their pay changed to what it already was - and the caller still needs to know
    which it was, so this says so rather than returning None for a no-op.
    """

    salary: Salary
    changed: bool


def set_salary(
    db: Session,
    *,
    employee_id: int,
    amount: Decimal,
    currency: str,
    effective_from: date,
    actor_id: int,
    change_reason: str | None = None,
) -> SalaryChange:
    """Record what this employee is paid from `effective_from` onward.

    Order matters and is deliberate: everything that can refuse the write happens
    before anything is written, so a rejection leaves the transaction clean rather
    than half-applied and relying on the caller to roll back.
    """
    employee = _employee_or_raise(db, employee_id)
    _check_amount(amount)
    _check_dates(employee, effective_from)

    current = get_current_salary(db, employee_id)

    if current is not None:
        # Unchanged first, backdating second, and the order is load-bearing.
        #
        # The common import is a re-upload of an exported file: same people, same
        # pay, same effective dates. Checking the date first made that the single
        # most likely upload fail with "use a later date" - refusing a change that
        # was not being made. Nothing to change means nothing to refuse.
        if current.amount == amount and current.currency == currency.upper():
            log.info(
                "salary_unchanged",
                extra={"employee_id": employee_id, "effective_from": str(effective_from)},
            )
            return SalaryChange(salary=current, changed=False)

        if effective_from <= current.effective_from:
            raise BackdatedSalary(effective_from, current.effective_from)

    # Resolved before anything is written: a missing rate must abort the write, not
    # leave a closed row behind with no replacement.
    rate = fx.get_rate(db, currency, effective_from)
    try:
        usd_amount = fx.to_usd(amount, rate)
    except ValueError as exc:
        # fx.to_usd is a pure function and raises ValueError, which keeps it free of
        # any notion of HTTP. Translated here, at the boundary of the request-shaped
        # world, rather than making the arithmetic know about status codes.
        raise UnprocessableEntity(str(exc)) from exc
    rate_date = _rate_date(db, currency, effective_from)

    if current is not None:
        # The day before, so every date belongs to exactly one row: no gap to fall
        # into, no overlap to double-count.
        current.effective_to = effective_from - timedelta(days=1)
        db.flush()

    salary = Salary(
        employee_id=employee_id,
        amount=amount,
        currency=currency.upper(),
        usd_amount=usd_amount,
        fx_units_per_usd=rate,
        fx_rate_date=rate_date,
        effective_from=effective_from,
        effective_to=None,
        change_reason=change_reason,
        created_by=actor_id,
    )
    db.add(salary)
    db.flush()

    audit.record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=salary.id,
        action=Action.UPDATE if current is not None else Action.CREATE,
        old=_snapshot(current),
        new=_snapshot(salary),
        actor_id=actor_id,
    )

    log.info(
        "salary_set",
        extra={
            "employee_id": employee_id,
            "effective_from": str(effective_from),
            "currency": salary.currency,
            "actor_id": actor_id,
        },
    )
    return SalaryChange(salary=salary, changed=True)


def close_for_termination(
    db: Session,
    employee_id: int,
    *,
    termination_date: date,
    actor_id: int,
) -> Salary | None:
    """End the open salary row on the day someone leaves.

    The one place EmployeeService reaches into this module, and the dependency runs
    only that way: salary.py reads the Employee model but never calls employee.py.

    Returns None when there is nothing to close - a new hire who leaves before
    payroll ran is a real case, not an error.

    Note what this does *not* do: it never reopens. Reactivating someone later is a
    new pay decision - at what rate, from when - and restoring the old row would
    silently reinstate a salary nobody agreed to.
    """
    current = get_current_salary(db, employee_id)
    if current is None:
        return None

    if termination_date < current.effective_from:
        raise ValueError(
            f"Termination on {termination_date} is before the current salary started "
            f"on {current.effective_from}."
        )

    old = _snapshot(current)
    current.effective_to = termination_date
    db.flush()

    audit.record(
        db,
        entity_type=EntityType.SALARY,
        entity_id=current.id,
        action=Action.UPDATE,
        old=old,
        new=_snapshot(current),
        actor_id=actor_id,
    )
    log.info(
        "salary_closed_for_termination",
        extra={"employee_id": employee_id, "termination_date": str(termination_date)},
    )
    return current


def get_current_salary(db: Session, employee_id: int) -> Salary | None:
    """The open row, or None. Exactly one can be open - a partial unique index says so."""
    return db.scalar(
        select(Salary).where(Salary.employee_id == employee_id, Salary.effective_to.is_(None))
    )


def get_salary_history(db: Session, employee_id: int) -> list[Salary]:
    """Every row for this employee, newest first - the order it is read in."""
    return list(
        db.scalars(
            select(Salary)
            .where(Salary.employee_id == employee_id)
            .order_by(Salary.effective_from.desc())
        )
    )


# --- refusals, each before anything is written ----------------------------------


def _employee_or_raise(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise UnprocessableEntity(f"No employee with id {employee_id}.")
    if employee.status != "ACTIVE":
        # A leaver's pay is history. Changing it would reopen a period that payroll
        # has closed, and the database would refuse the row anyway once the
        # termination date closed it.
        raise UnprocessableEntity(
            f"Employee {employee.employee_code} has left and cannot be given a salary."
        )
    return employee


def _check_amount(amount: Decimal) -> None:
    if amount <= 0:
        raise UnprocessableEntity(f"Salary must be positive, got {amount}.")


def _check_dates(employee: Employee, effective_from: date) -> None:
    if effective_from < employee.hire_date:
        # Paid before they joined. Almost always the right day in the wrong year.
        raise UnprocessableEntity(
            f"Salary cannot start on {effective_from}: "
            f"{employee.employee_code} was hired on {employee.hire_date}."
        )


def _rate_date(db: Session, currency: str, effective_from: date) -> date:
    """The date of the rate actually used, not the date it was asked for.

    Recorded so the row says which published rate produced its USD figure. For USD
    there is no row to point at, so the effective date stands in - the conversion is
    the identity and there is nothing to look up.
    """
    if currency.upper() == fx.REPORTING_CURRENCY:
        return effective_from

    from app.models import FxRate

    return db.scalar(
        select(FxRate.as_of)
        .where(FxRate.currency == currency.upper(), FxRate.as_of <= effective_from)
        .order_by(FxRate.as_of.desc())
        .limit(1)
    )


def _snapshot(salary: Salary | None) -> dict[str, object] | None:
    """What the audit trail records. Decimals stay Decimal - audit.record turns them
    into strings, because a JSONB number would quietly round them."""
    if salary is None:
        return None
    return {
        "amount": salary.amount,
        "currency": salary.currency,
        "usd_amount": salary.usd_amount,
        "fx_units_per_usd": salary.fx_units_per_usd,
        "fx_rate_date": salary.fx_rate_date,
        "effective_from": salary.effective_from,
        "effective_to": salary.effective_to,
    }
