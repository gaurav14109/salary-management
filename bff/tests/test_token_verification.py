"""The BFF is the authentication boundary, so every way a token can be invalid gets
a test. These are the checks the previous version skipped entirely.
"""

import time

import httpx
import pytest
import respx

from app.config import get_settings
from app.errors import UpstreamUnavailable
from app.security.session import encode
from app.security.token import InvalidToken
from tests.keys import OTHER_PRIVATE_KEY, make_token

API = "http://localhost:8000"


def test_a_well_formed_token_verifies(token_verifier):
    claims = token_verifier.verify(make_token())

    assert claims["sub"] == "kc-sub-123"
    assert claims["realm_access"]["roles"] == ["hr_manager"]


def test_a_token_signed_by_an_unknown_key_is_refused(token_verifier):
    """The check that makes all the others meaningful: without it, anyone can write
    any claims they like."""
    forged = make_token(key=OTHER_PRIVATE_KEY)

    with pytest.raises(InvalidToken):
        token_verifier.verify(forged)


def test_a_token_with_an_altered_payload_is_refused(token_verifier):
    """Escalating a role by editing the middle segment is the obvious attack."""
    import base64
    import json

    header, payload, signature = make_token(roles=["hr_manager"]).split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["realm_access"]["roles"] = ["admin"]
    tampered = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()

    with pytest.raises(InvalidToken):
        token_verifier.verify(f"{header}.{tampered}.{signature}")


def test_an_expired_token_is_refused(token_verifier):
    with pytest.raises(InvalidToken):
        token_verifier.verify(make_token(issued_at=time.time() - 7200, expires_in=60))


def test_a_token_for_another_audience_is_refused(token_verifier):
    """Without this, a token minted for any other client in the realm is accepted."""
    with pytest.raises(InvalidToken):
        token_verifier.verify(make_token(audience="some-other-app"))


def test_a_token_from_another_issuer_is_refused(token_verifier):
    """A different realm, or an attacker's own Keycloak."""
    with pytest.raises(InvalidToken):
        token_verifier.verify(make_token(issuer="http://evil.test/realms/salary"))


@pytest.mark.parametrize("missing", ["exp", "sub", "aud", "iss", "iat"])
def test_a_token_missing_a_required_claim_is_refused(token_verifier, missing):
    """Absent is not the same as valid. Without an explicit requirement, a token with
    no exp never expires."""
    with pytest.raises(InvalidToken):
        token_verifier.verify(make_token(omit=(missing,)))


def test_an_unsigned_token_is_refused(token_verifier):
    """alg: none is the oldest JWT attack there is, and pinning algorithms is what
    stops it."""
    import jwt

    unsigned = jwt.encode(
        {
            "sub": "x",
            "aud": get_settings().oidc_audience,
            "iss": get_settings().oidc_issuer,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(InvalidToken):
        token_verifier.verify(unsigned)


def test_modest_clock_skew_is_tolerated(token_verifier):
    """Hosts disagree about the time. A token that expired four seconds ago, by this
    machine's clock, is not an attack."""
    token = make_token(issued_at=time.time() - 304, expires_in=300)

    assert token_verifier.verify(token)["sub"] == "kc-sub-123"


def test_an_unreachable_provider_is_not_reported_as_a_bad_credential(token_verifier):
    """An outage must not read as everyone's password being wrong: 502, not 401."""

    def unreachable(_token):
        raise httpx.ConnectError("jwks unreachable")

    token_verifier._signing_key = unreachable

    with pytest.raises(UpstreamUnavailable):
        token_verifier.verify(make_token())


# ── end to end through the proxy ────────────────────────────────────────────────


def test_a_forged_token_in_a_valid_session_never_reaches_the_upstream(client, session):
    """The session cookie could only hold a forged token if the encryption key leaked.
    Verifying per request means that leak does not become every service behind."""
    session.access_token = make_token(key=OTHER_PRIVATE_KEY, roles=["admin"])
    client.cookies.set(get_settings().session_cookie_name, encode(session))

    with respx.mock:
        route = respx.get(f"{API}/employees").mock(return_value=httpx.Response(200))
        response = client.get("/api/employees")

    assert response.status_code == 401
    assert not route.called
