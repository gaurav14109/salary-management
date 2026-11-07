"""The dashboard contract.

Every figure is USD and every one of them is a string: a payroll total through an
IEEE 754 double is a payroll total that disagrees with the ledger.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class TopEarner(BaseModel):
    """The highest or lowest paid person under the current filter.

    Named, not just a figure. Both roles can already see every salary on the employee
    list, so this discloses nothing new - and "highest salary: $100,000" with nobody
    attached is a number nobody can act on.
    """

    model_config = ConfigDict(from_attributes=True)

    employee_code: str
    first_name: str
    last_name: str
    usd_amount: Decimal
    amount: Decimal = Field(description="What they are actually paid, in their currency.")
    currency: str


class CountrySlice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    country_code: str
    name: str = Field(description="'Germany', not 'DE' - a chart label nobody has to decode.")
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


class DepartmentSlice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    department_id: int
    name: str
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


class BandSlice(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    band_id: int
    name: str
    rank: int = Field(
        description="Sort key. Bands sorted by name read L1, L10, L11, L2, and a "
        "pay-by-band chart in that order means nothing. Arrives ordered by this."
    )
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


class CurrencySlice(BaseModel):
    """What payroll is actually denominated in.

    The one place a local figure belongs on this page: "how exposed is payroll to the
    rupee" is a question USD totals cannot answer.
    """

    model_config = ConfigDict(from_attributes=True)

    currency: str
    employee_count: int
    total_local: Decimal
    total_usd: Decimal


class DashboardRead(BaseModel):
    """Everything the dashboard renders, from one call.

    One request rather than six, because six means six chances for one to fail and
    leave the page showing a total that disagrees with the chart beside it.
    """

    model_config = ConfigDict(from_attributes=True)

    employee_count: int = Field(description="People with a current salary, not headcount.")
    total_payroll_usd: Decimal
    average_salary_usd: Decimal
    median_salary_usd: Decimal = Field(
        description="Reported alongside the mean because they differ: one outlier "
        "moves the mean and leaves the median alone."
    )
    highest: TopEarner | None
    lowest: TopEarner | None
    by_country: list[CountrySlice]
    by_department: list[DepartmentSlice]
    by_band: list[BandSlice]
    currency_breakdown: list[CurrencySlice]
