"""The identity provider has two addresses, and they are not interchangeable.

The browser reaches Keycloak at its public address; inside Docker the BFF reaches it
at a service name. Keycloak advertises only the public one, because that is what must
appear in tokens as ``iss``. Rewriting the back-channel URLs is what lets both be true
at once, and getting it wrong in either direction is a real failure: rewrite the
authorization URL and the browser is sent somewhere it cannot resolve; fail to rewrite
the token URL and the code exchange never leaves the container.
"""

import httpx
import pytest

from app.clients.keycloak import KeycloakClient
from app.config import Settings, get_settings

PUBLIC = "http://localhost:8080/realms/salary"
INTERNAL = "http://keycloak:8080/realms/salary"

DISCOVERY_DOC = {
    # Keycloak advertises its public hostname on every endpoint, whichever address
    # the discovery request arrived on.
    "authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth",
    "token_endpoint": f"{PUBLIC}/protocol/openid-connect/token",
    "end_session_endpoint": f"{PUBLIC}/protocol/openid-connect/logout",
}


@pytest.fixture
def internal_settings(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("OIDC_ISSUER", PUBLIC)
    monkeypatch.setenv("OIDC_INTERNAL_ISSUER", INTERNAL)
    yield
    get_settings.cache_clear()


def test_backchannel_issuer_defaults_to_the_public_one():
    # Outside Docker there is only one address, and the default must not require
    # every deployment to set a second setting it does not need.
    assert Settings(oidc_issuer=PUBLIC).backchannel_issuer == PUBLIC


def test_backchannel_issuer_overrides_when_set():
    settings = Settings(oidc_issuer=PUBLIC, oidc_internal_issuer=INTERNAL)
    assert settings.backchannel_issuer == INTERNAL


@pytest.mark.asyncio
async def test_discovery_is_fetched_over_the_internal_address(internal_settings):
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=DISCOVERY_DOC)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        await KeycloakClient(http).endpoints()

    assert seen == [f"{INTERNAL}/.well-known/openid-configuration"]


@pytest.mark.asyncio
async def test_token_and_logout_are_rewritten_but_authorization_is_not(internal_settings):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=DISCOVERY_DOC)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        endpoints = await KeycloakClient(http).endpoints()

    # The browser follows this one, so it keeps the address the browser can resolve.
    assert endpoints.authorization.startswith(PUBLIC)
    # These two are called by this process, from inside the network.
    assert endpoints.token == f"{INTERNAL}/protocol/openid-connect/token"
    assert endpoints.end_session == f"{INTERNAL}/protocol/openid-connect/logout"


def test_jwks_is_fetched_over_the_internal_address(internal_settings):
    from app.security.token import TokenVerifier

    assert TokenVerifier(http=None)._jwks_uri == f"{INTERNAL}/protocol/openid-connect/certs"


def test_tokens_are_still_validated_against_the_public_issuer(internal_settings):
    # The rewrite is a routing concern. The issuer a token must claim is the public
    # one, because that is what Keycloak signs into it.
    assert get_settings().oidc_issuer == PUBLIC
