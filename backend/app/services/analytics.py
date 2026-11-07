"""Dashboard aggregates.

Two rules, and both are about trusting the number on the screen.

**Everything sums `usd_amount`, the column frozen at write time.** Nothing here
converts a currency. Correcting an FX rate must never rewrite a quarter Finance has
already received, and a dashboard that recomputes on read would do exactly that.

**Everything reads `current_salary`**, the view that excludes closed rows and
terminated employees. Writing `effective_to IS NULL AND status = 'ACTIVE'` at each
call site is how one query eventually forgets the second half and counts leavers in
total payroll - permanently, with nothing raising.

Aggregation happens in SQL. Pulling 10,000 rows into this process to add them up
would be the thing that does not scale, and the database is better at it anyway.
"""

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Column, Integer, MetaData, Numeric, Select, String, Table, func, select
from sqlalchemy.orm import Session

from app.models import Band, Country, Department

log = logging.getLogger("salary.analytics")

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")

# The view is addressed as a lightweight Table rather than an ORM model: mapping it
# would invite someone to write to it, and a view that looks writable is a trap.
CURRENT = Table(
    "current_salary",
    MetaData(),
    Column("employee_id", Integer),
    Column("employee_code", String),
    Column("first_name", String),
    Column("last_name", String),
    Column("amount", Numeric(14, 3)),
    Column("currency", String),
    Column("usd_amount", Numeric(14, 2)),
    Column("country_code", String),
    Column("department_id", Integer),
    Column("band_id", Integer),
)


@dataclass(frozen=True)
class Person:
    """Who earns the most, or the least.

    The person is named, not just the figure. Both roles can already see every salary
    on the employee list, so this discloses nothing new - and "highest salary:
    $100,000" with no name is a number nobody can act on.
    """

    employee_code: str
    first_name: str
    last_name: str
    usd_amount: Decimal
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class CountryTotal:
    country_code: str
    name: str
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


@dataclass(frozen=True)
class DepartmentTotal:
    department_id: int
    name: str
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


@dataclass(frozen=True)
class BandTotal:
    band_id: int
    name: str
    rank: int
    employee_count: int
    total_usd: Decimal
    average_usd: Decimal


@dataclass(frozen=True)
class CurrencyTotal:
    """What people are actually paid in.

    The one place a local figure belongs on this page: "how much of payroll is
    exposed to the rupee" is a question USD totals cannot answer.
    """

    currency: str
    employee_count: int
    total_local: Decimal
    total_usd: Decimal


@dataclass(frozen=True)
class DashboardSummary:
    """Everything the dashboard renders, from one call.

    A dataclass, not a Pydantic model - the API layer converts it. Services return
    values, not responses.
    """

    employee_count: int
    total_payroll_usd: Decimal
    average_salary_usd: Decimal
    median_salary_usd: Decimal
    highest: Person | None
    lowest: Person | None
    by_country: list[CountryTotal] = field(default_factory=list)
    by_department: list[DepartmentTotal] = field(default_factory=list)
    by_band: list[BandTotal] = field(default_factory=list)
    currency_breakdown: list[CurrencyTotal] = field(default_factory=list)


def summary(
    db: Session,
    *,
    country: str | None = None,
    department: int | None = None,
    band: int | None = None,
) -> DashboardSummary:
    """Every figure on the dashboard, under one filter.

    The same filter is applied to the headline numbers and to every group-by, so the
    page cannot contradict itself - a total that describes one set beside a chart
    describing another is worse than no chart.
    """
    filters = _filters(country=country, department=department, band=band)

    totals = db.execute(
        select(
            func.count().label("employee_count"),
            func.coalesce(func.sum(CURRENT.c.usd_amount), 0).label("total_usd"),
            func.coalesce(func.avg(CURRENT.c.usd_amount), 0).label("average_usd"),
            # percentile_cont interpolates between the two middle rows, which is what
            # "median" means for an even count. The mean is reported too, and the two
            # differ: one outlier moves the mean and leaves the median alone.
            func.coalesce(
                func.percentile_cont(0.5).within_group(CURRENT.c.usd_amount.asc()), 0
            ).label("median_usd"),
        ).where(*filters)
    ).one()

    log.info(
        "dashboard_summary",
        extra={
            "employee_count": totals.employee_count,
            "country": country,
            "department": department,
            "band": band,
        },
    )

    return DashboardSummary(
        employee_count=totals.employee_count,
        total_payroll_usd=_money(totals.total_usd),
        average_salary_usd=_money(totals.average_usd),
        median_salary_usd=_money(totals.median_usd),
        highest=_extreme(db, filters, highest=True),
        lowest=_extreme(db, filters, highest=False),
        by_country=_by_country(db, filters),
        by_department=_by_department(db, filters),
        by_band=_by_band(db, filters),
        currency_breakdown=_by_currency(db, filters),
    )


