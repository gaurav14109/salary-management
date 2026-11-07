"""Response headers that cost nothing and close whole categories of attack.

OWASP A05. None of these are a substitute for anything else; they are the floor.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.config import get_settings

Handler = Callable[[Request], Awaitable[Response]]


async def apply_security_headers(request: Request, call_next: Handler) -> Response:
    response = await call_next(request)

    # Stops a browser second-guessing Content-Type, which is how a JSON response
    # becomes an executed script.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # This service is an API; nothing here should ever be framed.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    # Keeps tokens and ids in query strings out of other sites' referer logs.
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")

    # Authenticated payloads must not sit in a shared or browser cache.
    if request.url.path.startswith(("/auth", "/api", "/chat")):
        response.headers.setdefault("Cache-Control", "no-store, private")

    if get_settings().cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    return response
