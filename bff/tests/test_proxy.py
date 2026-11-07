"""Forwarding rules.

The proxy must be boring. Almost every test here is about something it refuses to do,
or something it must never forget to do.
"""

import time

import httpx
import respx

from app.config import get_settings
from app.security.csrf import CSRF_HEADER
from app.security.session import encode

API = "http://localhost:8000"


def test_an_unauthenticated_request_never_reaches_the_upstream(client):
    with respx.mock:
        route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))
        response = client.get("/api/employees")

    assert response.status_code == 401
    assert response.json()["login_url"].endswith("/auth/login")
    assert not route.called


@respx.mock
def test_an_authenticated_read_is_forwarded_with_a_bearer_token(authenticated, session):
    route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200, json={"items": []}))

    response = authenticated.get("/api/employees")

    assert response.status_code == 200
    assert route.calls.last.request.headers["authorization"] == f"Bearer {session.access_token}"


@respx.mock
def test_the_session_cookie_is_not_forwarded_upstream(authenticated, session):
    """A service that can read the session cookie can mint requests as that user."""
    route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))

    authenticated.get("/api/employees")

    assert "cookie" not in {k.lower() for k in route.calls.last.request.headers}


@respx.mock
def test_client_supplied_headers_are_not_passed_through(authenticated):
    """Allowlist, not denylist. An upstream that trusts X-Forwarded-For or X-User-Id
    would be trusting the client if these were forwarded."""
    route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))

    authenticated.get(
        "/api/employees",
        headers={
            "X-Forwarded-For": "1.2.3.4",
            "X-User-Id": "99",
            "X-Real-IP": "1.2.3.4",
            "Authorization": "Bearer attacker-supplied",
        },
    )

    forwarded = {k.lower() for k in route.calls.last.request.headers}
    assert "x-forwarded-for" not in forwarded
    assert "x-user-id" not in forwarded
    assert "x-real-ip" not in forwarded
    # The client's Authorization was discarded and replaced by the session's.
    assert route.calls.last.request.headers["authorization"] != "Bearer attacker-supplied"


@respx.mock
def test_an_upstream_set_cookie_is_not_relayed_to_the_browser(authenticated):
    """An upstream has no business setting cookies on a browser it cannot see."""
    respx.get(f"{API}/employees").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "upstream=1"})
    )

    response = authenticated.get("/api/employees")

    assert "upstream=1" not in str(response.headers.get_list("set-cookie"))


@respx.mock
def test_query_parameters_survive_the_hop(authenticated):
    route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))

    authenticated.get("/api/employees?country=IN&page=3")

    assert dict(route.calls.last.request.url.params) == {"country": "IN", "page": "3"}


@respx.mock
def test_an_upstream_error_status_passes_through_unchanged(authenticated):
    """A 403 from the API must stay a 403. Flattening it to 500 hides the one thing
    the user needs to know."""
    respx.get(f"{API}/fx-rates").mock(
        return_value=httpx.Response(403, json={"detail": "Forbidden"})
    )

    response = authenticated.get("/api/fx-rates")

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_a_traversal_path_is_refused_before_any_request(authenticated):
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200))
        response = authenticated.get("/api/employees/../../admin")

    assert response.status_code in (400, 404)
    assert not route.called


# ── upstream failure is not our failure ─────────────────────────────────────────


@respx.mock
def test_an_unreachable_upstream_is_502_not_500(authenticated):
    """502 says the problem is downstream. 500 says this service is broken, and sends
    whoever is on call to the wrong place."""
    respx.get(f"{API}/employees").mock(side_effect=httpx.ConnectError("refused"))

    response = authenticated.get("/api/employees")

    assert response.status_code == 502
    assert "request_id" in response.json()


@respx.mock
def test_a_slow_upstream_is_504(authenticated):
    respx.get(f"{API}/employees").mock(side_effect=httpx.ReadTimeout("slow"))

    assert authenticated.get("/api/employees").status_code == 504


@respx.mock
def test_an_upstream_failure_does_not_leak_its_address(authenticated):
    """The hostname and the library's message belong in the log, not in a response."""
    respx.get(f"{API}/employees").mock(side_effect=httpx.ConnectError("Connection refused"))

    body = authenticated.get("/api/employees").text

    assert "localhost:8000" not in body
    assert "ConnectError" not in body
    assert "Traceback" not in body


# ── CSRF: the cost of moving from a bearer header to a cookie ───────────────────


@respx.mock
def test_a_write_without_a_csrf_token_never_reaches_the_upstream(authenticated):
    """Browsers attach cookies to cross-site requests automatically. Without this,
    any page on the internet could make the user's browser change a salary."""
    route = respx.put(f"{API}/employees/42/salary").mock(return_value=httpx.Response(200))

    response = authenticated.put("/api/employees/42/salary", json={"amount": 1})

    assert response.status_code == 403
    assert not route.called


@respx.mock
def test_a_write_with_the_wrong_csrf_token_is_refused(authenticated):
    route = respx.put(f"{API}/employees/42/salary").mock(return_value=httpx.Response(200))

    response = authenticated.put(
        "/api/employees/42/salary", json={"amount": 1}, headers={CSRF_HEADER: "wrong"}
    )

    assert response.status_code == 403
    assert not route.called


@respx.mock
def test_a_write_with_the_matching_csrf_token_is_forwarded(authenticated, session):
    route = respx.put(f"{API}/employees/42/salary").mock(return_value=httpx.Response(200))

    response = authenticated.put(
        "/api/employees/42/salary",
        json={"amount": 1},
        headers={CSRF_HEADER: session.csrf_token},
    )

    assert response.status_code == 200
    assert route.called


@respx.mock
def test_reads_do_not_require_a_csrf_token(authenticated):
    respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))

    assert authenticated.get("/api/employees").status_code == 200


# ── token refresh ───────────────────────────────────────────────────────────────


@respx.mock
def test_an_expired_token_is_refreshed_before_forwarding(client, session, fake_keycloak):
    """The SPA never sees this happen, which is the point: token lifecycle belongs to
    this layer, not the browser."""
    route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))
    session.expires_at = time.time() - 1
    client.cookies.set(get_settings().session_cookie_name, encode(session))

    response = client.get("/api/employees")

    assert response.status_code == 200
    forwarded = route.calls.last.request.headers["authorization"]
    assert forwarded == f"Bearer {fake_keycloak.next_tokens['access_token']}"
    assert forwarded != f"Bearer {session.access_token}"


@respx.mock
def test_a_failed_refresh_ends_the_session(client, session, fake_keycloak):
    """A refresh token revoked in the provider must log the user out here, not loop."""
    fake_keycloak.refresh_fails = True
    session.expires_at = time.time() - 1
    client.cookies.set(get_settings().session_cookie_name, encode(session))

    response = client.get("/api/employees")

    assert response.status_code == 401
    assert response.json()["login_url"].endswith("/auth/login")


# ── limits ──────────────────────────────────────────────────────────────────────


@respx.mock
def test_an_oversized_body_is_refused(authenticated, session, monkeypatch):
    """An unbounded proxy body is a memory-exhaustion primitive."""
    from app.config import get_settings as real_settings

    real_settings.cache_clear()
    monkeypatch.setenv("MAX_BODY_BYTES", "100")
    route = respx.post(f"{API}/imports").mock(return_value=httpx.Response(200))

    response = authenticated.post(
        "/api/imports",
        content=b"x" * 5000,
        headers={CSRF_HEADER: session.csrf_token, "Content-Type": "application/octet-stream"},
    )

    real_settings.cache_clear()
    assert response.status_code == 413
    assert not route.called