def _filters(*, country: str | None, department: int | None, band: int | None) -> list:
    conditions = []
    if country:
        conditions.append(CURRENT.c.country_code == country.upper())
    if department is not None:
        conditions.append(CURRENT.c.department_id == department)
    if band is not None:
        conditions.append(CURRENT.c.band_id == band)
    return conditions


def _extreme(db: Session, filters: list, *, highest: bool) -> Person | None:
    order = CURRENT.c.usd_amount.desc() if highest else CURRENT.c.usd_amount.asc()
    row = db.execute(
        select(
            CURRENT.c.employee_code,
            CURRENT.c.first_name,
            CURRENT.c.last_name,
            CURRENT.c.usd_amount,
            CURRENT.c.amount,
            CURRENT.c.currency,
        )
        .where(*filters)
        .order_by(order, CURRENT.c.employee_code)
        .limit(1)
    ).first()
    if row is None:
        # An empty payroll is the normal state on day one, not a failure. The
        # dashboard has to render.
        return None
    return Person(
        employee_code=row.employee_code,
        first_name=row.first_name,
        last_name=row.last_name,
        usd_amount=_money(row.usd_amount),
        amount=row.amount,
        currency=row.currency,
    )


def _grouped(db: Session, filters: list, *columns, join=None, order_by=None) -> list:
    statement: Select = select(
        *columns,
        func.count().label("employee_count"),
        func.coalesce(func.sum(CURRENT.c.usd_amount), 0).label("total_usd"),
        func.coalesce(func.avg(CURRENT.c.usd_amount), 0).label("average_usd"),
    )
    if join is not None:
        statement = statement.select_from(CURRENT).join(join[0], join[1])
    statement = statement.where(*filters).group_by(*columns).order_by(order_by)
    return db.execute(statement).all()


def _by_country(db: Session, filters: list) -> list[CountryTotal]:
    rows = _grouped(
        db,
        filters,
        CURRENT.c.country_code,
        Country.name,
        join=(Country, Country.code == CURRENT.c.country_code),
        # Largest spend first: the dashboard's top row should be the one worth
        # looking at, not whichever country starts with 'A'.
        order_by=func.sum(CURRENT.c.usd_amount).desc(),
    )
    return [
        CountryTotal(
            country_code=row.country_code,
            name=row.name,
            employee_count=row.employee_count,
            total_usd=_money(row.total_usd),
            average_usd=_money(row.average_usd),
        )
        for row in rows
    ]


def _by_department(db: Session, filters: list) -> list[DepartmentTotal]:
    rows = _grouped(
        db,
        filters,
        CURRENT.c.department_id,
        Department.name,
        join=(Department, Department.id == CURRENT.c.department_id),
        order_by=func.sum(CURRENT.c.usd_amount).desc(),
    )
    return [
        DepartmentTotal(
            department_id=row.department_id,
            name=row.name,
            employee_count=row.employee_count,
            total_usd=_money(row.total_usd),
            average_usd=_money(row.average_usd),
        )
        for row in rows
    ]


def _by_band(db: Session, filters: list) -> list[BandTotal]:
    rows = _grouped(
        db,
        filters,
        CURRENT.c.band_id,
        Band.name,
        Band.rank,
        join=(Band, Band.id == CURRENT.c.band_id),
        # By rank, not by name. Sorted as text bands read L1, L10, L11, L2, and a
        # pay-by-band chart in that order is meaningless - which is why band.rank
        # exists at all.
        order_by=Band.rank.asc(),
    )
    return [
        BandTotal(
            band_id=row.band_id,
            name=row.name,
            rank=row.rank,
            employee_count=row.employee_count,
            total_usd=_money(row.total_usd),
            average_usd=_money(row.average_usd),
        )
        for row in rows
    ]


def _by_currency(db: Session, filters: list) -> list[CurrencyTotal]:
    rows = db.execute(
        select(
            CURRENT.c.currency,
            func.count().label("employee_count"),
            func.coalesce(func.sum(CURRENT.c.amount), 0).label("total_local"),
            func.coalesce(func.sum(CURRENT.c.usd_amount), 0).label("total_usd"),
        )
        .where(*filters)
        .group_by(CURRENT.c.currency)
        .order_by(func.sum(CURRENT.c.usd_amount).desc())
    ).all()
    return [
        CurrencyTotal(
            currency=row.currency,
            employee_count=row.employee_count,
            total_local=row.total_local,
            total_usd=_money(row.total_usd),
        )
        for row in rows
    ]


def _money(value) -> Decimal:
    """Two places, half-up.

    AVG returns more precision than money has, and half-up is what finance expects -
    Python's default is banker's rounding, which is defensible statistically and
    surprising on a report.
    """
    if value is None:
        return ZERO
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)
