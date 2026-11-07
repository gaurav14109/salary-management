"""Pydantic models: the contract with the UI.

Everything that crosses the wire is declared here, so the SPA's types have exactly
one source and FastAPI can publish it as OpenAPI. Internal shapes - a Principal
assembled from headers, a value object passed between services - stay dataclasses:
they are never serialized, never validated against untrusted input, and paying for
Pydantic's machinery to move a struct across a function call buys nothing.

The rule, stated once: crosses the HTTP boundary -> Pydantic. Does not -> dataclass.
"""

from app.schemas.common import ErrorResponse, FieldError, HealthResponse, ReadyResponse

__all__ = ["ErrorResponse", "FieldError", "HealthResponse", "ReadyResponse"]
