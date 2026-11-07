"""Reference data, as the UI needs it.

One response rather than four endpoints: the edit form needs all of it at once, and
four round trips to populate four dropdowns is four chances for one to fail.
"""

from pydantic import BaseModel, ConfigDict


class CountryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class CurrencyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    minor_units: int


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class BandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # Published so the UI can order bands correctly. Sorted by name they read
    # L1, L10, L11, L2, and a pay-by-band chart in that order means nothing.
    rank: int


class ReferenceData(BaseModel):
    """Everything a form or a filter needs to offer valid choices."""

    model_config = ConfigDict(frozen=True)

    countries: list[CountryRead]
    currencies: list[CurrencyRead]
    departments: list[DepartmentRead]
    bands: list[BandRead]
