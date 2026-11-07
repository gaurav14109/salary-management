"""The employee service.

It exists because two callers write employees - the API and the importer - and
"department and band must exist, employee_code is unique, every change is audited"
has to be written once. A second creation path would be a second way to create one
wrongly.

Two rules worth stating before the tests: every mutation audits in the caller's
transaction, and an update that changes nothing records nothing.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.errors import Conflict, UnprocessableEntity
from app.models import Employee, FxRate
from app.services.employee import create, get, search, set_status, update
from app.services.salary import get_current_salary, set_salary


@pytest.fixture
def inr_rate(db, actor_id):
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("83.45000000"),
            as_of=date(2026, 1, 1),
            created_by=actor_id,
        )
    )
    db.flush()


def new_employee(db, reference_data, actor_id, **overrides) -> Employee:
    params = {
        "employee_code": "EMP-2001",
        "first_name": "Anita",
        "last_name": "Rao",
        "email": "anita@acme.test",
        "country_code": "IN",
        "department_id": reference_data["department_id"],
        "job_title": "Engineer",
        "band_id": reference_data["band_id"],
        "hire_date": date(2024, 4, 1),
    }
    params.update(overrides)
    return create(db, actor_id=actor_id, **params)


def audit_rows(db, entity_type="EMPLOYEE") -> list:
    return db.execute(
        text(
            "SELECT action, old_value, new_value FROM audit_log WHERE entity_type = :t ORDER BY id"
        ),
        {"t": entity_type},
    ).all()


# --- creation -------------------------------------------------------------------


def test_create_returns_a_persisted_employee(db, reference_data, actor_id):
    employee = new_employee(db, reference_data, actor_id)

    assert employee.id is not None
    assert employee.status == "ACTIVE"


def test_create_writes_an_audit_row(db, reference_data, actor_id):
    new_employee(db, reference_data, actor_id)

    entries = audit_rows(db)
    assert len(entries) == 1
    assert entries[0].action == "CREATE"
    assert entries[0].old_value is None
    assert entries[0].new_value["employee_code"] == "EMP-2001"


def test_duplicate_employee_code_raises_rather_than_integrity_error(db, reference_data, actor_id):
    """The caller gets a sentence naming the code, not a constraint violation it
    cannot act on."""
    new_employee(db, reference_data, actor_id)

    with pytest.raises(Conflict) as caught:
        new_employee(db, reference_data, actor_id)

    assert "EMP-2001" in str(caught.value)


def test_a_duplicate_code_leaves_the_transaction_usable(db, reference_data, actor_id):
    """A failed insert must not poison the request: the caller may well want to try
    again with a corrected code."""
    new_employee(db, reference_data, actor_id)

    with pytest.raises(Conflict):
        new_employee(db, reference_data, actor_id)

    recovered = new_employee(
        db, reference_data, actor_id, employee_code="EMP-2002", email="anita.rao@acme.test"
    )
    assert recovered.id is not None


def test_duplicate_email_is_refused_too(db, reference_data, actor_id):
    """Found by the recovery test above reusing an address. email is UNIQUE in the
    schema, so it needs the same named error as employee_code - otherwise one of the
    two unique columns gives a sentence and the other gives a constraint name."""
    new_employee(db, reference_data, actor_id)

    with pytest.raises(Conflict) as caught:
        new_employee(db, reference_data, actor_id, employee_code="EMP-2002")

    assert "anita@acme.test" in str(caught.value)


def test_two_employees_may_share_no_email(db, reference_data, actor_id):
    """Nullable and UNIQUE: Postgres allows many NULLs, so contractors without a
    company address do not collide with each other."""
    new_employee(db, reference_data, actor_id, email=None)
    second = new_employee(db, reference_data, actor_id, employee_code="EMP-2002", email=None)

    assert second.id is not None


def test_an_unknown_department_raises_a_named_error(db, reference_data, actor_id):
    with pytest.raises(UnprocessableEntity) as caught:
        new_employee(db, reference_data, actor_id, department_id=999999)

    assert "department" in str(caught.value).lower()


def test_an_unknown_country_raises_a_named_error(db, reference_data, actor_id):
    with pytest.raises(UnprocessableEntity) as caught:
        new_employee(db, reference_data, actor_id, country_code="ZZ")

    assert "ZZ" in str(caught.value)


def test_an_employee_without_a_band_is_valid(db, reference_data, actor_id):
    """Contractors and executives sit outside a banding system."""
    employee = new_employee(db, reference_data, actor_id, band_id=None)
    assert employee.band_id is None


def test_a_termination_date_cannot_be_set_at_creation(db, reference_data, actor_id):
    """Hiring someone already terminated is a data-entry accident, not a workflow."""
    with pytest.raises(UnprocessableEntity):
        new_employee(db, reference_data, actor_id, status="INACTIVE")


# --- partial update -------------------------------------------------------------


def test_update_changes_only_what_was_given(db, reference_data, actor_id):
    employee = new_employee(db, reference_data, actor_id)

    updated = update(db, employee.id, changes={"job_title": "Senior Engineer"}, actor_id=actor_id)

    assert updated.job_title == "Senior Engineer"
    assert updated.last_name == "Rao"


def test_update_audits_only_the_fields_that_changed(db, reference_data, actor_id):
    """An audit entry listing every column makes the one that moved impossible to
    spot, which defeats the point of having a trail."""
    employee = new_employee(db, reference_data, actor_id)

    update(
        db,
        employee.id,
        changes={"job_title": "Senior Engineer", "last_name": "Rao"},
        actor_id=actor_id,
    )

    latest = audit_rows(db)[-1]
    assert latest.action == "UPDATE"
    assert set(latest.new_value) == {"job_title"}
    assert latest.old_value == {"job_title": "Engineer"}


def test_update_with_no_actual_change_writes_no_audit_row(db, reference_data, actor_id):
    employee = new_employee(db, reference_data, actor_id)
    before = len(audit_rows(db))

    update(db, employee.id, changes={"job_title": "Engineer"}, actor_id=actor_id)

    assert len(audit_rows(db)) == before


def test_update_cannot_change_the_employee_code(db, reference_data, actor_id):
    """It is the import's match key and the audit trail's human handle. Renaming it
    silently re-points both."""
    employee = new_employee(db, reference_data, actor_id)

    with pytest.raises(UnprocessableEntity):
        update(db, employee.id, changes={"employee_code": "EMP-9999"}, actor_id=actor_id)


def test_update_rejects_an_unknown_field(db, reference_data, actor_id):
    """Silently ignoring it would let a typo'd field name look like a successful save."""
    employee = new_employee(db, reference_data, actor_id)

    with pytest.raises(UnprocessableEntity):
        update(db, employee.id, changes={"salary": 100}, actor_id=actor_id)


