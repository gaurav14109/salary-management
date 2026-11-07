"""Typed failures, and the one place they become HTTP responses.

A handler raises something that describes *what went wrong in the domain* and never
builds a response. That separation is the point: a service function has no business
knowing it is being called over HTTP, and a route has no business deciding what a
database conflict looks like to a client.

Two rules, both OWASP A09/A05:

  * a client learns what it can act on, and nothing else - no stack trace, no SQL,
    no driver name, no hostname;
  * every response carries the request id, so a user reporting "it failed" hands you
    the one string that finds the full detail in the log.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import ErrorResponse, FieldError

log = logging.getLogger("salary.error")


class SalaryError(Exception):
    """Base for failures this service recognises and can describe safely.

    Subclasses carry the status and a default sentence. Anything not derived from
    this is, by definition, unanticipated - and is reported as a bare 500 with
    nothing of its contents disclosed.
    """

    status_code = 500
    detail = "Something went wrong."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail:
            self.detail = detail


class NotAuthenticated(SalaryError):
    """No usable identity on the request. Settled at the BFF, so reaching this means
    the request did not come through it, or came through it without a subject."""

    status_code = 401
    detail = "Not authenticated."


class Forbidden(SalaryError):
    """Known caller, insufficient role. Deliberately not a 404: the caller is already
    identified, so there is nothing to conceal, and a 404 would send whoever debugs
    it hunting a routing bug that does not exist."""

    status_code = 403
    detail = "You do not have permission to perform this action."


class NotFound(SalaryError):
    status_code = 404
    detail = "Not found."


class Conflict(SalaryError):
    """The request is valid but the current state refuses it - a duplicate code, a
    second open salary row. A 409 tells the client retrying will not help."""

    status_code = 409
    detail = "That change conflicts with the current state."


class UnprocessableEntity(SalaryError):
    """Shape is right, meaning is wrong: no FX rate for the effective date, an
    effective_from before the hire date. Validation Pydantic cannot do."""

    status_code = 422
    detail = "The request could not be processed."


class MissingFxRate(UnprocessableEntity):
    """No exchange rate exists for this currency on or before this date.

    Its own class rather than a bare UnprocessableEntity because it is the single
    most likely reason a salary write or an import row is rejected, and because the
    fix is specific: an admin adds the rate. The message names both the currency and
    the date so the person reading it knows exactly which row to create.

    There is deliberately no fallback to 1.0. A salary stored at parity is wrong in a
    way no report reveals - the figure looks like money and is off by a factor of
    eighty.
    """

    def __init__(self, currency: str, as_of: object) -> None:
        super().__init__(f"No FX rate for {currency} as of {as_of}.")
        self.currency = currency
        self.as_of = as_of


class PayloadTooLarge(SalaryError):
    """More than this endpoint will read into memory.

    Checked before parsing rather than after: discovering a file is too big by
    loading it is the mistake repeated. 413 rather than 422 because the problem is
    the size, not the contents - the caller cannot fix it by editing a field.
    """

    status_code = 413
    detail = "That file is too large."


class BackdatedSalary(UnprocessableEntity):
    """A salary effective on or before the one currently in force.

    Refused rather than accommodated. Closing the open row would set its end date
    before its own start date - a row that ends before it begins, which the database
    refuses anyway - and, worse, it would rewrite a period that has already been
    reported on. A correction to a past period is a different operation with
    different consequences, and it should look different.

    422 rather than 409: the caller picked a date, and picking another one is the
    fix. A 409 would say the request was right and the world was wrong.
    """

    def __init__(self, effective_from: object, current_from: object) -> None:
        super().__init__(
            f"Salary cannot start on {effective_from}: the current salary already "
            f"starts on {current_from}. Use a later date."
        )
        self.effective_from = effective_from
        self.current_from = current_from


def _response(request: Request, status_code: int, detail: str, **extra: object) -> JSONResponse:
    """The one place an error becomes bytes.

    Built through ErrorResponse rather than a dict literal, so the shape the SPA
    parses is the shape declared in the OpenAPI document - and a field added here
    cannot quietly diverge from the contract the frontend was generated against.
    """
    body = ErrorResponse(
        detail=detail,
        request_id=getattr(request.state, "request_id", None),
        **extra,
    )
    # exclude_none keeps `fields` off every non-validation error rather than
    # sending a null the client has to ignore.
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers. Called once, at wiring time."""

    @app.exception_handler(SalaryError)
    async def _salary_error(request: Request, exc: SalaryError) -> JSONResponse:
        # Expected failures: logged at warning, since a 403 is information about the
        # system, not a fault in it. Noisy enough to spot a pattern, quiet enough
        # not to drown the log.
        log.warning(
            "handled_error",
            extra={
                "error": type(exc).__name__,
                "status": exc.status_code,
                "path": request.url.path,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _response(request, exc.status_code, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Framework-raised: a 404 from no matching route, a 405 from the wrong verb.
        return _response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default body echoes the offending input back. For this service
        # that means a salary figure, or whatever else was posted, in a response and
        # from there in a browser log or an error tracker. The field name is what the
        # caller needs; the value is what they already sent.
        fields = [
            FieldError(
                field=".".join(str(part) for part in error["loc"][1:]),
                message=error["msg"],
            )
            for error in exc.errors()
        ]
        return _response(request, 422, "Invalid request.", fields=fields)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Everything the operator needs goes to the log; one sentence goes to the
        # client. The request id is the only thing joining them, which is why it is
        # in both.
        log.exception(
            "unhandled_error",
            extra={
                "path": request.url.path,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _response(request, 500, "Something went wrong.")
