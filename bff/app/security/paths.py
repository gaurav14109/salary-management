"""What the proxy may forward.

A proxy that passes a client-supplied path straight through is a server-side request
forgery primitive (OWASP A10): ``/api/../../admin`` escapes the surface the proxy was
meant to expose, and a URL-encoded ``%2e%2e`` escapes naive string checks.

Rejected rather than normalised. Normalising guesses at intent, and a request nobody
meant to send is better refused than silently rewritten into one.
"""

import urllib.parse

from app.errors import UnsafePath

_CONTROL = {"\n", "\r", "\x00"}


def safe_upstream_path(path: str) -> str:
    """Validate a client-supplied path segment before it reaches an upstream."""
    if any(c in path for c in _CONTROL):
        raise UnsafePath()

    # Decode twice: a single decode turns %252e%252e into %2e%2e, which is still an
    # escape attempt and which the HTTP client would decode again on the way out.
    decoded = urllib.parse.unquote(urllib.parse.unquote(path))
    if any(c in decoded for c in _CONTROL):
        raise UnsafePath()

    for candidate in (path, decoded):
        if candidate.startswith("/"):
            raise UnsafePath()
        if "//" in candidate:
            raise UnsafePath()
        if any(segment == ".." for segment in candidate.split("/")):
            raise UnsafePath()
        # A full URL in the path position is an attempt to retarget the proxy.
        if "://" in candidate:
            raise UnsafePath()

    return path
