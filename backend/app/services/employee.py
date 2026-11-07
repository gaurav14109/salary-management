"""The employee service.

Exists because two callers write employees - the API and the importer - so the rules
have to live in one place. "Department and band must exist, employee_code is unique,
every change is audited" is written once here rather than once per caller, and a new
way to create an employee cannot introduce a new way to create one wrongly.

Dependency direction, from the component diagram: this module may call `salary`
(exactly once, to close a leaver's open row). `salary` never calls this one - it
reads the Employee model directly. Otherwise the two would cycle.
"""

import logging
from collections.abc import Iterator, Mapping
from datetime import date
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import Conflict, UnprocessableEntity
from app.models import Band, Country, Department, Employee, Salary
from app.models.audit import Action, EntityType
from app.services import audit
from app.services import salary as salary_service

log = logging.getLogger("salary.employee")

STATUSES = ("ACTIVE", "INACTIVE")

# What a PATCH may touch. An allow-list rather than "everything except": a column
# added later is not editable until someone decides it should be, which is the safe
# default for a table holding pay-adjacent data.
#
# employee_code is absent deliberately - it is the import's match key and the audit
# trail's human handle, so renaming it silently re-points both. status is absent
# because changing it drags the salary row with it; that is set_status's job.
EDITABLE_FIELDS = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "country_code",
        "department_id",
        "job_title",
        "band_id",
    }
)

# API name -> column. The allow-list is what stops `sort` being an injection point:
# an unknown key never reaches SQL at all.
SORTABLE = {
    "employee_code": Employee.employee_code,
    "last_name": Employee.last_name,
    "first_name": Employee.first_name,
    "hire_date": Employee.hire_date,
    "job_title": Employee.job_title,
    "usd_amount": Salary.usd_amount,
}


def get(db: Session, employee_id: int) -> Employee | None:
    return db.get(Employee, employee_id)


def create(
    db: Session,
    *,
    employee_code: str,
    first_name: str,
    last_name: str,
    country_code: str,
    department_id: int,
    job_title: str,
    hire_date: date,
    actor_id: int,
    email: str | None = None,
    band_id: int | None = None,
    status: str = "ACTIVE",
) -> Employee:
    """Add a person. Audited, and refusing anything the database would refuse later."""
    if status != "ACTIVE":
        # Hiring someone already terminated is a data-entry accident, not a workflow.
        # Someone who has left is created active and then terminated, so the salary
        # row is closed by the same code path as everyone else's.
        raise UnprocessableEntity("A new employee must be ACTIVE. Terminate them afterwards.")

    _check_references(db, country_code=country_code, department_id=department_id, band_id=band_id)

    employee = Employee(
        employee_code=employee_code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        country_code=country_code.upper(),
        department_id=department_id,
        job_title=job_title,
        band_id=band_id,
        hire_date=hire_date,
        status="ACTIVE",
    )

    try:
        # A savepoint, so a duplicate code rolls back this INSERT alone. Without it
        # the failure poisons the request transaction and the caller cannot retry
        # with a corrected code - which is the obvious next thing they will do.
        with db.begin_nested():
            db.add(employee)
            db.flush()
    except IntegrityError as exc:
        raise _translate(exc, employee_code=employee_code, email=email) from exc

    audit.record(
        db,
        entity_type=EntityType.EMPLOYEE,
        entity_id=employee.id,
        action=Action.CREATE,
        old=None,
        new=_snapshot(employee),
        actor_id=actor_id,
    )
    log.info(
        "employee_created",
        extra={"employee_id": employee.id, "code": employee_code, "actor_id": actor_id},
    )
    return employee


def update(
    db: Session,
    employee_id: int,
    *,
    changes: Mapping[str, Any],
    actor_id: int,
) -> Employee:
    """Apply a partial edit, recording only what actually moved.

    Diffed before writing. An audit entry listing every column makes the one that
    changed impossible to spot, which defeats the point of keeping a trail; and an
    entry for a field someone re-saved unchanged is a change that did not happen.
    """
    employee = _employee_or_raise(db, employee_id)

    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        # Ignoring it silently would let a typo'd field name look like a saved edit.
        raise UnprocessableEntity(
            f"Not editable: {', '.join(sorted(unknown))}. "
            f"Editable fields are: {', '.join(sorted(EDITABLE_FIELDS))}."
        )

    _check_references(
        db,
        country_code=changes.get("country_code"),
        department_id=changes.get("department_id"),
        band_id=changes.get("band_id") if "band_id" in changes else None,
    )

    old: dict[str, Any] = {}
    new: dict[str, Any] = {}
    for field, value in changes.items():
        current = getattr(employee, field)
        if current == value:
            continue
        old[field] = current
        new[field] = value
        setattr(employee, field, value)

    if not new:
        log.info("employee_unchanged", extra={"employee_id": employee_id})
        return employee

    db.flush()
    audit.record(
        db,
        entity_type=EntityType.EMPLOYEE,
        entity_id=employee.id,
        action=Action.UPDATE,
        old=old,
        new=new,
        actor_id=actor_id,
    )
    log.info(
        "employee_updated",
        extra={"employee_id": employee_id, "fields": sorted(new), "actor_id": actor_id},
    )
    return employee


