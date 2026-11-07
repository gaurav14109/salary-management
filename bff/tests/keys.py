"""A fixture signing key, so the suite exercises the real verifier.

Only the key source is swapped - jwt.decode still checks signature, issuer, audience
and expiry for real. Running Keycloak in CI to test token verification would be slower
and would test Keycloak rather than this code.
"""

import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()

OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(
    *,
    sub: str = "kc-sub-123",
    username: str = "hr",
    email: str | None = "hr@acme.test",
    roles: list[str] | None = None,
    audience: str | None = None,
    issuer: str | None = None,
    expires_in: int = 300,
    issued_at: float | None = None,
    key=None,
    omit: tuple[str, ...] = (),
) -> str:
    settings = get_settings()
    now = issued_at if issued_at is not None else time.time()
    claims = {
        "sub": sub,
        "preferred_username": username,
        "email": email,
        "realm_access": {"roles": roles if roles is not None else ["hr_manager"]},
        "aud": audience or settings.oidc_audience,
        "iss": issuer or settings.oidc_issuer,
        "iat": int(now),
        "exp": int(now + expires_in),
    }
    for claim in omit:
        claims.pop(claim, None)
    return jwt.encode(claims, key or PRIVATE_KEY, algorithm="RS256")
