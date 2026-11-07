"""Access control.

This service authenticates nobody. The BFF verified the token and forwarded the
claims, so everything here is about two questions: did this request come from the
BFF, and is the role it carries allowed.

Almost every test below is about something the service refuses.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import Principal, Role, current_principal, require_role
from app.errors import register_error_handlers
from app.middleware import install_middleware

GATEWAY_KEY = "test-gateway-key"


@pytest.fixture
def key() -> list[str]:
    """Mutable so a test can change the configured key mid-flight."""
    return [GATEWAY_KEY]


@pytest.fixture
def api(key) -> TestClient:
    """A small app wired exactly like the real one, with routes to aim at.

    The key is injected rather than set in the environment - that is what
    key_provider is for, and it keeps the test from depending on process state.
    """
    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: key[0])

    @app.get("/whoami")
    def whoami(principal: Principal = Depends(current_principal)) -> dict:
        return {
            "sub": principal.sub,
            "email": principal.email,
            "display_name": principal.display_name,
            "roles": sorted(principal.roles),
        }

    @app.get("/admin-only", dependencies=[Depends(require_role(Role.ADMIN))])
    def admin_only() -> dict:
        return {"ok": True}

    @app.get("/shared", dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))])
    def shared() -> dict:
        return {"ok": True}

    return TestClient(app)


def headers(roles: str = "hr_manager", **overrides: str) -> dict[str, str]:
    base = {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-1",
        "X-User-Email": "hr@acme.test",
        "X-User-Name": "priya",
        "X-User-Roles": roles,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# --- the caller must be the BFF ------------------------------------------------


def test_request_without_gateway_key_is_401(api):
    response = api.get("/whoami", headers=headers(**{"X-Gateway-Key": None}))
    assert response.status_code == 401


def test_request_with_wrong_gateway_key_is_401(api):
    response = api.get("/whoami", headers=headers(**{"X-Gateway-Key": "not-it"}))
    assert response.status_code == 401


def test_absent_and_wrong_keys_are_indistinguishable_to_the_caller(api):
    """Told apart, they are an oracle for probing the boundary. The log tells them
    apart; the response must not."""
    absent = api.get("/whoami", headers=headers(**{"X-Gateway-Key": None}))
    wrong = api.get("/whoami", headers=headers(**{"X-Gateway-Key": "not-it"}))

    assert absent.status_code == wrong.status_code
    assert absent.json()["detail"] == wrong.json()["detail"]


def test_the_log_does_tell_absent_from_wrong(api, caplog):
    """The operator needs the difference: nothing sent one is a misconfiguration,
    sent the wrong one is a stale deploy or a probe."""
    with caplog.at_level("WARNING", logger="salary.gateway"):
        api.get("/whoami", headers=headers(**{"X-Gateway-Key": None}))
        api.get("/whoami", headers=headers(**{"X-Gateway-Key": "not-it"}))

    reasons = [r.reason for r in caplog.records if r.name == "salary.gateway"]
    assert reasons == ["gateway_key_absent", "gateway_key_mismatch"]


def test_the_gateway_key_is_never_echoed_back(api):
    """A 401 that quotes the expected key would be a generous 401."""
    response = api.get("/whoami", headers=headers(**{"X-Gateway-Key": "not-it"}))
    assert GATEWAY_KEY not in response.text


def test_health_is_reachable_without_the_gateway_key(api):
    """The container's own healthcheck is not the BFF and has no key to send."""

    @api.app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    assert api.get("/health").status_code == 200


def test_an_unconfigured_gateway_key_refuses_every_request(api, key):
    """Fail closed. An empty key must not mean 'no check required'."""
    key[0] = ""
    assert api.get("/whoami", headers=headers()).status_code == 401


# --- identity comes from the forwarded claims, and nowhere else ----------------


def test_principal_is_built_from_forwarded_headers(api):
    body = api.get("/whoami", headers=headers()).json()
    assert body["sub"] == "kc-sub-1"
    assert body["email"] == "hr@acme.test"
    assert body["display_name"] == "priya"


def test_roles_header_is_split_and_normalised(api):
    body = api.get("/whoami", headers=headers(roles=" ADMIN , hr_manager ,,")).json()
    assert body["roles"] == ["admin", "hr_manager"]


def test_missing_user_sub_is_401_even_with_a_valid_gateway_key(api):
    """The gateway key says the BFF called. It does not say on whose behalf."""
    response = api.get("/whoami", headers=headers(**{"X-User-Sub": None}))
    assert response.status_code == 401


def test_authorization_header_alone_grants_nothing(api):
    """No fallback to a bearer token. A second way to be told who you are is a
    second trust path, and the weaker one always wins."""
    response = api.get("/whoami", headers={"Authorization": "Bearer anything.at.all"})
    assert response.status_code == 401


# --- authorization -------------------------------------------------------------


def test_hr_manager_blocked_from_admin_route_with_403(api):
    response = api.get("/admin-only", headers=headers(roles="hr_manager"))
    assert response.status_code == 403


def test_admin_allowed_on_admin_route(api):
    assert api.get("/admin-only", headers=headers(roles="admin")).status_code == 200


def test_hr_manager_allowed_on_shared_route(api):
    assert api.get("/shared", headers=headers(roles="hr_manager")).status_code == 200


def test_unknown_role_grants_nothing(api):
    """A role this application has never heard of is not a key to anything."""
    response = api.get("/shared", headers=headers(roles="payroll_admin,offline_access"))
    assert response.status_code == 403


def test_no_roles_at_all_is_403_not_500(api):
    response = api.get("/shared", headers=headers(roles=""))
    assert response.status_code == 403


def test_403_is_not_404(api):
    """Hiding the route's existence would also hide it from the person debugging it,
    and the caller is already authenticated - there is nothing to conceal."""
    response = api.get("/admin-only", headers=headers(roles="hr_manager"))
    assert response.status_code == 403
    assert "admin" in response.json()["detail"].lower()
