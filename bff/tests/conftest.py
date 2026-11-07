import secrets
import time

import pytest
from fastapi.testclient import TestClient

from app.clients.keycloak import Endpoints
from app.config import get_settings
from app.main import app
from app.security import rate_limit
from app.security.session import Session, encode
from app.security.token import TokenVerifier
from tests.keys import PUBLIC_KEY, make_token

DISCOVERY = Endpoints(
    authorization="http://kc/realms/salary/protocol/openid-connect/auth",
    token="http://kc/realms/salary/protocol/openid-connect/token",
    end_session="http://kc/realms/salary/protocol/openid-connect/logout",
)


class FakeKeycloak:
    """A stand-in for the identity provider.

    Possible because the service depends on the TokenIssuer protocol rather than on
    Keycloak: no import patching, and the fake can be made to fail on demand.
    """

    def __init__(self) -> None:
        self.exchanged: list[tuple[str, str]] = []
        self.ended: list[str] = []
        self.refresh_fails = False
        self.next_tokens = {
            "access_token": make_token(username="hr-refreshed"),
            "refresh_token": "fresh-refresh-token",
            "expires_in": 300,
        }

    async def endpoints(self) -> Endpoints:
        return DISCOVERY

    async def exchange_code(self, *, code: str, verifier: str) -> dict:
        self.exchanged.append((code, verifier))
        return {
            "access_token": make_token(),
            "refresh_token": "refresh-token",
            "expires_in": 300,
        }

    async def refresh(self, refresh_token: str) -> dict:
        if self.refresh_fails:
            raise RuntimeError("refresh rejected")
        return self.next_tokens

    async def end_session(self, refresh_token: str) -> None:
        self.ended.append(refresh_token)


class FixtureKeyVerifier(TokenVerifier):
    """The real verifier, reading its key from the test fixture instead of JWKS.

    jwt.decode still checks signature, issuer, audience and expiry, so these tests
    exercise the verification logic rather than a stub of it.
    """

    def _signing_key(self, token: str):
        return PUBLIC_KEY


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture
def fake_keycloak() -> FakeKeycloak:
    return FakeKeycloak()


@pytest.fixture
def token_verifier() -> FixtureKeyVerifier:
    return FixtureKeyVerifier(http=None)


@pytest.fixture
def client(fake_keycloak, token_verifier) -> TestClient:
    # raise_server_exceptions=False so an unhandled error produces the response a real
    # client would receive, instead of propagating into the test. The point of the
    # handler is the response, so that is what gets asserted.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        app.state.keycloak = fake_keycloak
        app.state.verifier = token_verifier
        yield test_client


@pytest.fixture
def session() -> Session:
    return Session(
        access_token=make_token(),
        refresh_token="refresh-token",
        expires_at=time.time() + 300,
        sub="kc-sub-123",
        username="hr",
        email="hr@acme.test",
        roles=["hr_manager"],
        csrf_token=secrets.token_urlsafe(32),
    )


@pytest.fixture
def authenticated(client, session) -> TestClient:
    client.cookies.set(get_settings().session_cookie_name, encode(session))
    return client
