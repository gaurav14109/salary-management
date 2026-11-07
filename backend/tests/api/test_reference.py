"""Reference data.

One endpoint rather than four, because the edit form needs all of it at once and
four round trips is four chances for one to fail.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reference import router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
def api(db) -> TestClient:
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


def test_reference_returns_every_list_the_edit_form_needs(api):
    body = api.get("/reference", headers=headers()).json()

    assert set(body) == {"countries", "currencies", "departments", "bands"}
    assert body["countries"] and body["currencies"] and body["departments"] and body["bands"]


def test_bands_carry_their_rank_and_arrive_in_it(api):
    """Sorted by name they read L1, L10, L11, L2. The UI needs rank to order them,
    and gets them already ordered so it does not have to."""
    bands = api.get("/reference", headers=headers()).json()["bands"]

    assert [b["rank"] for b in bands] == sorted(b["rank"] for b in bands)
    assert all("rank" in b for b in bands)


def test_currencies_carry_their_minor_units(api):
    """The form needs to know KWD takes three decimal places and JPY takes none."""
    currencies = {
        c["code"]: c["minor_units"]
        for c in api.get("/reference", headers=headers()).json()["currencies"]
    }

    assert currencies["KWD"] == 3
    assert currencies["JPY"] == 0
    assert currencies["USD"] == 2


def test_an_unauthenticated_request_gets_nothing(api):
    """Department and band names are company structure. Not secret, not public."""
    assert api.get("/reference", headers={"X-Gateway-Key": GATEWAY_KEY}).status_code == 401


def test_retired_departments_are_not_offered(api, db):
    """A retired department stays readable in historical reports but must not appear
    in a dropdown for new records."""
    from sqlalchemy import text

    db.execute(text("UPDATE department SET is_active = false WHERE name = 'Legal'"))
    db.flush()

    names = {d["name"] for d in api.get("/reference", headers=headers()).json()["departments"]}
    assert "Legal" not in names
