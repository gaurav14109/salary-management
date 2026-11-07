"""Where the browser may be sent after login.

An open redirect (OWASP A01) is one of the easiest things to leave in an auth flow and
one of the most useful to a phisher: a link to your real, trusted domain that lands the
user on theirs, after a genuine login. It is also trivially avoidable - the only
legitimate value here is a path within the SPA.
"""

import urllib.parse

_CONTROL = {"\n", "\r", "\t", "\\", "\x00"}


def safe_return_path(value: str | None) -> str:
    """Reduce an untrusted ``return_to`` to a safe same-site path.

    Rejects, rather than attempts to repair:

      * absolute URLs      ``https://evil.test/``      - a different origin
      * scheme-relative    ``//evil.test``             - urljoin treats this as absolute
      * backslash variants ``/\\evil.test``            - some browsers normalise to //
      * control characters                             - header and log injection
      * anything not starting with a single ``/``
    """
    if not value:
        return "/"
    if any(c in value for c in _CONTROL):
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"

    parsed = urllib.parse.urlsplit(value)
    # A path cannot carry a scheme or a host. If parsing found either, it was a URL.
    if parsed.scheme or parsed.netloc:
        return "/"
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment)) or "/"
