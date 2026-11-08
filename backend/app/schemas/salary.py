"""The salary contract.

Every money field is a `Decimal` and leaves as a string. JSON numbers are IEEE 754
doubles - a salary that round-trips through one is no longer the salary that was
stored, and nobody notices until Finance does.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.salary import CHANGE_REASONS


class SalaryWrite(BaseModel):
    """A pay change, as the UI sends it.

    Deliberately small. The caller does not send `usd_amount`, `fx_units_per_usd` or
    `fx_rate_date` - those are derived from the rate in force on `effective_from`,
    and accepting them from a client would let the browser decide what a salary is
    worth in USD.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    amount: Decimal = Field(
        gt=0,
        max_digits=14,
        decimal_places=3,
        description="In the currency the employee is paid in. Three decimal places "
        "because KWD, BHD, OMR and JOD have three.",
    )
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    effective_from: date = Field(description="Inclusive. The day this pay starts applying.")
    change_reason: str | None = Field(
        default=None,
        description=f"One of {', '.join(CHANGE_REASONS)}.",
    )

    @field_validator("currency")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()

    @field_validator("change_reason")
    @classmethod
    def known_reason(cls, value: str | None) -> str | None:
        """Checked here as well as by the CHECK constraint, so a typo is a named
        field error rather than a constraint violation the caller cannot act on."""
        if value is None:
            return None
        if value.upper() not in CHANGE_REASONS:
            raise ValueError(f"must be one of: {', '.join(CHANGE_REASONS)}")
        return value.upper()


class SalaryRead(BaseModel):
    """A salary row as returned, including what it was converted at and when.

    The FX fields are published rather than hidden: a USD figure nobody can explain
    is a USD figure nobody trusts, and "which rate produced this" is the first
    question anyone asks about a converted total.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    currency: str
    usd_amount: Decimal
    fx_units_per_usd: Decimal
    fx_rate_date: date
    effective_from: date
    effective_to: date | None = Field(description="Inclusive. Null means this is current.")
    change_reason: str | None = Field(
        default=None,
        description="NEW_HIRE, MERIT, IMPORT... Makes the history read as a story "
        "rather than a list of numbers that changed for no stated reason.",
    )
    created_by: int
    created_by_name: str | None = Field(
        default=None,
        description="Who made the change. 'Changed by user 47' is not an audit trail "
        "anyone can read.",
    )
    created_at: datetime = Field(
        description="When the change was made, as distinct from effective_from, which "
        "is when the pay starts applying. A raise entered in November and backdated to "
        "April is one event in November."
    )


class SalaryChangeResult(BaseModel):
    """What a write produced.

    `changed` is false when the amount and currency matched what was already in
    force: no row was written and no audit entry made. The UI needs to say "no
    change" rather than claiming a raise, and the importer counts these.
    """

    model_config = ConfigDict(frozen=True)

    changed: bool
    salary: SalaryRead
