"""Responses that are not about one resource: errors and health.

The error shape matters more than it looks. It is the one response every screen in
the SPA has to handle, so it is the contract most worth pinning down and publishing
in the OpenAPI document rather than leaving as an undeclared dict.
"""

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    """One rejected input, named but not echoed.

    Deliberately carries no `value`. Pydantic's default validation body returns the
    offending input, which for this service means a salary figure in a response, and
    from there in a browser console or an error tracker. The caller already knows
    what they sent; what they need is which field and why.
    """

    model_config = ConfigDict(frozen=True)

    field: str = Field(description="Dotted path to the field, e.g. 'salary.amount'.")
    message: str = Field(description="What is wrong with it, in a sentence.")


class ErrorResponse(BaseModel):
    """Every failure, in one shape.

    `request_id` is the point of it: a user reporting "it failed" hands over this
    one string, and it finds the full detail in the log of both this service and the
    BFF, which issued it.
    """

    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "example": {
                "detail": "This action requires one of: admin.",
                "request_id": "6f1c1b0e-6f3a-4a0e-9c3a-2f1d9b7e4c11",
                "fields": None,
            }
        },
    )

    detail: str = Field(description="A sentence the caller can act on. Never internal detail.")
    request_id: str | None = Field(
        default=None, description="Correlates this response with the logs of every service."
    )
    fields: list[FieldError] | None = Field(
        default=None, description="Present only on a 422, one entry per rejected field."
    )


class HealthResponse(BaseModel):
    """Liveness. Shallow on purpose - it must not fail because something else did."""

    model_config = ConfigDict(frozen=True)

    status: str = "ok"


class ReadyResponse(BaseModel):
    """Readiness: the process is up *and* the schema it queries exists."""

    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    database: str = "ok"


class Page[ItemT](BaseModel):
    """One page of a collection.

    Every list response is this shape. No endpoint returns an unbounded collection,
    so no UI can accidentally fetch 10,000 rows by calling one - the limit is the
    API's, not the caller's discipline.
    """

    model_config = ConfigDict(frozen=True)

    items: list[ItemT]
    total: int = Field(description="Rows matching the filter, not rows on this page.")
    page: int = Field(ge=1)
    size: int = Field(ge=1)
