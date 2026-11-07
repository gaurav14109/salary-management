"""CSRF protection, which cookie-based auth makes mandatory.

The browser attaches cookies to cross-site requests automatically and does not attach
``Authorization`` headers. That difference is the entire reason bearer tokens are
immune to CSRF, and moving the session into a cookie gives it up.

Double-submit: the session carries a secret, and a readable sibling cookie carries the
same value. A cross-site page can cause the session cookie to be sent, but the same
origin policy stops it reading the sibling, so it cannot build the header.
"""

import secrets

from app.errors import CsrfFailure
from app.security.session import Session

CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "salary_csrf"

# GET, HEAD and OPTIONS change nothing, so there is nothing to forge.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def new_token() -> str:
    return secrets.token_urlsafe(32)


def enforce(method: str, supplied: str | None, session: Session) -> None:
    """Raise unless a state-changing request proves same-origin provenance."""
    if method.upper() not in UNSAFE_METHODS:
        return
    # compare_digest, not ==, so a mismatch takes the same time wherever it occurs.
    if not supplied or not secrets.compare_digest(supplied, session.csrf_token):
        raise CsrfFailure()
