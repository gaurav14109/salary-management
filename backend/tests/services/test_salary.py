"""Effective-dated salary writes.

The rule the whole money model rests on: a salary is never updated in place. A change
closes the current row and inserts a new one, and the USD figure is frozen at write
time with the rate that produced it. Without that, "what did we pay in Q2" has no
answer, and correcting an FX rate silently rewrites a quarter Finance already saw.

Everything here runs in one transaction: resolve the rate, close the open row, insert
the new one, write the audit entry. If any step fails, none of them happened.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.errors import BackdatedSalary, MissingFxRate, UnprocessableEntity
from app.models import FxRate, Salary
from app.services.salary import get_current_salary, get_salary_history, set_salary


@pytest.fixture
def inr_rate(db, actor_id):
    """A rate later than the seeded 2020-01-01 one, so these tests pin their own."""
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("83.45000000"),
            as_of=date(2026, 1, 1),
            created_by=actor_id,
        )
    )
    db.flush()


def write(db, employee_id, actor_id, **overrides):
    params = {
        "amount": Decimal("1200000.000"),
        "currency": "INR",
        "effective_from": date(2026, 4, 1),
    }
    params.update(overrides)
    return set_salary(db, employee_id=employee_id, actor_id=actor_id, **params)


# --- the first salary -----------------------------------------------------------


def test_first_salary_creates_open_row(db, employee_id, actor_id, inr_rate):
    result = write(db, employee_id, actor_id)

    assert result.changed is True
    assert result.salary.effective_to is None
    assert result.salary.amount == Decimal("1200000.000")


def test_usd_amount_and_rate_frozen_on_row(db, employee_id, actor_id, inr_rate):
    """Stored, not computed on read. These three columns are the record of what the
    conversion was, not a cache of what it would be now."""
    salary = write(db, employee_id, actor_id).salary

    assert salary.usd_amount == Decimal("14379.87")
    assert salary.fx_units_per_usd == Decimal("83.45000000")
    assert salary.fx_rate_date == date(2026, 1, 1)


def test_the_rate_used_is_the_one_in_force_on_the_effective_date(
    db, employee_id, actor_id, inr_rate
):
    """Not today's rate. A salary effective in April converts at April's rate even if
    it is entered in November."""
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("90.00000000"),
            as_of=date(2026, 10, 1),
            created_by=actor_id,
        )
    )
    db.flush()

    salary = write(db, employee_id, actor_id, effective_from=date(2026, 4, 1)).salary

    assert salary.fx_units_per_usd == Decimal("83.45000000")


# --- the second salary ----------------------------------------------------------


def test_second_salary_closes_previous_row_effective_to_day_before(
    db, employee_id, actor_id, inr_rate
):
    """No gap and no overlap. The previous row ends the day before the new one starts,
    so every date in between belongs to exactly one row."""
    first = write(db, employee_id, actor_id, effective_from=date(2026, 4, 1)).salary
    write(
        db, employee_id, actor_id, amount=Decimal("1400000.000"), effective_from=date(2026, 10, 1)
    )

    db.refresh(first)
    assert first.effective_to == date(2026, 9, 30)


def test_only_one_row_stays_open(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))
    write(
        db, employee_id, actor_id, amount=Decimal("1400000.000"), effective_from=date(2026, 10, 1)
    )

    open_rows = db.execute(
        text("SELECT count(*) FROM salary WHERE employee_id = :e AND effective_to IS NULL"),
        {"e": employee_id},
    ).scalar_one()
    assert open_rows == 1


def test_later_fx_rate_change_does_not_alter_existing_row(db, employee_id, actor_id, inr_rate):
    """The constraint that justifies the whole design. Correcting a rate must never
    rewrite a payroll total Finance has already received."""
    salary = write(db, employee_id, actor_id).salary
    frozen = salary.usd_amount

    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("95.00000000"),
            as_of=date(2026, 5, 1),
            created_by=actor_id,
        )
    )
    db.flush()
    db.refresh(salary)

    assert salary.usd_amount == frozen


# --- what it refuses ------------------------------------------------------------


def test_backdated_effective_from_rejected(db, employee_id, actor_id, inr_rate):
    """Review Focus #3. Backdating would mean closing the current row with a date
    before it started, which the database refuses - and rewriting a period that has
    already been reported on."""
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    # A different amount: backdating only means anything for a change that is
    # actually being made. The same amount on an earlier date is a no-op.
    with pytest.raises(BackdatedSalary):
        write(
            db,
            employee_id,
            actor_id,
            amount=Decimal("1400000.000"),
            effective_from=date(2026, 1, 1),
        )


def test_a_change_on_the_same_day_as_the_current_row_is_rejected(
    db, employee_id, actor_id, inr_rate
):
    """Closing the open row would set effective_to one day before its own
    effective_from - a row that ends before it starts."""
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    with pytest.raises(BackdatedSalary):
        write(
            db,
            employee_id,
            actor_id,
            amount=Decimal("1300000.000"),
            effective_from=date(2026, 4, 1),
        )


@pytest.fixture
def no_inr_rate(db):
    """Remove every INR rate, including the one migration 0002 seeds.

    The seed deliberately gives every currency a rate, so "no rate exists" cannot
    happen by accident any more - it has to be arranged. That is the point of the
    seed, and it is also why this fixture is explicit about what it is destroying.
    """
    db.execute(text("DELETE FROM fx_rate WHERE currency = 'INR'"))
    db.flush()


def test_missing_fx_rate_raises_and_writes_nothing(db, employee_id, actor_id, no_inr_rate):
    """Review Focus #1. No rate means no write at all - not a row with a NULL
    usd_amount, and never one converted at 1.0."""
    with pytest.raises(MissingFxRate):
        write(db, employee_id, actor_id, currency="INR", effective_from=date(2026, 4, 1))

    assert db.query(Salary).filter_by(employee_id=employee_id).count() == 0


def test_negative_amount_rejected(db, employee_id, actor_id, inr_rate):
    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(UnprocessableEntity):
            write(db, employee_id, actor_id, amount=bad)


def test_a_salary_before_the_hire_date_is_rejected(db, employee_id, actor_id, inr_rate):
    """Paid before they joined. The date is almost always a typo for the right year."""
    with pytest.raises(UnprocessableEntity):
        write(db, employee_id, actor_id, effective_from=date(2020, 1, 1))


def test_a_terminated_employee_cannot_be_given_a_salary(db, employee_id, actor_id, inr_rate):
    db.execute(
        text("UPDATE employee SET status='INACTIVE', termination_date='2026-03-31' WHERE id=:e"),
        {"e": employee_id},
    )

    with pytest.raises(UnprocessableEntity):
        write(db, employee_id, actor_id)


def test_an_unknown_employee_is_refused_before_any_lookup(db, actor_id, inr_rate):
    with pytest.raises(UnprocessableEntity):
        set_salary(
            db,
            employee_id=999999,
            amount=Decimal("1000.000"),
            currency="INR",
            effective_from=date(2026, 4, 1),
            actor_id=actor_id,
        )


# --- unchanged pay is not a change ----------------------------------------------


def test_an_identical_amount_writes_no_new_row(db, employee_id, actor_id, inr_rate):
    """What makes re-uploading an exported file safe: the same pay is not a raise,
    and must not produce a row or an audit entry claiming it was."""
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    result = write(db, employee_id, actor_id, effective_from=date(2026, 10, 1))

    assert result.changed is False
    assert db.query(Salary).filter_by(employee_id=employee_id).count() == 1


def test_an_identical_amount_on_the_same_date_is_a_no_op_not_a_backdate(
    db, employee_id, actor_id, inr_rate
):
    """The exact shape of a re-uploaded export: same pay, same effective date.

    Checked before the backdating rule. The other order made the most common upload
    fail with "use a later date", refusing a change that was not being made - found
    by the importer tests, not by these.
    """
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    result = write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    assert result.changed is False
    assert db.query(Salary).filter_by(employee_id=employee_id).count() == 1


def test_an_unchanged_write_records_no_audit_entry(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))
    before = db.execute(text("SELECT count(*) FROM audit_log")).scalar_one()

    write(db, employee_id, actor_id, effective_from=date(2026, 10, 1))

    assert db.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == before


def test_a_currency_change_at_the_same_amount_is_a_change(db, employee_id, actor_id, inr_rate):
    """1,200,000 INR and 1,200,000 USD are not the same salary."""
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    result = write(db, employee_id, actor_id, currency="USD", effective_from=date(2026, 10, 1))

    assert result.changed is True


# --- the audit trail ------------------------------------------------------------


def test_audit_row_written_with_old_and_new_amount(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id, amount=Decimal("1200000.000"), effective_from=date(2026, 4, 1))
    write(
        db, employee_id, actor_id, amount=Decimal("1400000.000"), effective_from=date(2026, 10, 1)
    )

    entry = db.execute(
        text("SELECT old_value, new_value, action FROM audit_log ORDER BY id DESC LIMIT 1")
    ).one()

    assert entry.action == "UPDATE"
    assert entry.old_value["amount"] == "1200000.000"
    assert entry.new_value["amount"] == "1400000.000"


def test_the_first_salary_is_audited_as_a_creation(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id)

    entry = db.execute(
        text("SELECT old_value, action FROM audit_log ORDER BY id DESC LIMIT 1")
    ).one()

    assert entry.action == "CREATE"
    assert entry.old_value is None


def test_the_audit_entry_records_the_frozen_usd_figure(db, employee_id, actor_id, inr_rate):
    """So the trail can be read without re-deriving a conversion."""
    write(db, employee_id, actor_id)

    entry = db.execute(text("SELECT new_value FROM audit_log ORDER BY id DESC LIMIT 1")).one()

    assert entry.new_value["usd_amount"] == "14379.87"
    assert entry.new_value["fx_units_per_usd"] == "83.45000000"


def test_a_failed_write_leaves_no_audit_entry(db, employee_id, actor_id, no_inr_rate):
    """The audit row lives or dies with the mutation it describes."""
    before = db.execute(text("SELECT count(*) FROM audit_log")).scalar_one()

    with pytest.raises(MissingFxRate):
        write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))

    assert db.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == before


# --- reading --------------------------------------------------------------------


def test_get_current_salary_returns_the_open_row(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))
    write(
        db, employee_id, actor_id, amount=Decimal("1400000.000"), effective_from=date(2026, 10, 1)
    )

    current = get_current_salary(db, employee_id)

    assert current is not None
    assert current.amount == Decimal("1400000.000")


def test_get_current_salary_is_none_for_someone_with_no_salary(db, employee_id):
    assert get_current_salary(db, employee_id) is None


def test_history_is_newest_first(db, employee_id, actor_id, inr_rate):
    write(db, employee_id, actor_id, effective_from=date(2026, 4, 1))
    write(
        db, employee_id, actor_id, amount=Decimal("1400000.000"), effective_from=date(2026, 10, 1)
    )

    history = get_salary_history(db, employee_id)

    assert [row.effective_from for row in history] == [date(2026, 10, 1), date(2026, 4, 1)]


def test_every_refusal_is_a_typed_error_not_a_bare_valueerror(db, employee_id, actor_id, inr_rate):
    """A ValueError reaching the route layer is a 500 - a page for whoever is on call
    about a request that was simply wrong. Every refusal here is a SalaryError, which
    the error handlers already know how to turn into a status and a sentence.

    Found by the API tests: four paths were returning 500 before this was fixed.
    """
    from app.errors import SalaryError

    cases = [
        {"amount": Decimal("-1")},
        {"effective_from": date(2020, 1, 1)},
        {"employee_id_override": 999999},
    ]
    for case in cases:
        employee = case.pop("employee_id_override", employee_id)
        with pytest.raises(SalaryError):
            set_salary(
                db,
                employee_id=employee,
                amount=case.get("amount", Decimal("1200000.000")),
                currency="INR",
                effective_from=case.get("effective_from", date(2026, 4, 1)),
                actor_id=actor_id,
            )
