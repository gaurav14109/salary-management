"""The employee contract.

Three write models rather than one, because the three operations accept genuinely
different fields. A single model with everything optional would accept
`{"status": "INACTIVE"}` on a PATCH that has no business closing a salary row, and
the route would have to re-check by hand what the schema had already let through.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.salary import SalaryRead


class EmployeeCreate(BaseModel):
    """A new hire. Always ACTIVE - someone who has already left is created and then
    terminated, so their salary row closes by the same path as everyone else's."""

    model_config = ConfigDict(str_strip_whitespace=True)

    employee_code: str = Field(min_length=1, max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    department_id: int
    job_title: str = Field(min_length=1, max_length=150)
    band_id: int | None = None
    hire_date: date


class EmployeeUpdate(BaseModel):
    """A partial edit. Every field optional; at least one required.

    No `employee_code` - it is the import's match key and the audit trail's human
    handle. No `status` - changing that closes a salary row, which is a different
    operation with a different endpoint.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    department_id: int | None = None
    job_title: str | None = Field(default=None, min_length=1, max_length=150)
    band_id: int | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "EmployeeUpdate":
        """An empty PATCH is almost certainly a UI bug. Accepting it silently would
        return 200 for a save that saved nothing."""
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change.")
        return self


class StatusUpdate(BaseModel):
    """Activate or terminate. Separate because it moves the salary row with it."""

    status: str = Field(pattern=r"^(ACTIVE|INACTIVE)$")
    termination_date: date | None = Field(
        default=None,
        description="Required when terminating. The open salary row is closed on this day.",
    )


class EmployeeRead(BaseModel):
    """An employee in a list. Reference ids are resolved to names, because a table
    showing department 7 is a table nobody can read."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_code: str
    first_name: str
    last_name: str
    email: str | None
    country_code: str
    department_id: int
    job_title: str
    band_id: int | None
    status: str
    hire_date: date
    termination_date: date | None


class EmployeeListItem(BaseModel):
    """One row of the employee list: the person and what they are currently paid.

    `current_salary` is nullable and that is not an edge case - a new hire has no pay
    row until someone enters one, and the list is where they go to do it.
    """

    model_config = ConfigDict(frozen=True)

    employee: EmployeeRead
    current_salary: SalaryRead | None


class EmployeeDetail(BaseModel):
    """One employee, their current pay, and how it got there."""

    model_config = ConfigDict(frozen=True)

    employee: EmployeeRead
    current_salary: SalaryRead | None
    history: list[SalaryRead]
