"""Proxy routes.

Upstreams live in a registry rather than in a function per service, so adding one is
a line of configuration instead of a copy of the forwarding logic - and the auth,
CSRF and refresh rules cannot drift between them, because there is one copy.
"""

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request, Response

from app.clients import keycloak as kc
from app.clients.keycloak import KeycloakClient
from app.config import get_settings
from app.deps import keycloak, require_session, verifier
from app.errors import SessionExpired
from app.proxy.forwarder import forward
from app.security.csrf import CSRF_HEADER, enforce
from app.security.session import Session
from app.security.token import TokenVerifier

router = APIRouter(tags=["proxy"])
log = logging.getLogger("bff.proxy")

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url_attr: str

    @property
    def base_url(self) -> str:
        return getattr(get_settings(), self.base_url_attr)


UPSTREAMS = {
    "api": Upstream("salary-api", "salary_api_url"),
    "chat": Upstream("chat-service", "chat_service_url"),
}


async def _handle(
    request: Request,
    upstream: Upstream,
    path: str,
    session: Session,
    client: KeycloakClient,
    token_verifier: TokenVerifier,
) -> Response:
    enforce(request.method, request.headers.get(CSRF_HEADER), session)

    refreshed = False
    if session.access_token_expired():
        try:
            tokens = await client.refresh(session.refresh_token)
        except Exception as exc:  # noqa: BLE001 - any refresh failure ends the session
            log.info("refresh_failed", extra={"request_id": request.state.request_id})
            raise SessionExpired() from exc
        session.access_token = tokens["access_token"]
        session.refresh_token = tokens.get("refresh_token", session.refresh_token)
        session.expires_at = kc.expires_at(tokens)
        refreshed = True

    # The authentication boundary: no token leaves this service unverified.
    #
    # The expiry check above reads a claim; this checks the signature, the issuer and
    # the audience against the provider's published keys. It runs on every request,
    # including refreshed ones, so a key rotated or revoked mid-session is caught here
    # rather than by each service behind.
    #
    # Cost is a cached RSA verification, tens of microseconds. The alternative -
    # trusting a token because it came out of our own cookie - makes the cookie the
    # only thing standing between a leaked encryption key and every service behind.
    claims = token_verifier.verify(session.access_token)

    # Keycloak can revoke a role without the session noticing, so what reaches the
    # upstream is what the token says now, not what it said at login. Display-only
    # here; the salary service authorizes.
    session.roles = sorted(claims.get("realm_access", {}).get("roles", []))

    response = await forward(
        http=request.app.state.http,
        request=request,
        upstream_base=upstream.base_url,
        path=path,
        access_token=session.access_token,
        request_id=request.state.request_id,
    )

    if refreshed:
        # Re-issued with the same created_at, so refreshing cannot extend the absolute
        # session lifetime.
        from app.auth.routes import set_session_cookies

        set_session_cookies(response, session)
    return response


@router.api_route("/api/{path:path}", methods=METHODS)
async def proxy_api(
    request: Request,
    path: str,
    session: Session = Depends(require_session),
    client: KeycloakClient = Depends(keycloak),
    token_verifier: TokenVerifier = Depends(verifier),
) -> Response:
    return await _handle(request, UPSTREAMS["api"], path, session, client, token_verifier)


@router.api_route("/chat/{path:path}", methods=["GET", "POST"])
async def proxy_chat(
    request: Request,
    path: str,
    session: Session = Depends(require_session),
    client: KeycloakClient = Depends(keycloak),
    token_verifier: TokenVerifier = Depends(verifier),
) -> Response:
    return await _handle(request, UPSTREAMS["chat"], path, session, client, token_verifier)
