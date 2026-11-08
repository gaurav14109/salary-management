"""Employee endpoints, including pay as a sub-resource.

Thin by design: validate, authorize, call a service, serialize. No rule about money,
dates or eligibility lives here - if one did, the importer would not be bound by it.

Salary has no route module of its own because pay is reached through a person
(`PUT /employees/{id}/salary`). The rule is one module per aggregate in `services/`,
one per URL root in `api/`; those are not always the same thing.
"""

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.access import Role, require_role
from app.db import get_db, get_streaming_session
from app.deps import current_actor
from app.errors import NotFound
from app.models import AppUser
from app.schemas import Page
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeDetail,
    EmployeeListItem,
    EmployeeRead,
    EmployeeUpdate,
    StatusUpdate,
)
from app.schemas.salary import SalaryChangeResult, SalaryRead, SalaryWrite
from app.services import employee as employee_service
from app.services import salary as salary_service

router = APIRouter(prefix="/employees", tags=["employees"])

MAX_PAGE_SIZE = 200

# Reading is the job. Writing a person is an admin act; writing their pay is the HR
# manager's. The two differ because the consequences do - a wrong salary is a
# correctable row, a wrong employee record is a person who does not exist.
READERS = require_role(Role.ADMIN, Role.HR_MANAGER)
PAY_WRITERS = require_role(Role.ADMIN, Role.HR_MANAGER)
ADMIN_ONLY = require_role(Role.ADMIN)


