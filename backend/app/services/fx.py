"""Exchange rates: looking one up, and converting with it.

Two functions, both pure of HTTP. They take a Session and plain values so the same
rule applies whether the caller is a route, the importer or a test - a conversion
that behaved differently depending on how it was invoked would be the worst kind of
bug to find in payroll.

The rule the money model rests on: convert once, at write time, with a rate that
existed on or before that date. Never at read time, and never at 1.0.
"""

import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import MissingFxRate
from app.models import FxRate

log = logging.getLogger("salary.fx")

REPORTING_CURRENCY = "USD"
USD_PLACES = Decimal("0.01")


def get_rate(db: Session, currency: str, as_of: date) -> Decimal:
    """Units of `currency` per 1 USD, as known on `as_of`.

    The most recent rate dated on or before `as_of`. A rate dated after it cannot be
    used: it did not exist yet, and applying it would rewrite history with
    information from the future.

    Raises MissingFxRate rather than returning a default. Every caller of this
    function is about to store money.
    """
    code = currency.upper()

    # USD needs no row and must not need one: a missing USD rate would make the
    # reporting currency unconvertible to itself, which is absurd and would fail
    # at exactly the wrong moment.
    if code == REPORTING_CURRENCY:
        return Decimal(1)

    rate = db.scalar(
        select(FxRate.units_per_usd)
        .where(FxRate.currency == code, FxRate.as_of <= as_of)
        .order_by(FxRate.as_of.desc())
        .limit(1)
    )
    if rate is None:
        log.info("fx_rate_missing", extra={"currency": code, "as_of": str(as_of)})
        raise MissingFxRate(code, as_of)
    return rate


def to_usd(amount: Decimal, units_per_usd: Decimal) -> Decimal:
    """Convert to USD, quantized to two places, rounded half-up.

    Division, not multiplication. `units_per_usd` is how many of the local currency
    one dollar buys, so 1,200,000 INR at 83.45 is ~14,379 USD. Multiplying gives
    100,140,000 - about seven thousand times the real figure, and plausible enough in
    a spreadsheet to survive review.

    Half-up because that is what payroll and finance expect. Python's default is
    banker's rounding, which is defensible statistically and surprising on a payslip.
    """
    if units_per_usd <= 0:
        # Guarded rather than left to raise ZeroDivisionError or produce a negative
        # salary. The database enforces this too; this is the message that names it.
        raise ValueError(f"FX rate must be positive, got {units_per_usd}.")

    converted = (amount / units_per_usd).quantize(USD_PLACES, rounding=ROUND_HALF_UP)

    if converted <= 0:
        # A real amount that rounds to 0.00 would fail the database's amount > 0
        # check as a confusing constraint violation, or - worse, if that check were
        # ever relaxed - be stored as a salary of nothing.
        raise ValueError(
            f"{amount} at {units_per_usd} per USD converts to {converted}, which is not a "
            "usable salary figure."
        )
    return converted


def list_rates(
    db: Session,
    *,
    currency: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[FxRate], int]:
    """One page of rates, newest first, with the total for the filter applied.

    Pagination lives here rather than in the route because "never return an unbounded
    collection" is a rule about the data, not about HTTP - the importer and the
    dashboard are bound by it too.
    """
    filters = []
    if currency:
        filters.append(FxRate.currency == currency.upper())

    total = db.scalar(select(func.count()).select_from(FxRate).where(*filters)) or 0
    items = list(
        db.scalars(
            select(FxRate)
            .where(*filters)
            .order_by(FxRate.as_of.desc(), FxRate.currency)
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return items, total


def create_rate(
    db: Session,
    *,
    currency: str,
    units_per_usd: Decimal,
    as_of: date,
    created_by: int,
) -> FxRate:
    """Record a rate. The caller decides who may; this decides what is valid."""
    rate = FxRate(
        currency=currency.upper(),
        units_per_usd=units_per_usd,
        as_of=as_of,
        created_by=created_by,
    )
    db.add(rate)
    db.flush()
    log.info(
        "fx_rate_created",
        extra={"currency": rate.currency, "as_of": str(as_of), "actor_id": created_by},
    )
    return rate
