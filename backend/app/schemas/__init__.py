"""Pydantic models: the contract with the UI.

Everything that crosses the wire is declared here, so the SPA's types have exactly
one source and FastAPI can publish it as OpenAPI. Internal shapes - a Principal
assembled from headers, a value object passed between services - stay dataclasses:
they are never serialized, never validated against untrusted input, and paying for
Pydantic's machinery to move a struct across a function call buys nothing.

The rule, stated once: crosses the HTTP boundary -> Pydantic. Does not -> dataclass.
"""

from app.schemas.common import (
    ErrorResponse,
    FieldError,
    HealthResponse,
    Page,
    ReadyResponse,
)
from app.schemas.dashboard import DashboardRead
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeDetail,
    EmployeeListItem,
    EmployeeRead,
    EmployeeUpdate,
    StatusUpdate,
)
from app.schemas.fx import FxRateCreate, FxRateRead
from app.schemas.imports import ImportJobRead, ImportRowError
from app.schemas.reference import (
    BandRead,
    CountryRead,
    CurrencyRead,
    DepartmentRead,
    ReferenceData,
)
from app.schemas.salary import SalaryChangeResult, SalaryRead, SalaryWrite

__all__ = [
    "BandRead",
    "CountryRead",
    "CurrencyRead",
    "DashboardRead",
    "DepartmentRead",
    "EmployeeCreate",
    "EmployeeDetail",
    "EmployeeListItem",
    "EmployeeRead",
    "EmployeeUpdate",
    "ErrorResponse",
    "ImportJobRead",
    "ImportRowError",
    "FieldError",
    "FxRateCreate",
    "FxRateRead",
    "HealthResponse",
    "Page",
    "ReadyResponse",
    "SalaryChangeResult",
    "SalaryRead",
    "SalaryWrite",
    "ReferenceData",
    "StatusUpdate",
]
