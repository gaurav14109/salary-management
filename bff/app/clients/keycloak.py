"""Everything that talks to the identity provider, behind one interface.

The rest of the service depends on the ``TokenIssuer`` protocol rather than on
Keycloak, so a test substitutes a fake without patching imports, and swapping provider
touches one file. The authorization code is exchanged with a client secret, which is
possible only because this is a server.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import get_settings
from app.errors import UpstreamTimeout, UpstreamUnavailable


@dataclass(frozen=True)
class Endpoints:
    authorization: str
    token: str
    end_session: str


class TokenIssuer(Protocol):
    async def endpoints(self) -> Endpoints: ...
    async def exchange_code(self, *, code: str, verifier: str) -> dict: ...
    async def refresh(self, refresh_token: str) -> dict: ...
    async def end_session(self, refresh_token: str) -> None: ...


def new_pkce() -> tuple[str, str]:
    """(verifier, challenge).

    PKCE still applies to a confidential client: it binds the authorization code to
    this exchange, so a code intercepted in the redirect cannot be redeemed elsewhere.
    """
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def claims_of(access_token: str) -> dict:
    """Read the payload without verifying.

    Safe here and nowhere else: this token arrived over TLS in a response to a request
    this process made. The salary API verifies signature, issuer, audience and expiry
    properly, because a token reaching it could have come from anywhere.
    """
    payload = access_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def expires_at(token_response: dict) -> float:
    return time.time() + int(token_response.get("expires_in", 300))


def _backchannel(url: str) -> str:
    """Swap the provider's public origin for the one reachable from this process.

    A plain prefix replacement, anchored at the issuer, so a URL that does not start
    with the public issuer is returned untouched rather than mangled.
    """
    settings = get_settings()
    if settings.oidc_internal_issuer and url.startswith(settings.oidc_issuer):
        return settings.oidc_internal_issuer + url[len(settings.oidc_issuer) :]
    return url


class KeycloakClient:
    """Shared across requests, holding one connection pool.

    A client per request means a fresh TCP and TLS handshake on every call, plus a
    file descriptor churned each time - which shows up as latency under load and as
    exhaustion under a lot of it.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._http = client
        self._endpoints: Endpoints | None = None

    async def endpoints(self) -> Endpoints:
        """Discovered once. Hard-coding the paths works until the realm moves.

        Discovery advertises the provider's public address on every endpoint. Two of
        the three are called by this process rather than followed by the browser, so
        those are rewritten to the address this process can actually reach. The
        authorization endpoint is left alone: the browser is the one that goes there.
        """
        if self._endpoints is None:
            settings = get_settings()
            doc = await self._get(f"{settings.backchannel_issuer}/.well-known/openid-configuration")
            self._endpoints = Endpoints(
                authorization=doc["authorization_endpoint"],
                token=_backchannel(doc["token_endpoint"]),
                end_session=_backchannel(doc["end_session_endpoint"]),
            )
        return self._endpoints

    async def exchange_code(self, *, code: str, verifier: str) -> dict:
        s = get_settings()
        return await self._post(
            (await self.endpoints()).token,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{s.bff_base_url}/auth/callback",
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret,
                "code_verifier": verifier,
            },
        )

    async def refresh(self, refresh_token: str) -> dict:
        s = get_settings()
        return await self._post(
            (await self.endpoints()).token,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret,
            },
        )

    async def end_session(self, refresh_token: str) -> None:
        s = get_settings()
        await self._post(
            (await self.endpoints()).end_session,
            {
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret,
                "refresh_token": refresh_token,
            },
            expect_json=False,
        )

    async def _get(self, url: str) -> dict:
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout("The identity provider took too long to respond") from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("The identity provider is unavailable") from exc

    async def _post(self, url: str, data: dict, *, expect_json: bool = True) -> dict:
        try:
            response = await self._http.post(url, data=data)
            response.raise_for_status()
            return response.json() if expect_json else {}
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout("The identity provider took too long to respond") from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("The identity provider is unavailable") from exc