def set_status(
    db: Session,
    employee_id: int,
    *,
    status: str,
    termination_date: date | None,
    actor_id: int,
) -> Employee:
    """Activate or terminate, and move the salary row with it.

    Not a field edit, which is why it is not in EDITABLE_FIELDS: terminating closes
    the open salary row with the same date, in the same transaction. The database
    refuses a leaver without a date anyway (ck_employee_inactive_dated), so the two
    move together or neither does.
    """
    if status not in STATUSES:
        raise UnprocessableEntity(f"Status must be one of: {', '.join(STATUSES)}.")

    employee = _employee_or_raise(db, employee_id)
    old = {"status": employee.status, "termination_date": employee.termination_date}

    if status == "INACTIVE":
        if termination_date is None:
            raise UnprocessableEntity("A terminated employee needs a termination date.")
        if termination_date < employee.hire_date:
            raise UnprocessableEntity(
                f"Termination on {termination_date} is before the hire date {employee.hire_date}."
            )
        try:
            salary_service.close_for_termination(
                db, employee_id, termination_date=termination_date, actor_id=actor_id
            )
        except ValueError as exc:
            raise UnprocessableEntity(str(exc)) from exc
    else:
        # Reactivating clears the date but never reopens the closed salary row.
        # Rehiring is a new pay decision - at what rate, from when - and restoring
        # the old row would silently reinstate a salary nobody agreed to.
        termination_date = None

    employee.status = status
    employee.termination_date = termination_date
    db.flush()

    audit.record(
        db,
        entity_type=EntityType.EMPLOYEE,
        entity_id=employee.id,
        action=Action.UPDATE,
        old=old,
        new={"status": status, "termination_date": termination_date},
        actor_id=actor_id,
    )
    log.info(
        "employee_status_set",
        extra={"employee_id": employee_id, "status": status, "actor_id": actor_id},
    )
    return employee


def search(
    db: Session,
    *,
    q: str | None = None,
    country: str | None = None,
    department: int | None = None,
    band: int | None = None,
    status: str | None = None,
    sort: str = "last_name",
    order: str = "asc",
    page: int = 1,
    size: int = 50,
) -> tuple[list[tuple[Employee, Salary | None]], int]:
    """One page of employees with their current pay, and the total for the filter.

    A LEFT JOIN on the open salary row. Not an inner join: a new hire whose pay has
    not been entered yet must not vanish from the list that exists to let someone
    enter it.

    The join condition is `effective_to IS NULL`, which is exactly the predicate of
    ux_salary_open - so it uses the partial index rather than scanning history.
    """
    if sort not in SORTABLE:
        raise UnprocessableEntity(
            f"Cannot sort by {sort!r}. Sortable fields: {', '.join(sorted(SORTABLE))}."
        )
    if order not in ("asc", "desc"):
        raise UnprocessableEntity("Order must be 'asc' or 'desc'.")

    joined = select(Employee, Salary).outerjoin(
        Salary,
        (Salary.employee_id == Employee.id) & Salary.effective_to.is_(None),
    )
    joined = _apply_filters(
        joined, q=q, country=country, department=department, band=band, status=status
    )

    total = (
        db.scalar(
            _apply_filters(
                select(func.count()).select_from(Employee),
                q=q,
                country=country,
                department=department,
                band=band,
                status=status,
            )
        )
        or 0
    )

    column = SORTABLE[sort]
    direction = column.desc() if order == "desc" else column.asc()
    # NULLs last either way: sorting by pay descending, someone with no salary row is
    # not the highest earner, and ascending they are not the lowest.
    rows = db.execute(
        joined.order_by(direction.nullslast(), Employee.id).offset((page - 1) * size).limit(size)
    ).all()

    return [(row[0], row[1]) for row in rows], total