def test_update_of_an_unknown_employee_raises(db, actor_id):
    with pytest.raises(UnprocessableEntity):
        update(db, 999999, changes={"job_title": "X"}, actor_id=actor_id)


# --- status, and the salary row it drags with it --------------------------------


def test_set_status_inactive_closes_the_open_salary_row(db, reference_data, actor_id, inr_rate):
    employee = new_employee(db, reference_data, actor_id)
    set_salary(
        db,
        employee_id=employee.id,
        amount=Decimal("1200000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )

    set_status(
        db,
        employee.id,
        status="INACTIVE",
        termination_date=date(2026, 9, 30),
        actor_id=actor_id,
    )

    assert get_current_salary(db, employee.id) is None
    closed = db.execute(
        text("SELECT effective_to FROM salary WHERE employee_id = :e"), {"e": employee.id}
    ).scalar_one()
    assert closed == date(2026, 9, 30)


def test_set_status_inactive_without_a_date_is_refused(db, reference_data, actor_id):
    """ck_employee_inactive_dated refuses it in the database too. Caught here so the
    caller gets a sentence rather than a constraint name."""
    employee = new_employee(db, reference_data, actor_id)

    with pytest.raises(UnprocessableEntity):
        set_status(db, employee.id, status="INACTIVE", termination_date=None, actor_id=actor_id)


def test_a_termination_before_the_salary_started_is_refused(db, reference_data, actor_id, inr_rate):
    """Closing a row before it opened would leave a row that ends before it starts."""
    employee = new_employee(db, reference_data, actor_id)
    set_salary(
        db,
        employee_id=employee.id,
        amount=Decimal("1200000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )

    with pytest.raises(UnprocessableEntity):
        set_status(
            db, employee.id, status="INACTIVE", termination_date=date(2026, 1, 1), actor_id=actor_id
        )


def test_terminating_someone_with_no_salary_is_fine(db, reference_data, actor_id):
    """A new hire who leaves before payroll ran. Nothing to close."""
    employee = new_employee(db, reference_data, actor_id)

    result = set_status(
        db, employee.id, status="INACTIVE", termination_date=date(2026, 9, 30), actor_id=actor_id
    )

    assert result.status == "INACTIVE"


def test_reactivating_does_not_reopen_the_closed_salary_row(db, reference_data, actor_id, inr_rate):
    """The asymmetry is deliberate. Rehiring is a new pay decision - at what rate, from
    when - not an undo. Reopening the old row would silently restore a salary nobody
    agreed to."""
    employee = new_employee(db, reference_data, actor_id)
    set_salary(
        db,
        employee_id=employee.id,
        amount=Decimal("1200000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )
    set_status(
        db, employee.id, status="INACTIVE", termination_date=date(2026, 9, 30), actor_id=actor_id
    )

    set_status(db, employee.id, status="ACTIVE", termination_date=None, actor_id=actor_id)

    assert get_current_salary(db, employee.id) is None


def test_set_status_is_audited(db, reference_data, actor_id):
    employee = new_employee(db, reference_data, actor_id)

    set_status(
        db, employee.id, status="INACTIVE", termination_date=date(2026, 9, 30), actor_id=actor_id
    )

    latest = audit_rows(db)[-1]
    assert latest.new_value["status"] == "INACTIVE"
    assert latest.old_value["status"] == "ACTIVE"


# --- search ---------------------------------------------------------------------


@pytest.fixture
def people(db, reference_data, actor_id, inr_rate):
    rows = [
        ("EMP-3001", "Priya", "Sharma", "IN", "Engineer"),
        ("EMP-3002", "Lukas", "Weber", "DE", "Designer"),
        ("EMP-3003", "Chen", "sharma", "IN", "Analyst"),
    ]
    made = []
    for code, first, last, country, title in rows:
        made.append(
            new_employee(
                db,
                reference_data,
                actor_id,
                employee_code=code,
                first_name=first,
                last_name=last,
                email=f"{code.lower()}@acme.test",
                country_code=country,
                job_title=title,
            )
        )
    set_salary(
        db,
        employee_id=made[0].id,
        amount=Decimal("1200000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )
    return made


def test_search_returns_a_page_and_a_total(db, people):
    items, total = search(db, page=1, size=2)

    assert len(items) == 2
    assert total == 3


def test_search_matches_name_or_employee_code_case_insensitively(db, people):
    by_name, _ = search(db, q="SHARMA")
    by_code, _ = search(db, q="emp-3002")

    assert {e.employee_code for e, _ in by_name} == {"EMP-3001", "EMP-3003"}
    assert {e.employee_code for e, _ in by_code} == {"EMP-3002"}


def test_filters_combine_as_and(db, people):
    items, total = search(db, country="IN", q="sharma")

    assert total == 2
    items, total = search(db, country="DE", q="sharma")
    assert total == 0


def test_an_employee_with_no_salary_still_appears_in_the_list(db, people):
    """A LEFT JOIN, not an inner one. A new hire whose pay row has not been entered
    yet must not vanish from the list that exists to let someone enter it."""
    items, total = search(db)

    assert total == 3
    without = [salary for _, salary in items if salary is None]
    assert len(without) == 2


def test_the_current_salary_comes_back_with_the_employee(db, people):
    items, _ = search(db, q="EMP-3001")

    _, salary = items[0]
    assert salary is not None
    assert salary.usd_amount == Decimal("14379.87")


def test_an_invalid_sort_field_is_refused_and_never_reaches_sql(db, people):
    """The allow-list is what stops a sort parameter being an injection point."""
    with pytest.raises(UnprocessableEntity):
        search(db, sort="salary; DROP TABLE employee")


def test_sort_by_usd_amount_puts_the_unpaid_last(db, people):
    """Descending by pay, a NULL is not the highest earner."""
    items, _ = search(db, sort="usd_amount", order="desc")

    assert items[0][1] is not None


def test_terminated_employees_can_be_filtered_out(db, people, actor_id):
    set_status(
        db, people[1].id, status="INACTIVE", termination_date=date(2026, 9, 30), actor_id=actor_id
    )

    _, total = search(db, status="ACTIVE")
    assert total == 2


def test_get_returns_none_for_an_unknown_id(db):
    assert get(db, 999999) is None
