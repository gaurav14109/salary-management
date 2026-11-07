"""The FX rate contract.

Rates are money-adjacent, so every decimal here is a `Decimal` and is serialized as
a string. JSON numbers are IEEE 754 doubles: a rate that round-trips through one is
no longer the rate that was stored, and the error compounds through every salary
converted with it.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FxRateCreate(BaseModel):
    """An admin recording a rate."""

    model_config = ConfigDict(str_strip_whitespace=True)

    currency: str = Field(
        min_length=3,
        max_length=3,
        pattern=r"^[A-Za-z]{3}$",
        description="ISO 4217 alphabetic code. Stored uppercase.",
    )
    units_per_usd: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=8,
        description="83.45 means 83.45 INR per 1 USD. Rejected at zero or below, "
        "before it can reach a division.",
    )
    as_of: date = Field(description="The day this rate is effective from.")

    @field_validator("currency")
    @classmethod
    def uppercase(cls, value: str) -> str:
        """Normalized here, so nothing downstream has to remember to.

        'inr' and 'INR' are the same currency; stored differently they are two, and
        the lookup finds neither reliably.
        """
        return value.upper()


class FxRateRead(BaseModel):
    """A rate as returned. Decimals leave as strings - see the module docstring.

    from_attributes so a route can hand over the ORM object directly: the model
    decides what is public, and a column added to the table later is not published
    by accident.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    currency: str
    units_per_usd: Decimal
    as_of: date
    created_by: int = Field(description="app_user.id of the admin who recorded it.")
