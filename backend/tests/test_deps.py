"""The actor arrives before the handler body runs.

The point of resolving it as a dependency is that a write cannot be made without one
by forgetting a line. This checks the wiring, not the service - that is tested
separately, against the database.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_db
from app.deps import current_actor
from app.middleware import install_middleware
from app.models import AppUser

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
def api(db) -> TestClient:
    app = FastAPI()
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)

    @app.get("/who")
    def who(actor: AppUser = Depends(current_actor)) -> dict:
        return {"actor_id": actor.id, "external_id": actor.external_id, "email": actor.email}

    # The test's transaction, so the row is rolled back with everything else.
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def headers(sub: str = "kc-sub-route") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": sub,
        "X-User-Email": "priya@acme.test",
        "X-User-Name": "priya",
        "X-User-Roles": "hr_manager",
    }


def test_a_request_from_an_unknown_subject_provisions_the_actor(api, db):
    body = api.get("/who", headers=headers()).json()

    assert body["external_id"] == "kc-sub-route"
    assert body["email"] == "priya@acme.test"
    assert (
        db.execute(
            text("SELECT count(*) FROM app_user WHERE external_id = 'kc-sub-route'")
        ).scalar_one()
        == 1
    )


def test_a_second_request_reuses_the_same_actor(api):
    first = api.get("/who", headers=headers()).json()
    second = api.get("/who", headers=headers()).json()

    assert first["actor_id"] == second["actor_id"]


def test_no_actor_is_provisioned_for_a_request_that_never_gets_past_the_gate(api, db):
    """Provisioning on an unauthenticated request would let anyone who can reach the
    port fill the table with rows of their choosing."""
    api.get("/who", headers={**headers(), "X-Gateway-Key": "wrong"})

    assert db.execute(text("SELECT count(*) FROM app_user")).scalar_one() == 0
