"""Shared dependencies.

The HTTP client and the Keycloak client are created once at startup and held on
application state, so every request reuses one connection pool.
"""

from fastapi import Cookie, Request

from app.clients.keycloak import KeycloakClient
from app.config import get_settings
from app.errors import NotAuthenticated
from app.security.session import Session, decode
from app.security.token import TokenVerifier


def keycloak(request: Request) -> KeycloakClient:
    return request.app.state.keycloak


def verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


def optional_session(salary_session: str | None = Cookie(default=None)) -> Session | None:
    return decode(salary_session)


def require_session(salary_session: str | None = Cookie(default=None)) -> Session:
    """A route that uses this cannot be reached without a valid session.

    Expressing it as a dependency rather than a check inside each handler is what
    makes "every proxied route is authenticated" true by construction rather than by
    review (OWASP A01).
    """
    session = decode(salary_session)
    if session is None:
        raise NotAuthenticated()
    return session


def session_cookie_name() -> str:
    return get_settings().session_cookie_name
