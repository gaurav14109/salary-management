"""The login flow. The only part of the system that knows OIDC exists."""

import logging
import secrets
import urllib.parse

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.clients import keycloak as kc
from app.clients.keycloak import KeycloakClient
from app.config import get_settings
from app.deps import keycloak, optional_session, verifier
from app.errors import InvalidLoginTransaction, RateLimited
from app.security import rate_limit
from app.security.csrf import CSRF_COOKIE, new_token
from app.security.redirects import safe_return_path
from app.security.session import Session, decrypt_text, encode, encrypt_text
from app.security.token import TokenVerifier

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("bff.auth")

TRANSACTION_COOKIE = "salary_login_txn"
_TXN_SEPARATOR = "|"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login")
async def login(
    request: Request,
    return_to: str = "/",
    client: KeycloakClient = Depends(keycloak),
) -> Response:
    settings = get_settings()

    if not rate_limit.allow(f"login:{_client_key(request)}", settings.login_rate_limit_per_minute):
        raise RateLimited()

    # An untrusted query parameter decides where the browser lands after a genuine
    # login on a genuine domain, which is exactly what makes an open redirect useful
    # to a phisher. Reduced to a same-site path before it is stored.
    destination = safe_return_path(return_to)

    code_verifier, challenge = kc.new_pkce()
    state = secrets.token_urlsafe(32)
    endpoints = await client.endpoints()

    params = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": f"{settings.bff_base_url}/auth/callback",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    log.info("login_started", extra={"request_id": request.state.request_id})

    response = RedirectResponse(f"{endpoints.authorization}?{urllib.parse.urlencode(params)}")
    # The verifier and state must survive a round trip through the browser, and the
    # browser is all that comes back. Encrypted in a cookie rather than held in server
    # memory, which would break the moment there is more than one instance.
    response.set_cookie(
        TRANSACTION_COOKIE,
        encrypt_text(_TXN_SEPARATOR.join([state, code_verifier, destination])),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",  # must survive the redirect back from Keycloak
        max_age=600,
        path="/auth",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    salary_login_txn: str | None = Cookie(default=None),
    client: KeycloakClient = Depends(keycloak),
    token_verifier: TokenVerifier = Depends(verifier),
) -> Response:
    settings = get_settings()

    if not rate_limit.allow(
        f"callback:{_client_key(request)}", settings.login_rate_limit_per_minute
    ):
        raise RateLimited()

    if error:
        # Keycloak's error string is logged, not returned: it is operational detail,
        # and reflecting an attacker-influenced value is its own problem.
        log.warning("login_failed_at_idp", extra={"request_id": request.state.request_id})
        raise InvalidLoginTransaction()

    if not code or not state or not salary_login_txn:
        raise InvalidLoginTransaction()

    decrypted = decrypt_text(salary_login_txn)
    if decrypted is None or decrypted.count(_TXN_SEPARATOR) < 2:
        raise InvalidLoginTransaction()
    expected_state, code_verifier, destination = decrypted.split(_TXN_SEPARATOR, 2)

    # Without this, any site could complete a login flow on the user's behalf. The
    # attacker cannot produce a state matching a cookie they never saw.
    if not secrets.compare_digest(state, expected_state):
        log.warning("login_state_mismatch", extra={"request_id": request.state.request_id})
        raise InvalidLoginTransaction()

    tokens = await client.exchange_code(code=code, verifier=code_verifier)

    # Verified even though it arrived straight from the token endpoint over TLS.
    # The BFF is the authentication boundary, so it does not have one rule for
    # tokens it fetched and another for tokens it is handed - and this is where a
    # misconfigured audience or issuer surfaces, at login, rather than as a 401 on
    # the first real request.
    claims = token_verifier.verify(tokens["access_token"])

    session = Session(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=kc.expires_at(tokens),
        sub=claims["sub"],
        username=claims.get("preferred_username", ""),
        email=claims.get("email"),
        roles=sorted(claims.get("realm_access", {}).get("roles", [])),
        # A fresh CSRF secret per login, so a value learned before authentication is
        # useless after it.
        csrf_token=new_token(),
    )

    log.info(
        "login_succeeded",
        extra={"request_id": request.state.request_id, "sub": session.sub, "roles": session.roles},
    )

    response = RedirectResponse(settings.spa_base_url.rstrip("/") + destination)
    set_session_cookies(response, session)
    response.delete_cookie(TRANSACTION_COOKIE, path="/auth")
    return response


@router.get("/me")
async def me(session: Session | None = Depends(optional_session)) -> JSONResponse:
    """Who is logged in, or where to go to log in.

    A 401 carrying login_url rather than a redirect: a fetch() that follows a redirect
    to Keycloak fails CORS and surfaces as an unrelated-looking error. Only the SPA
    knows when navigating the whole window is acceptable.
    """
    if session is None:
        return JSONResponse(
            status_code=401,
            content={
                "authenticated": False,
                "login_url": f"{get_settings().bff_base_url}/auth/login",
            },
        )
    return JSONResponse(
        {
            "authenticated": True,
            "username": session.username,
            "email": session.email,
            "roles": session.roles,
        }
    )


@router.post("/logout")
async def logout(
    request: Request,
    session: Session | None = Depends(optional_session),
    client: KeycloakClient = Depends(keycloak),
) -> JSONResponse:
    if session is not None:
        try:
            # End the Keycloak session too. Clearing our cookie alone leaves the user
            # silently logged straight back in by the next /auth/login.
            await client.end_session(session.refresh_token)
        except Exception:  # noqa: BLE001 - a failed upstream logout must not block ours
            log.warning("idp_logout_failed", extra={"request_id": request.state.request_id})
        log.info("logout", extra={"request_id": request.state.request_id, "sub": session.sub})

    response = JSONResponse({"authenticated": False})
    clear_session_cookies(response)
    return response


def set_session_cookies(response: Response, session: Session) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        encode(session),
        httponly=True,  # the point of the whole layer: JavaScript cannot read this
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable by same-origin JavaScript on purpose: the half of the double-submit
    # pair the SPA echoes back in a header.
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
