"""FX lookup and conversion.

The rule the whole money model rests on: a salary is converted once, at write time,
using a rate that existed on or before that date - and if no such rate exists, the
write fails. There is no fallback to 1.0 under any condition, because a salary
silently stored at parity is wrong in a way no report will reveal.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.errors import MissingFxRate
from app.models import Currency, FxRate
from app.services.fx import get_rate, to_usd


@pytest.fixture
def rates(db, actor_id, reference_data):
    """Three INR rates, so "most recent on or before" has something to choose from."""
    for as_of, units in (
        ("2026-01-01", "82.00000000"),
        ("2026-03-01", "83.45000000"),
        ("2026-06-01", "84.10000000"),
    ):
        db.add(
            FxRate(
                currency="INR",
                units_per_usd=Decimal(units),
                as_of=date.fromisoformat(as_of),
                created_by=actor_id,
            )
        )
    db.flush()


# --- lookup --------------------------------------------------------------------


def test_returns_exact_rate_on_matching_date(db, rates):
    assert get_rate(db, "INR", date(2026, 3, 1)) == Decimal("83.45000000")


def test_returns_most_recent_rate_before_date(db, rates):
    """A salary effective mid-March uses March's rate, not June's."""
    assert get_rate(db, "INR", date(2026, 3, 15)) == Decimal("83.45000000")


def test_a_later_rate_does_not_apply_retroactively(db, rates):
    assert get_rate(db, "INR", date(2026, 5, 31)) == Decimal("83.45000000")


def test_raises_missing_fx_rate_when_currency_has_no_rate(db, rates):
    """Review Focus #1. The error names the currency and the date, because the person
    reading it has to go and add that rate."""
    with pytest.raises(MissingFxRate) as caught:
        get_rate(db, "XAF", date(2026, 3, 1))

    assert "XAF" in str(caught.value)
    assert "2026-03-01" in str(caught.value)


def test_raises_when_only_future_rates_exist(db, rates):
    """A rate dated after the salary cannot be used: it did not exist yet.

    Before 2020, where even the seeded rates do not reach - those are dated
    2020-01-01 precisely so that every realistic salary date resolves.
    """
    with pytest.raises(MissingFxRate):
        get_rate(db, "INR", date(2019, 12, 31))


def test_usd_to_usd_uses_rate_one(db):
    """USD needs no row, and must not need one - a missing USD rate would otherwise
    make the reporting currency unconvertible to itself."""
    assert get_rate(db, "USD", date(2026, 3, 1)) == Decimal("1")


def test_the_currency_is_matched_case_insensitively(db, rates):
    assert get_rate(db, "inr", date(2026, 3, 1)) == Decimal("83.45000000")


# --- conversion ----------------------------------------------------------------


def test_converts_by_dividing_not_multiplying(db):
    """1,200,000 INR at 83.45 per USD is ~14,379 USD. Multiplying would give
    100,140,000 - roughly 7,000 times the real figure, and plausible enough in a
    spreadsheet to survive review."""
    assert to_usd(Decimal("1200000.000"), Decimal("83.45000000")) == Decimal("14379.87")


def test_conversion_rounds_half_up_to_two_decimals(db):
    """Banker's rounding is Python's default and is not what payroll expects."""
    assert to_usd(Decimal("1.005"), Decimal("1")) == Decimal("1.01")
    assert to_usd(Decimal("2.675"), Decimal("1")) == Decimal("2.68")


def test_conversion_result_always_has_two_places(db):
    assert to_usd(Decimal("100.000"), Decimal("1")) == Decimal("100.00")
    assert str(to_usd(Decimal("100.000"), Decimal("1"))) == "100.00"


def test_a_zero_or_negative_rate_is_refused_rather_than_dividing(db):
    """Guarding the division rather than letting it raise ZeroDivisionError or
    produce a negative salary. The constraint exists in the database too."""
    for bad in (Decimal("0"), Decimal("-83.45")):
        with pytest.raises(ValueError):
            to_usd(Decimal("1000.000"), bad)


def test_conversion_never_returns_zero_for_a_real_salary(db):
    """A tiny amount against a huge rate must not quietly round to 0.00 and pass the
    amount > 0 check in the database as a USD figure of nothing."""
    with pytest.raises(ValueError):
        to_usd(Decimal("0.001"), Decimal("100000.00000000"))


def test_a_missing_rate_surfaces_as_422_not_500(db, rates):
    """It is a request that cannot be processed, not a fault in the service. The
    caller - or the importer, row by row - has to be told which rate to add."""
    with pytest.raises(MissingFxRate) as caught:
        get_rate(db, "XAF", date(2026, 3, 1))

    assert caught.value.status_code == 422
    assert caught.value.currency == "XAF"


def test_a_real_rate_supersedes_the_seeded_one_without_deleting_it(db, rates):
    """Migration 0002 seeds one indicative rate per currency, dated 2020-01-01.

    What makes that survivable is this: "most recent on or before" means a rate an
    admin enters later wins from its own date onward, with nothing removed. The seed
    is a floor, not a wrong answer that sticks.
    """
    seeded = db.scalar(
        select(FxRate.units_per_usd).where(
            FxRate.currency == "INR", FxRate.as_of == date(2020, 1, 1)
        )
    )

    assert get_rate(db, "INR", date(2020, 6, 1)) == seeded  # only the seed exists yet
    assert get_rate(db, "INR", date(2026, 3, 1)) == Decimal("83.45000000")  # fixture wins


def test_usd_is_seeded_at_one(db):
    """The reporting currency against itself. The lookup short-circuits USD before it
    queries, so this asserts the table is self-describing - not that anything reads
    it. A USD row of anything but 1 would be a trap for whoever queries fx_rate
    directly."""
    seeded = db.scalar(select(FxRate.units_per_usd).where(FxRate.currency == "USD"))

    assert seeded == Decimal("1.00000000")
    assert get_rate(db, "USD", date(2026, 3, 1)) == Decimal("1")


def test_every_seeded_currency_has_a_rate(db):
    """A currency the UI offers and the lookup then refuses is a dead end the user
    cannot diagnose."""
    missing = db.scalars(
        select(Currency.code).where(Currency.code.not_in(select(FxRate.currency).distinct()))
    ).all()

    assert not missing
