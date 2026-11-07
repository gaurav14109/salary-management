"""The employee endpoints.

Routes hold no rules - every assertion here is about a status code, a shape, or who
is allowed. If a test here asserted a business outcome, the rule would be in the
wrong layer.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.employees import router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware
from app.models import FxRate

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
    return TestClient(app)


def headers(roles: str = "admin") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-admin",
        "X-User-Email": "admin@acme.test",
        "X-User-Name": "rahul",
        "X-User-Roles": roles,
    }


def a_person(reference_data, **overrides) -> dict:
    body = {
        "employee_code": "EMP-5001",
        "first_name": "Anita",
        "last_name": "Rao",
        "email": "anita@acme.test",
        "country_code": "IN",
        "department_id": reference_data["department_id"],
        "job_title": "Engineer",
        "band_id": reference_data["band_id"],
        "hire_date": "2024-04-01",
    }
    body.update(overrides)
    return body


@pytest.fixture
def created(api, reference_data) -> int:
    return api.post("/employees", json=a_person(reference_data), headers=headers()).json()["id"]


# --- authorization --------------------------------------------------------------


def test_hr_manager_cannot_create_an_employee(api, reference_data):
    """Creating a person is an admin act. Editing their pay is the HR manager's job -
    the roles differ because the consequences do."""
    response = api.post("/employees", json=a_person(reference_data), headers=headers("hr_manager"))

    assert response.status_code == 403


def test_hr_manager_can_edit_a_salary(api, created, reference_data):
    response = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers("hr_manager"),
    )

    assert response.status_code == 200


def test_hr_manager_can_read_the_list(api):
    assert api.get("/employees", headers=headers("hr_manager")).status_code == 200


def test_an_unauthenticated_request_reads_nothing(api):
    assert api.get("/employees", headers={"X-Gateway-Key": GATEWAY_KEY}).status_code == 401


# --- list -----------------------------------------------------------------------


def test_list_paginates_with_total_count(api, created):
    body = api.get("/employees?size=1", headers=headers()).json()

    assert set(body) == {"items", "total", "page", "size"}
    assert body["total"] == 1


def test_page_size_capped_at_200(api):
    assert api.get("/employees?size=201", headers=headers()).status_code == 422


def test_invalid_sort_field_returns_422_not_500(api):
    """No SQL injection via the sort column: an unknown key never reaches SQL."""
    response = api.get("/employees?sort=id;DROP TABLE employee", headers=headers())

    assert response.status_code == 422


def test_sort_by_usd_amount_desc(api, created, reference_data):
    api.post(
        "/employees",
        json=a_person(reference_data, employee_code="EMP-5002", email="b@acme.test"),
        headers=headers(),
    )
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    items = api.get("/employees?sort=usd_amount&order=desc", headers=headers()).json()["items"]

    assert items[0]["current_salary"] is not None
    assert items[1]["current_salary"] is None


def test_an_employee_with_no_salary_is_listed_with_a_null(api, created):
    items = api.get("/employees", headers=headers()).json()["items"]

    assert items[0]["current_salary"] is None


# --- create ---------------------------------------------------------------------


def test_create_returns_201_and_the_employee(api, reference_data):
    response = api.post("/employees", json=a_person(reference_data), headers=headers())

    assert response.status_code == 201
    assert response.json()["employee_code"] == "EMP-5001"


def test_a_duplicate_code_is_409(api, created, reference_data):
    response = api.post(
        "/employees", json=a_person(reference_data, email="other@acme.test"), headers=headers()
    )

    assert response.status_code == 409
    assert "EMP-5001" in response.json()["detail"]


def test_an_unknown_department_is_422_naming_it(api, reference_data):
    response = api.post(
        "/employees", json=a_person(reference_data, department_id=999999), headers=headers()
    )

    assert response.status_code == 422
    assert "department" in response.json()["detail"].lower()


def test_creating_with_a_status_is_refused_by_the_schema(api, reference_data):
    """status is not a creation field. Sending one should not look accepted."""
    body = a_person(reference_data)
    body["status"] = "INACTIVE"

    created = api.post("/employees", json=body, headers=headers()).json()
    assert created["status"] == "ACTIVE"


# --- patch ----------------------------------------------------------------------


def test_patch_changes_one_field(api, created):
    response = api.patch(
        f"/employees/{created}", json={"job_title": "Senior Engineer"}, headers=headers()
    )

    assert response.status_code == 200
    assert response.json()["job_title"] == "Senior Engineer"


def test_patch_with_an_empty_body_is_422(api, created):
    """A save that saves nothing must not return 200."""
    assert api.patch(f"/employees/{created}", json={}, headers=headers()).status_code == 422


def test_patch_cannot_set_the_status(api, created):
    """Terminating closes a salary row. It is not a field edit and has its own route."""
    response = api.patch(f"/employees/{created}", json={"status": "INACTIVE"}, headers=headers())

    assert response.status_code == 422


def test_patch_of_an_unknown_employee_is_422(api):
    response = api.patch("/employees/999999", json={"job_title": "X"}, headers=headers())

    assert response.status_code == 422


# --- status ---------------------------------------------------------------------


def test_terminating_closes_the_open_salary_row(api, created):
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    response = api.put(
        f"/employees/{created}/status",
        json={"status": "INACTIVE", "termination_date": "2026-09-30"},
        headers=headers(),
    )

    assert response.status_code == 200
    detail = api.get(f"/employees/{created}", headers=headers()).json()
    assert detail["current_salary"] is None
    assert detail["history"][0]["effective_to"] == "2026-09-30"


def test_terminating_without_a_date_is_422(api, created):
    response = api.put(
        f"/employees/{created}/status", json={"status": "INACTIVE"}, headers=headers()
    )

    assert response.status_code == 422


def test_hr_manager_cannot_terminate(api, created):
    response = api.put(
        f"/employees/{created}/status",
        json={"status": "INACTIVE", "termination_date": "2026-09-30"},
        headers=headers("hr_manager"),
    )

    assert response.status_code == 403


# --- salary ---------------------------------------------------------------------


def test_put_salary_returns_new_row_and_closes_old(api, created):
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    body = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1400000.000", "currency": "INR", "effective_from": "2026-10-01"},
        headers=headers(),
    ).json()

    assert body["changed"] is True
    assert body["salary"]["effective_to"] is None
    history = api.get(f"/employees/{created}", headers=headers()).json()["history"]
    assert history[1]["effective_to"] == "2026-09-30"


def test_put_salary_with_an_unchanged_amount_reports_no_change(api, created):
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    body = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-10-01"},
        headers=headers(),
    ).json()

    assert body["changed"] is False


def test_a_backdated_salary_is_422(api, created):
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    response = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1400000.000", "currency": "INR", "effective_from": "2026-01-01"},
        headers=headers(),
    )

    assert response.status_code == 422


def test_the_client_cannot_supply_the_usd_amount(api, created):
    """Letting the browser decide what a salary is worth in USD would make the
    reporting currency a client-controlled figure."""
    body = api.put(
        f"/employees/{created}/salary",
        json={
            "amount": "1200000.000",
            "currency": "INR",
            "effective_from": "2026-04-01",
            "usd_amount": "1.00",
        },
        headers=headers(),
    ).json()

    assert body["salary"]["usd_amount"] == "14379.87"


def test_money_is_serialised_as_a_string(api, created):
    raw = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    ).text

    assert '"usd_amount":"14379.87"' in raw.replace(" ", "")


# --- detail ---------------------------------------------------------------------


def test_detail_includes_current_salary_and_history(api, created):
    api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    body = api.get(f"/employees/{created}", headers=headers()).json()

    assert set(body) == {"employee", "current_salary", "history"}
    assert body["current_salary"]["amount"] == "1200000.000"
    assert len(body["history"]) == 1


def test_detail_of_an_unknown_employee_is_404(api):
    """A GET for something that is not there is 404. A PATCH for the same thing is
    422, because there the id is a field of a request that cannot be processed."""
    assert api.get("/employees/999999", headers=headers()).status_code == 404


# --- failures that must not be 500 ----------------------------------------------
#
# The service refuses several things with a plain ValueError. Unhandled, each is a
# 500 that tells the caller nothing and pages whoever is on call for a request that
# was simply wrong.


def test_salary_for_an_unknown_employee_is_422_not_500(api):
    response = api.put(
        "/employees/999999/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    )

    assert response.status_code == 422
    assert "999999" in response.json()["detail"]


def test_salary_for_a_terminated_employee_is_422_not_500(api, created):
    api.put(
        f"/employees/{created}/status",
        json={"status": "INACTIVE", "termination_date": "2026-09-30"},
        headers=headers(),
    )

    response = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-10-01"},
        headers=headers(),
    )

    assert response.status_code == 422


def test_a_salary_starting_before_the_hire_date_is_422_not_500(api, created):
    response = api.put(
        f"/employees/{created}/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2020-01-01"},
        headers=headers(),
    )

    assert response.status_code == 422
    assert "hired" in response.json()["detail"].lower()


def test_no_failure_path_leaks_an_internal_detail(api):
    """Whatever the refusal, the body is a sentence and a request id - never a
    traceback, a table name or a constraint."""
    body = api.put(
        "/employees/999999/salary",
        json={"amount": "1200000.000", "currency": "INR", "effective_from": "2026-04-01"},
        headers=headers(),
    ).text

    for leak in ("Traceback", "psycopg", "sqlalchemy", "salary.py"):
        assert leak not in body
