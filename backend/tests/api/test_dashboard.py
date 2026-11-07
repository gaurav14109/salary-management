"""The dashboard endpoint.

Aggregate payroll for the whole company is as disclosive as any individual salary,
and easier to carry away - so the authorization tests here matter as much as the
ones on the employee endpoints.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dashboard import router
from app.db import get_db
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

    person = create(
        db,
        employee_code="EMP-D1",
        first_name="Anita",
        last_name="Rao",
        email="anita@acme.test",
        country_code="IN",
        department_id=reference_data["department_id"],
        job_title="Engineer",
        band_id=reference_data["band_id"],
        hire_date=date(2024, 4, 1),
        actor_id=actor_id,
    )
    set_salary(
        db,
        employee_id=person.id,
        amount=Decimal("835000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )

    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def headers(roles: str = "hr_manager") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-1",
        "X-User-Name": "priya",
        "X-User-Roles": roles,
    }


def test_an_unauthenticated_request_gets_no_payroll_total(api):
    assert api.get("/dashboard", headers={"X-Gateway-Key": GATEWAY_KEY}).status_code == 401


def test_a_caller_with_no_recognised_role_is_refused(api):
    """A valid Keycloak user who is not HR is not entitled to company payroll."""
    assert api.get("/dashboard", headers=headers("offline_access")).status_code == 403


def test_both_roles_can_read_it(api):
    assert api.get("/dashboard", headers=headers("hr_manager")).status_code == 200
    assert api.get("/dashboard", headers=headers("admin")).status_code == 200


def test_the_response_carries_every_section_the_page_renders(api):
    body = api.get("/dashboard", headers=headers()).json()

    assert set(body) == {
        "employee_count",
        "total_payroll_usd",
        "average_salary_usd",
        "median_salary_usd",
        "highest",
        "lowest",
        "by_country",
        "by_department",
        "by_band",
        "currency_breakdown",
    }


def test_money_is_serialised_as_strings(api):
    """A payroll total through an IEEE 754 double is a total that disagrees with the
    ledger."""
    raw = api.get("/dashboard", headers=headers()).text.replace(" ", "")

    assert '"total_payroll_usd":"10005.99"' in raw


def test_bands_arrive_ordered_by_rank(api):
    bands = api.get("/dashboard", headers=headers()).json()["by_band"]

    assert [b["rank"] for b in bands] == sorted(b["rank"] for b in bands)


def test_a_filter_narrows_every_figure(api):
    body = api.get("/dashboard?country=DE", headers=headers()).json()

    assert body["employee_count"] == 0
    assert body["highest"] is None
    assert body["by_country"] == []


def test_a_malformed_country_filter_is_422(api):
    assert api.get("/dashboard?country=INDIA", headers=headers()).status_code == 422
