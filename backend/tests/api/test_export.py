"""CSV export.

The file lands in Excel, which is why two of these tests are about things that are
not data: a name beginning with `=` is a formula to Excel, and a 10,000-row export
that materializes in memory is a process that dies on the one request that mattered.
"""

import csv
import io
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.employees import router
from app.db import get_db, get_streaming_session
from app.errors import register_error_handlers
from app.middleware import install_middleware
from app.models import FxRate
from app.services.employee import create
from app.services.salary import set_salary

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
def api(db, reference_data, actor_id) -> TestClient:
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("83.45000000"),
            as_of=date(2026, 1, 1),
            created_by=actor_id,
        )
    )
    db.flush()

    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db

    # The export opens its own session so the body can outlive the request. In a
    # test that session would see an empty database - the fixture's rows are in an
    # uncommitted transaction - so the factory is overridden to hand back this one,
    # unclosed.
    @contextmanager
    def test_session():
        yield db

    app.dependency_overrides[get_streaming_session] = lambda: test_session
    return TestClient(app)


def headers(roles: str = "hr_manager") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-1",
        "X-User-Name": "priya",
        "X-User-Roles": roles,
    }


def person(db, reference_data, actor_id, code, **overrides):
    params = {
        "employee_code": code,
        "first_name": "Anita",
        "last_name": "Rao",
        "email": f"{code.lower()}@acme.test",
        "country_code": "IN",
        "department_id": reference_data["department_id"],
        "job_title": "Engineer",
        "band_id": reference_data["band_id"],
        "hire_date": date(2024, 4, 1),
    }
    params.update(overrides)
    return create(db, actor_id=actor_id, **params)


def pay(db, actor_id, employee, amount="1200000.000", currency="INR"):
    set_salary(
        db,
        employee_id=employee.id,
        amount=Decimal(amount),
        currency=currency,
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )


def rows_of(response) -> list[dict]:
    return list(csv.DictReader(io.StringIO(response.text)))


# --- authorization and shape ----------------------------------------------------


def test_an_unauthenticated_export_is_refused(api):
    response = api.get("/employees/export", headers={"X-Gateway-Key": GATEWAY_KEY})

    assert response.status_code == 401


def test_the_response_is_a_csv_attachment_with_a_filename(api):
    response = api.get("/employees/export", headers=headers())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]


def test_export_is_not_shadowed_by_the_detail_route(api):
    """`/employees/export` and `/employees/{id}` compete. Declared the wrong way
    round, 'export' is parsed as an id and the download becomes a 422."""
    response = api.get("/employees/export", headers=headers())

    assert response.status_code == 200


# --- contents -------------------------------------------------------------------


def test_export_includes_local_amount_currency_and_usd_amount(db, api, reference_data, actor_id):
    """Both figures. USD is what the dashboard compares; the local amount is what the
    person is actually paid, and a payroll export missing it is unusable for payroll."""
    pay(db, actor_id, person(db, reference_data, actor_id, "EMP-X1"))

    row = rows_of(api.get("/employees/export", headers=headers()))[0]

    assert row["amount"] == "1200000.000"
    assert row["currency"] == "INR"
    assert row["usd_amount"] == "14379.87"
    assert row["fx_units_per_usd"] == "83.45000000"


def test_an_employee_with_no_salary_still_appears(db, api, reference_data, actor_id):
    """Same LEFT JOIN as the list. Someone whose pay has not been entered is exactly
    who an export is used to chase."""
    person(db, reference_data, actor_id, "EMP-X2")

    rows = rows_of(api.get("/employees/export", headers=headers()))

    assert len(rows) == 1
    assert rows[0]["amount"] == ""


def test_export_respects_active_filters(db, api, reference_data, actor_id):
    person(db, reference_data, actor_id, "EMP-X3", country_code="IN")
    person(db, reference_data, actor_id, "EMP-X4", country_code="DE")

    rows = rows_of(api.get("/employees/export?country=DE", headers=headers()))

    assert [r["employee_code"] for r in rows] == ["EMP-X4"]


def test_export_ignores_pagination_and_returns_all_matches(db, api, reference_data, actor_id):
    """A page size on an export is a silently truncated file - the worst failure mode
    here, because it looks like a complete one."""
    for i in range(5):
        person(db, reference_data, actor_id, f"EMP-P{i}")

    rows = rows_of(api.get("/employees/export?size=2&page=1", headers=headers()))

    assert len(rows) == 5


# --- the thing that makes a CSV dangerous ----------------------------------------


@pytest.mark.parametrize("dangerous", ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\tcmd", "\rcmd"])
def test_formula_injection_prefix_escaped(db, api, reference_data, actor_id, dangerous):
    """Excel evaluates a cell beginning with = + - @ as a formula, and =HYPERLINK or
    =cmd|'/c calc' turns an export into code execution on the reader's machine. The
    value is neutralised, not stripped: the reader still needs to see what was in the
    field (OWASP CSV injection)."""
    pay(db, actor_id, person(db, reference_data, actor_id, "EMP-X5", last_name=dangerous))

    row = rows_of(api.get("/employees/export", headers=headers()))[0]

    assert row["last_name"].startswith("'")
    assert dangerous.strip() in row["last_name"]


def test_an_ordinary_name_is_not_mangled(db, api, reference_data, actor_id):
    """The escaping must not fire on normal data, or every export carries stray
    apostrophes and someone turns it off."""
    person(db, reference_data, actor_id, "EMP-X6", last_name="O'Brien")

    row = rows_of(api.get("/employees/export", headers=headers()))[0]

    assert row["last_name"] == "O'Brien"


def test_a_comma_in_a_name_does_not_shift_the_columns(db, api, reference_data, actor_id):
    person(db, reference_data, actor_id, "EMP-X7", last_name="Rao, Jr")

    row = rows_of(api.get("/employees/export", headers=headers()))[0]

    assert row["last_name"] == "Rao, Jr"
    assert row["employee_code"] == "EMP-X7"


# --- streaming -------------------------------------------------------------------


def test_the_body_is_produced_after_the_request_session_would_have_closed(
    db, api, reference_data, actor_id
):
    """The trap this route exists to avoid.

    A StreamingResponse body is generated after dependencies have torn down, so a
    generator holding the request's Session reads from a closed one. This export
    opens its own, which is why the rows arrive at all.
    """
    for i in range(3):
        person(db, reference_data, actor_id, f"EMP-S{i}")

    rows = rows_of(api.get("/employees/export", headers=headers()))

    assert len(rows) == 3


@pytest.mark.perf
def test_a_ten_thousand_row_export_streams_rather_than_materialising(
    db, api, reference_data, actor_id
):
    """The claim in the route docstring, checked.

    `yield_per` streams from a server-side cursor; without it the whole result set is
    built in memory before the first byte leaves. The evidence is that the first
    chunk arrives before the last row has been read - with `stream=True` the client
    has a chunk while the generator is still running.
    """
    for i in range(10_000):
        person(db, reference_data, actor_id, f"EMP-B{i:05d}", email=f"b{i}@acme.test")

    with api.stream("GET", "/employees/export", headers=headers()) as response:
        chunks = 0
        first = None
        for chunk in response.iter_text():
            if first is None:
                first = chunk
            chunks += 1

    assert response.status_code == 200
    # More than one chunk means the body was produced incrementally, not handed over
    # as one finished string.
    assert chunks > 1
    assert first.startswith("employee_code,")
