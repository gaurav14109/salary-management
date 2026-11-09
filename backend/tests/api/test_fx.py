"""The FX rate endpoints.

Reference data the HR manager maintains. An admin reads salaries converted by these
rates and must not be able to change the rates themselves - that would be a way to
move every reported figure without touching a single salary, from a role that is
meant only to look.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.fx import router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware
from app.models import FxRate

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
def api(db, reference_data) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def headers(roles: str = "hr_manager") -> dict[str, str]:
    """The default caller writes, and writing belongs to the HR manager."""
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-hr",
        "X-User-Email": "hr@acme.test",
        "X-User-Name": "rahul",
        "X-User-Roles": roles,
    }


def a_rate(**overrides) -> dict:
    return {"currency": "INR", "units_per_usd": "83.45", "as_of": "2026-03-01", **overrides}


# --- authorization --------------------------------------------------------------


def test_an_admin_cannot_post_fx_rate(api, db):
    response = api.post("/fx-rates", json=a_rate(), headers=headers(roles="admin"))

    assert response.status_code == 403
    # Nothing was written. Counted for this currency and date rather than globally,
    # because migration 0002 seeds one indicative rate per currency.
    assert db.query(FxRate).filter_by(currency="INR", as_of=date(2026, 3, 1)).count() == 0


def test_hr_manager_can_post_fx_rate(api):
    assert api.post("/fx-rates", json=a_rate(), headers=headers()).status_code == 201


def test_an_admin_can_read_fx_rates(api):
    """Reading them explains a USD figure on screen. Writing them changes it."""
    assert api.get("/fx-rates", headers=headers(roles="admin")).status_code == 200


def test_an_unauthenticated_request_cannot_read_rates(api):
    assert api.get("/fx-rates", headers={"X-Gateway-Key": GATEWAY_KEY}).status_code == 401


# --- writing --------------------------------------------------------------------


def test_a_posted_rate_is_stored_with_its_author(api, db):
    api.post("/fx-rates", json=a_rate(), headers=headers())

    stored = db.query(FxRate).filter_by(currency="INR", as_of=date(2026, 3, 1)).one()
    assert stored.units_per_usd == Decimal("83.45000000")
    assert stored.as_of == date(2026, 3, 1)
    assert stored.created_by is not None


def test_a_duplicate_rate_for_one_currency_and_date_is_409_not_500(api):
    """The unique index decides it. The caller gets a status that says retrying will
    not help, rather than a generic failure."""
    api.post("/fx-rates", json=a_rate(), headers=headers())

    response = api.post("/fx-rates", json=a_rate(units_per_usd="83.50"), headers=headers())

    assert response.status_code == 409
    assert "INR" in response.json()["detail"]


def test_an_unknown_currency_is_422_naming_the_field(api):
    """A currency with no row in the reference table is a foreign key violation. It
    must read as "that currency does not exist", not as a database error."""
    response = api.post("/fx-rates", json=a_rate(currency="XYZ"), headers=headers())

    assert response.status_code == 422
    assert "XYZ" in response.json()["detail"]


def test_a_non_positive_rate_is_rejected_before_it_reaches_the_database(api):
    response = api.post("/fx-rates", json=a_rate(units_per_usd="0"), headers=headers())

    assert response.status_code == 422
    assert response.json()["fields"][0]["field"] == "units_per_usd"


def test_a_malformed_currency_code_is_rejected(api):
    assert (
        api.post("/fx-rates", json=a_rate(currency="rupee"), headers=headers()).status_code == 422
    )


def test_the_currency_is_stored_uppercase_whatever_was_sent(api, db):
    api.post("/fx-rates", json=a_rate(currency="inr"), headers=headers())

    assert db.query(FxRate).filter_by(as_of=date(2026, 3, 1)).one().currency == "INR"


# --- reading --------------------------------------------------------------------


def test_rates_are_returned_newest_first(api):
    for as_of in ("2026-01-01", "2026-06-01", "2026-03-01"):
        api.post("/fx-rates", json=a_rate(as_of=as_of), headers=headers())

    items = api.get("/fx-rates", headers=headers()).json()["items"]

    # The seeded rates are all dated 2020-01-01, so the three posted here sort above
    # them - which is itself the ordering being asserted.
    assert [item["as_of"] for item in items[:3]] == ["2026-06-01", "2026-03-01", "2026-01-01"]


def test_rates_can_be_filtered_by_currency(api):
    api.post("/fx-rates", json=a_rate(), headers=headers())
    api.post("/fx-rates", json=a_rate(currency="EUR"), headers=headers())

    body = api.get("/fx-rates?currency=EUR", headers=headers()).json()

    # EUR, and only EUR: the posted rate plus the one migration 0002 seeded.
    assert {item["currency"] for item in body["items"]} == {"EUR"}
    assert body["total"] == 2


def test_the_list_is_a_page_not_an_unbounded_collection(api):
    body = api.get("/fx-rates", headers=headers()).json()

    assert set(body) == {"items", "total", "page", "size"}


def test_a_page_size_beyond_the_maximum_is_refused(api):
    """No endpoint returns 10,000 rows because someone asked it to."""
    assert api.get("/fx-rates?size=5000", headers=headers()).status_code == 422


def test_the_amount_is_serialised_as_a_string_not_a_float(api):
    """JSON numbers are IEEE 754 doubles. A rate that round-trips through one is no
    longer the rate that was stored."""
    api.post("/fx-rates", json=a_rate(), headers=headers())

    raw = api.get("/fx-rates", headers=headers()).text
    assert '"units_per_usd":"83.45000000"' in raw.replace(" ", "")