@router.get("", response_model=Page[EmployeeListItem], dependencies=[Depends(READERS)])
def list_employees(
    q: str | None = Query(default=None, description="Matches name or employee code."),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    department: int | None = None,
    band: int | None = None,
    employment_status: str | None = Query(default=None, alias="status"),
    sort: str = "last_name",
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> Page[EmployeeListItem]:
    """A page of employees with their current pay.

    `size` is capped in the signature, so an oversized request is a 422 from the
    framework rather than a query that returns ten thousand rows. An unknown `sort`
    is refused by the service, which owns the allow-list - the route does not get to
    decide what is sortable.
    """
    rows, total = employee_service.search(
        db,
        q=q,
        country=country,
        department=department,
        band=band,
        status=employment_status,
        sort=sort,
        order=order,
        page=page,
        size=size,
    )
    return Page[EmployeeListItem](
        items=[
            EmployeeListItem(
                employee=EmployeeRead.model_validate(person),
                current_salary=SalaryRead.model_validate(pay) if pay else None,
            )
            for person, pay in rows
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/export", dependencies=[Depends(READERS)])
def export_employees(
    q: str | None = None,
    country: str | None = Query(default=None, min_length=2, max_length=2),
    department: int | None = None,
    band: int | None = None,
    employment_status: str | None = Query(default=None, alias="status"),
    session_source=Depends(get_streaming_session),
) -> StreamingResponse:
    """The filtered list as a CSV download.

    Declared above `/{employee_id}` on purpose. FastAPI matches routes in order, so
    the other way round `/employees/export` is parsed as an id and the download
    becomes a 422 that looks like a validation bug.

    No `page` or `size`. A paginated export is a silently truncated file, which is
    the worst failure mode here because it looks like a complete one.

    It opens its own Session rather than taking `get_db`. A StreamingResponse body is
    produced *after* dependencies have torn down, so a generator holding the
    request's session would read from a closed one - and would do it only on large
    exports, in production, where the first row still arrives and the rest do not.
    """
    filters = {
        "q": q,
        "country": country,
        "department": department,
        "band": band,
        "status": employment_status,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    return StreamingResponse(
        _csv_lines(session_source, filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="employees-{stamp}.csv"'},
    )


def _csv_lines(session_source, filters: dict[str, object]):
    """Yield the file one row at a time, over a session this generator owns."""
    with session_source() as db:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=employee_service.EXPORT_COLUMNS)
        writer.writeheader()
        yield _drain(buffer)

        for row in employee_service.export_rows(db, **filters):
            writer.writerow({key: _safe(value) for key, value in row.items()})
            yield _drain(buffer)


def _drain(buffer: io.StringIO) -> str:
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


# Excel treats a cell beginning with any of these as a formula. =HYPERLINK(...) and
# =cmd|'/c calc'!A1 turn an export into code execution on the reader's machine, and
# the attacker only needs a text field - a job title will do (OWASP CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> str:
    """Neutralise a formula without destroying the data.

    A leading apostrophe makes Excel treat the cell as text. The original characters
    stay visible, because the reader still needs to see what was in the field - and
    stripping it would quietly change someone's name.

    Deliberately narrow: it fires only on the first character, so O'Brien and
    "Rao, Jr" pass through untouched. An escape that mangles ordinary names is one
    somebody turns off.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


@router.get("/{employee_id}", response_model=EmployeeDetail, dependencies=[Depends(READERS)])
def get_employee(employee_id: int, db: Session = Depends(get_db)) -> EmployeeDetail:
    """One employee, their current pay, and how it got there.

    404 here, where the id addresses a resource. The same unknown id on a PATCH is a
    422, because there it is a field of a request that cannot be processed.
    """
    person = employee_service.get(db, employee_id)
    if person is None:
        raise NotFound(f"No employee with id {employee_id}.")

    history = salary_service.get_salary_history(db, employee_id)
    current = next((row for row in history if row.effective_to is None), None)
    names = salary_service.actor_names(db, history)

    def as_read(row) -> SalaryRead:
        """Attach the author's name to the row the ORM gave us.

        Resolved here rather than by a relationship on the model: the timeline is the
        only reader that wants it, and a lazy-loaded `salary.author` would fire a
        query per row from every other caller that happened to touch the attribute.
        """
        entry = SalaryRead.model_validate(row)
        return entry.model_copy(update={"created_by_name": names.get(row.created_by)})

    return EmployeeDetail(
        employee=EmployeeRead.model_validate(person),
        current_salary=as_read(current) if current else None,
        history=[as_read(row) for row in history],
    )


@router.post(
    "",
    response_model=EmployeeRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ADMIN_ONLY)],
)
def create_employee(
    body: EmployeeCreate,
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> EmployeeRead:
    person = employee_service.create(
        db,
        employee_code=body.employee_code,
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        country_code=body.country_code,
        department_id=body.department_id,
        job_title=body.job_title,
        band_id=body.band_id,
        hire_date=body.hire_date,
        actor_id=actor.id,
    )
    return EmployeeRead.model_validate(person)


@router.patch("/{employee_id}", response_model=EmployeeRead, dependencies=[Depends(ADMIN_ONLY)])
def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> EmployeeRead:
    """Partial edit.

    `exclude_unset` is what makes it partial: a field the client did not send is
    absent rather than None, so "clear the email" and "leave the email alone" stay
    distinguishable. Without it, every PATCH would blank every field it omitted.
    """
    person = employee_service.update(
        db,
        employee_id,
        changes=body.model_dump(exclude_unset=True),
        actor_id=actor.id,
    )
    return EmployeeRead.model_validate(person)


@router.put(
    "/{employee_id}/status", response_model=EmployeeRead, dependencies=[Depends(ADMIN_ONLY)]
)
def set_employee_status(
    employee_id: int,
    body: StatusUpdate,
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> EmployeeRead:
    """Activate or terminate.

    Its own route rather than a PATCH field, because terminating closes the open
    salary row with the same date in the same transaction. A caller should be able to
    see from the URL that this does more than set a column.
    """
    person = employee_service.set_status(
        db,
        employee_id,
        status=body.status,
        termination_date=body.termination_date,
        actor_id=actor.id,
    )
    return EmployeeRead.model_validate(person)


@router.put(
    "/{employee_id}/salary",
    response_model=SalaryChangeResult,
    dependencies=[Depends(PAY_WRITERS)],
)
def set_salary(
    employee_id: int,
    body: SalaryWrite,
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> SalaryChangeResult:
    """Record what this employee is paid from `effective_from` onward.

    The body carries no `usd_amount`, `fx_units_per_usd` or `fx_rate_date`: those are
    derived from the rate in force on the effective date. Accepting them would let
    the browser decide what a salary is worth in the reporting currency.

    `changed` is false when the amount and currency matched what was already in
    force - no row written, no audit entry. The UI says "no change" instead of
    claiming a raise.
    """
    result = salary_service.set_salary(
        db,
        employee_id=employee_id,
        amount=body.amount,
        currency=body.currency,
        effective_from=body.effective_from,
        change_reason=body.change_reason,
        actor_id=actor.id,
    )
    return SalaryChangeResult(
        changed=result.changed,
        salary=SalaryRead.model_validate(result.salary),
    )
