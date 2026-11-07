"""Typed failures, and the one place they become HTTP responses.

Two rules, both OWASP A09/A05:

  * a client learns what it can act on, and nothing else. No stack trace, no upstream
    hostname, no library name.
  * every response carries the request id, so a user reporting "it failed" gives you
    the one string that finds the full detail in the log.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("bff.error")


class BffError(Exception):
    """Base for failures this service recognises and can describe safely."""

    status_code = 500
    detail = "Something went wrong"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail:
            self.detail = detail


class NotAuthenticated(BffError):
    status_code = 401
    detail = "Not authenticated"


class SessionExpired(BffError):
    status_code = 401
    detail = "Session expired"


class CsrfFailure(BffError):
    status_code = 403
    detail = "CSRF token missing or invalid"


class InvalidLoginTransaction(BffError):
    """A callback whose state does not match the cookie, or whose cookie is gone."""

    status_code = 400
    detail = "Login could not be completed. Please try again."


class UnsafePath(BffError):
    status_code = 400
    detail = "Invalid path"


class PayloadTooLarge(BffError):
    status_code = 413
    detail = "Request body is too large"


class RateLimited(BffError):
    status_code = 429
    detail = "Too many attempts. Please wait and try again."


class UpstreamUnavailable(BffError):
    """The upstream could not be reached. Distinct from an error it returned."""

    status_code = 502
    detail = "The service is temporarily unavailable"


class UpstreamTimeout(BffError):
    status_code = 504
    detail = "The service took too long to respond"


def _body(request: Request, detail: str, **extra: object) -> dict:
    return {"detail": detail, "request_id": getattr(request.state, "request_id", None), **extra}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BffError)
    async def _bff_error(request: Request, exc: BffError) -> JSONResponse:
        extra: dict[str, object] = {}
        # A 401 is the one error the SPA must be able to act on without knowing any
        # OIDC detail, so it carries where to go.
        if isinstance(exc, NotAuthenticated | SessionExpired):
            from app.config import get_settings

            extra["login_url"] = f"{get_settings().bff_base_url}/auth/login"

        log.warning(
            "handled_error",
            extra={
                "error": type(exc).__name__,
                "status": exc.status_code,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=exc.status_code, content=_body(request, exc.detail, **extra)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(request, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default body echoes the input back, which for this service can
        # mean echoing a token or a cookie into a client-visible response.
        fields = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body(request, "Invalid request", fields=fields)
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Full detail to the log, a generic sentence to the client. The request id is
        # the bridge between the two.
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content=_body(request, "Something went wrong"))