EXPORT_COLUMNS = (
    "employee_code",
    "first_name",
    "last_name",
    "email",
    "country_code",
    "department",
    "job_title",
    "band",
    "status",
    "hire_date",
    "termination_date",
    "amount",
    "currency",
    "usd_amount",
    "fx_units_per_usd",
    "fx_rate_date",
    "effective_from",
)


def export_rows(
    db: Session,
    *,
    q: str | None = None,
    country: str | None = None,
    department: int | None = None,
    band: int | None = None,
    status: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Every matching employee, streamed, with reference ids resolved to names.

    No pagination, deliberately: a page size on an export produces a silently
    truncated file, which is the worst failure mode here because it looks like a
    complete one.

    `yield_per` streams from a server-side cursor rather than building ten thousand
    rows in memory first. The columns are named rather than selected as entities so
    nothing loads a whole ORM object per row to read six of its fields.

    Department and band arrive as names: an export whose department column holds `7`
    is an export nobody can use without the database beside them.
    """
    statement = (
        select(
            Employee.employee_code,
            Employee.first_name,
            Employee.last_name,
            Employee.email,
            Employee.country_code,
            Department.name.label("department"),
            Employee.job_title,
            Band.name.label("band"),
            Employee.status,
            Employee.hire_date,
            Employee.termination_date,
            Salary.amount,
            Salary.currency,
            Salary.usd_amount,
            Salary.fx_units_per_usd,
            Salary.fx_rate_date,
            Salary.effective_from,
        )
        .select_from(Employee)
        .join(Department, Department.id == Employee.department_id)
        # Both outer: an employee may have no band, and may have no salary yet.
        # Either as an inner join silently drops exactly the people an export is
        # used to chase.
        .outerjoin(Band, Band.id == Employee.band_id)
        .outerjoin(Salary, (Salary.employee_id == Employee.id) & Salary.effective_to.is_(None))
        .order_by(Employee.employee_code)
    )
    statement = _apply_filters(
        statement, q=q, country=country, department=department, band=band, status=status
    )

    for row in db.execute(statement.execution_options(yield_per=500)):
        yield dict(row._mapping)


# --- helpers --------------------------------------------------------------------


def _apply_filters(
    statement: Select,
    *,
    q: str | None,
    country: str | None,
    department: int | None,
    band: int | None,
    status: str | None,
) -> Select:
    """Applied to both the page query and the count, so the total always describes
    the same set the page came from."""
    if q:
        pattern = f"%{q.lower()}%"
        statement = statement.where(
            or_(
                func.lower(Employee.last_name).like(pattern),
                func.lower(Employee.first_name).like(pattern),
                func.lower(Employee.employee_code).like(pattern),
            )
        )
    if country:
        statement = statement.where(Employee.country_code == country.upper())
    if department:
        statement = statement.where(Employee.department_id == department)
    if band:
        statement = statement.where(Employee.band_id == band)
    if status:
        statement = statement.where(Employee.status == status.upper())
    return statement


def _employee_or_raise(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise UnprocessableEntity(f"No employee with id {employee_id}.")
    return employee


def _check_references(
    db: Session,
    *,
    country_code: str | None = None,
    department_id: int | None = None,
    band_id: int | None = None,
) -> None:
    """Check foreign keys before the insert, so the caller gets "no such department"
    rather than a constraint name.

    The database still enforces all three. This is for the message, not the rule -
    a check that replaced the constraint would be a rule one code path could skip.
    """
    if country_code and db.get(Country, country_code.upper()) is None:
        raise UnprocessableEntity(f"Unknown country: {country_code}.")
    if department_id is not None and db.get(Department, department_id) is None:
        raise UnprocessableEntity(f"Unknown department: {department_id}.")
    if band_id is not None and db.get(Band, band_id) is None:
        raise UnprocessableEntity(f"Unknown band: {band_id}.")


def _translate(exc: IntegrityError, *, employee_code: str, email: str | None) -> Exception:
    """Constraint name -> sentence. Matching the name rather than the driver's text:
    the name is ours and stable, the message belongs to Postgres."""
    detail = str(exc.orig)
    if "employee_employee_code_key" in detail:
        return Conflict(f"An employee with code {employee_code} already exists.")
    if "employee_email_key" in detail and email:
        return Conflict(f"An employee with email {email} already exists.")
    return exc


def _snapshot(employee: Employee) -> dict[str, Any]:
    return {
        "employee_code": employee.employee_code,
        "first_name": employee.first_name,
        "last_name": employee.last_name,
        "email": employee.email,
        "country_code": employee.country_code,
        "department_id": employee.department_id,
        "job_title": employee.job_title,
        "band_id": employee.band_id,
        "status": employee.status,
        "hire_date": employee.hire_date,
    }
