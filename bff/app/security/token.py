"""Token verification. The BFF is the authentication boundary.

Every request that leaves here carrying a Bearer token has had that token verified:
signature against the provider's published keys, issuer, audience, expiry and
not-before. Authentication is answered once, here, for every service behind.

What this deliberately does not do is authorize. Whether a role may call an endpoint
is the salary service's decision, because only it knows what its endpoints mean. This
layer answers "is this really who they say they are", and nothing else.

Keys are fetched from JWKS and cached. Introspecting with the provider on every
request would close the window in which a revoked token still validates, at the cost
of a network round trip per request and the provider becoming a hard dependency of
every page load. Short token lifetimes narrow that window instead.
"""

import logging
import time

import httpx
import jwt
from jwt import PyJWKClient

from app.config import get_settings
from app.errors import BffError, UpstreamUnavailable

log = logging.getLogger("bff.token")


class InvalidToken(BffError):
    """The token is not one this service will act on.

    One exception for every failure mode on purpose: telling a caller whether the
    signature, the audience or the expiry was wrong tells an attacker which to fix.
    """

    status_code = 401
    detail = "Not authenticated"


class TokenVerifier:
    """Verifies access tokens against the provider's published signing keys.

    The key set is cached and refetched on expiry, or immediately when a token
    arrives signed by a key id the cache does not know - which is what key rotation
    looks like from here, and the alternative is every user failing until the TTL
    lapses.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http
        self._jwks: PyJWKClient | None = None
        self._fetched_at: float = 0.0

    @property
    def _jwks_uri(self) -> str:
        # Fetched by this process, so the back-channel address. The issuer that
        # tokens must claim is still the public one - see verify() below.
        return f"{get_settings().backchannel_issuer}/protocol/openid-connect/certs"

    def _client(self, *, force_refresh: bool = False) -> PyJWKClient:
        settings = get_settings()
        stale = time.monotonic() - self._fetched_at > settings.oidc_jwks_ttl_seconds
        if self._jwks is None or stale or force_refresh:
            self._jwks = PyJWKClient(
                self._jwks_uri, cache_keys=True, lifespan=settings.oidc_jwks_ttl_seconds
            )
            self._fetched_at = time.monotonic()
        return self._jwks

    def verify(self, token: str) -> dict:
        """Return the verified claims, or raise InvalidToken."""
        settings = get_settings()

        try:
            signing_key = self._signing_key(token)
        except jwt.PyJWKClientError as exc:
            log.warning("jwks_key_not_found")
            raise InvalidToken() from exc
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            # Reaching the provider failed. That is not the user's fault and must not
            # look like a rejected credential, or an outage reads as everyone's
            # password being wrong.
            log.error("jwks_unreachable")
            raise UpstreamUnavailable("The identity provider is unavailable") from exc

        try:
            return jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                # Pinning the algorithm is not a formality: accepting whatever the
                # header asks for is how "alg: none" and HS256-with-the-public-key
                # forgeries work.
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=30,  # tolerate modest clock skew between hosts
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidToken() from exc
        except jwt.InvalidAudienceError as exc:
            # Without this check, a token minted for any other client in the realm
            # would be accepted here.
            log.warning("token_wrong_audience")
            raise InvalidToken() from exc
        except jwt.InvalidIssuerError as exc:
            log.warning("token_wrong_issuer")
            raise InvalidToken() from exc
        except jwt.InvalidTokenError as exc:
            log.warning("token_invalid", extra={"reason": type(exc).__name__})
            raise InvalidToken() from exc

    def _signing_key(self, token: str):
        try:
            return self._client().get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError:
            # An unknown key id usually means the provider rotated. Refetch once
            # before giving up, rather than failing every request until the TTL ends.
            log.info("jwks_refresh_on_unknown_kid")
            return self._client(force_refresh=True).get_signing_key_from_jwt(token).key

    def reset(self) -> None:
        """For tests, and for a long-running process whose realm moved."""
        self._jwks = None
        self._fetched_at = 0.0
