"""Typed failures, and the one place they become HTTP responses."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("chat.error")


class ChatError(Exception):
    status_code = 500
    detail = "Something went wrong."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail:
            self.detail = detail


class NotAuthenticated(ChatError):
    status_code = 401
    detail = "Not authenticated."


class Forbidden(ChatError):
    status_code = 403
    detail = "You do not have permission to use the assistant."


class NotFound(ChatError):
    status_code = 404
    detail = "Not found."


class UnprocessableEntity(ChatError):
    status_code = 422
    detail = "The request could not be processed."


class UpstreamUnavailable(ChatError):
    """The salary API or the model could not be reached.

    503 rather than 500: the question was fine and trying again may work, which is
    exactly what the user needs to be told.
    """

    status_code = 503
    detail = "The assistant is unavailable right now. Please try again."


class RateLimited(UpstreamUnavailable):
    """The model provider is asking us to slow down.

    Its own class because the remedy is different from an outage: waiting fixes
    this, and the evaluation suite needs to tell "the model got it wrong" apart
    from "we ran out of free-tier tokens" - scoring quota as inaccuracy measures
    the wrong thing entirely.
    """

    detail = "The assistant is busy right now. Please try again in a moment."

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__()
        self.retry_after = retry_after


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ChatError)
    async def _chat_error(request: Request, exc: ChatError) -> JSONResponse:
        log.warning(
            "handled_error",
            extra={"error": type(exc).__name__, "status": exc.status_code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", None),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Everything useful goes to the log; one sentence goes to the client. A chat
        # service holds a prompt and tool output, and neither belongs in a response.
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Something went wrong.",
                "request_id": getattr(request.state, "request_id", None),
            },
        )
