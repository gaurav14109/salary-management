import urllib.parse

from app.config import get_settings


def test_me_returns_401_and_where_to_log_in(client):
    """What lets the SPA hold no OIDC knowledge: no client id, no PKCE, no URL to build."""
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["authenticated"] is False
    assert response.json()["login_url"].endswith("/auth/login")


def test_me_reports_the_logged_in_user(authenticated):
    body = authenticated.get("/auth/me").json()

    assert body == {
        "authenticated": True,
        "username": "hr",
        "email": "hr@acme.test",
        "roles": ["hr_manager"],
    }


def test_login_redirects_to_the_provider_with_pkce(client):
    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 307
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(response.headers["location"]).query))
    assert query["client_id"] == "salary-bff"
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] and query["state"]


def test_the_pkce_verifier_never_reaches_the_browser_in_the_clear(client):
    """Only the challenge goes to the provider; the verifier stays encrypted, so
    intercepting the redirect does not let anyone else redeem the code."""
    response = client.get("/auth/login", follow_redirects=False)

    challenge = dict(
        urllib.parse.parse_qsl(urllib.parse.urlparse(response.headers["location"]).query)
    )["code_challenge"]
    assert challenge not in response.cookies["salary_login_txn"]


def test_an_open_redirect_is_not_honoured_after_login(client, fake_keycloak):
    """The end-to-end version of the unit test: a hostile return_to must not survive
    the round trip through the provider."""
    client.get("/auth/login?return_to=https://evil.test/steal", follow_redirects=False)
    state = dict(
        urllib.parse.parse_qsl(
            urllib.parse.urlparse(
                client.get(
                    "/auth/login?return_to=https://evil.test/steal", follow_redirects=False
                ).headers["location"]
            ).query
        )
    )["state"]

    response = client.get(f"/auth/callback?code=the-code&state={state}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(get_settings().spa_base_url)
    assert "evil.test" not in response.headers["location"]


def test_a_callback_with_a_forged_state_is_rejected(client):
    """Without this, any site could complete a login flow on the user's behalf."""
    response = client.get("/auth/callback?code=abc&state=attacker-chosen")

    assert response.status_code == 400
    assert "request_id" in response.json()


def test_a_callback_without_a_transaction_cookie_is_rejected(client):
    assert client.get("/auth/callback?code=abc&state=xyz").status_code == 400


def test_a_successful_login_sets_both_cookies(client, fake_keycloak):
    location = client.get("/auth/login", follow_redirects=False).headers["location"]
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))["state"]

    response = client.get(f"/auth/callback?code=the-code&state={state}", follow_redirects=False)

    assert response.status_code == 307
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(get_settings().session_cookie_name))
    csrf_cookie = next(c for c in cookies if c.startswith("salary_csrf"))

    assert "HttpOnly" in session_cookie  # JavaScript must not read the session
    assert "HttpOnly" not in csrf_cookie  # the SPA must read this one
    assert "samesite=lax" in session_cookie.lower()


def test_logout_ends_the_provider_session_too(authenticated, fake_keycloak):
    """Clearing our cookie alone leaves the user silently logged back in by the next
    /auth/login."""
    response = authenticated.post("/auth/logout")

    assert response.status_code == 200
    assert fake_keycloak.ended == ["refresh-token"]


def test_logout_still_clears_our_cookie_when_the_provider_fails(authenticated, fake_keycloak):
    async def boom(_):
        raise RuntimeError("provider down")

    fake_keycloak.end_session = boom

    response = authenticated.post("/auth/logout")

    assert response.status_code == 200
    assert any(
        "salary_session=" in c and "Max-Age=0" in c for c in response.headers.get_list("set-cookie")
    )


def test_login_is_rate_limited(client):
    limit = get_settings().login_rate_limit_per_minute

    statuses = [
        client.get("/auth/login", follow_redirects=False).status_code for _ in range(limit + 3)
    ]

    assert statuses.count(429) >= 1
