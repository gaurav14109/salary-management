"""Dashboard aggregates.

Every figure is in USD, summed from `salary.usd_amount` - the column frozen at write
time. Nothing here converts anything: correcting an FX rate must never rewrite a
quarter Finance has already received.

Every figure also reads `current_salary`, the view that excludes closed rows and
terminated employees. Total payroll that counts leavers is overstated permanently,
with nothing raising.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import FxRate
from app.services.analytics import summary
from app.services.employee import create, set_status
from app.services.salary import set_salary


@pytest.fixture
def rates(db, actor_id):
    for code, units in (("INR", "83.45000000"), ("EUR", "0.92000000")):
        db.add(
            FxRate(
                currency=code,
                units_per_usd=Decimal(units),
                as_of=date(2026, 1, 1),
                created_by=actor_id,
            )
        )
    db.flush()


@pytest.fixture
def payroll(db, reference_data, actor_id, rates):
    """Four people: two countries, two bands, two currencies.

    Deliberately small and hand-checkable - an aggregate test whose expected numbers
    were produced by the code it is testing proves nothing.
    """
    band_l3 = reference_data["band_id"]
    band_l5 = db.execute(text("SELECT id FROM band WHERE rank = 5")).scalar_one()
    design = db.execute(text("SELECT id FROM department WHERE lower(name) = 'design'")).scalar_one()
    engineering = reference_data["department_id"]

    people = [
        # code,     country, dept,        band,    amount,      currency, usd
        ("EMP-A", "IN", engineering, band_l3, "835000.000", "INR"),  # 10,005.99
        ("EMP-B", "IN", engineering, band_l5, "1670000.000", "INR"),  # 20,011.98
        ("EMP-C", "DE", design, band_l3, "46000.000", "EUR"),  # 50,000.00
        ("EMP-D", "DE", design, band_l5, "92000.000", "EUR"),  # 100,000.00
    ]
    made = {}
    for code, country, dept, band, amount, currency in people:
        person = create(
            db,
            employee_code=code,
            first_name=code[-1],
            last_name="Person",
            email=f"{code.lower()}@acme.test",
            country_code=country,
            department_id=dept,
            job_title="Engineer",
            band_id=band,
            hire_date=date(2024, 4, 1),
            actor_id=actor_id,
        )
        set_salary(
            db,
            employee_id=person.id,
            amount=Decimal(amount),
            currency=currency,
            effective_from=date(2026, 4, 1),
            actor_id=actor_id,
        )
        made[code] = person
    return made


# --- the headline numbers -------------------------------------------------------


def test_employee_count_is_people_with_current_pay(db, payroll):
    assert summary(db).employee_count == 4


def test_total_payroll_is_the_sum_of_the_frozen_usd_amounts(db, payroll):
    """10,005.99 + 20,011.98 + 50,000.00 + 100,000.00, computed by hand."""
    assert summary(db).total_payroll_usd == Decimal("180017.97")


def test_average_is_the_total_over_the_count(db, payroll):
    assert summary(db).average_salary_usd == Decimal("45004.49")


def test_median_is_the_midpoint_not_the_mean(db, payroll):
    """Four salaries: the median is between the 2nd and 3rd, 20,011.98 and 50,000.
    The mean is 45,004.49 - the two differ, which is the point of reporting both.
    A single outlier moves the mean and leaves the median alone."""
    assert summary(db).median_salary_usd == Decimal("35005.99")


def test_highest_and_lowest_name_the_person(db, payroll):
    result = summary(db)

    assert result.highest.employee_code == "EMP-D"
    assert result.highest.usd_amount == Decimal("100000.00")
    assert result.lowest.employee_code == "EMP-A"


def test_an_empty_payroll_reports_zeroes_not_an_error(db, reference_data):
    """A dashboard on a fresh database must render. Nulls from SUM and no row from
    MAX are the normal state on day one, not a failure."""
    result = summary(db)

    assert result.employee_count == 0
    assert result.total_payroll_usd == Decimal("0.00")
    assert result.average_salary_usd == Decimal("0.00")
    assert result.highest is None
    assert result.lowest is None
    assert result.by_country == []


# --- group-bys ------------------------------------------------------------------


def test_by_country_totals_in_usd(db, payroll):
    by_country = {row.country_code: row for row in summary(db).by_country}

    assert by_country["DE"].total_usd == Decimal("150000.00")
    assert by_country["IN"].total_usd == Decimal("30017.97")
    assert by_country["IN"].employee_count == 2


def test_by_country_carries_the_name_not_just_the_code(db, payroll):
    """A chart labelled 'DE' is a chart nobody reads."""
    names = {row.country_code: row.name for row in summary(db).by_country}

    assert names["DE"] == "Germany"


def test_by_country_is_ordered_by_spend(db, payroll):
    """Largest first: the dashboard's first row should be the one worth looking at."""
    assert [row.country_code for row in summary(db).by_country] == ["DE", "IN"]


def test_by_band_is_ordered_by_rank_not_name(db, payroll, reference_data, actor_id):
    """Sorted as text, bands read L1, L10, L11, L2 - and an average-pay-by-band chart
    in that order is meaningless. band.rank exists for exactly this.

    L10 is added here deliberately. With only L3 and L5 in the fixture, name order
    and rank order coincide and this test passes whichever one the code uses -
    verified by switching the service to ORDER BY name and watching it stay green.
    A test that cannot fail is not a test.
    """
    band_l10 = db.execute(
        text("INSERT INTO band (name, rank) VALUES ('L10', 10) RETURNING id")
    ).scalar_one()
    person = create(
        db,
        employee_code="EMP-Z",
        first_name="Z",
        last_name="Person",
        email="z@acme.test",
        country_code="IN",
        department_id=reference_data["department_id"],
        job_title="Principal",
        band_id=band_l10,
        hire_date=date(2024, 4, 1),
        actor_id=actor_id,
    )
    set_salary(
        db,
        employee_id=person.id,
        amount=Decimal("8345000.000"),
        currency="INR",
        effective_from=date(2026, 4, 1),
        actor_id=actor_id,
    )

    names = [row.name for row in summary(db).by_band]

    assert names == ["L3", "L5", "L10"]
    assert names != sorted(names)  # text order would be L10, L3, L5


def test_by_band_averages_in_usd(db, payroll):
    """L3 holds EMP-A (10,005.99) and EMP-C (50,000.00).

    The average is 30,002.995, which rounds half-up to 30,003.00. Banker's rounding -
    Python's default - would give 30,002.99 instead, and a payroll report that rounds
    differently from the finance team's spreadsheet is a report nobody trusts.
    """
    by_band = {row.name: row for row in summary(db).by_band}

    assert by_band["L3"].average_usd == Decimal("30003.00")
    assert by_band["L3"].employee_count == 2


def test_by_department_groups_as_well(db, payroll):
    by_department = {row.name: row for row in summary(db).by_department}

    assert by_department["Engineering"].employee_count == 2
    assert by_department["Design"].total_usd == Decimal("150000.00")


def test_currency_breakdown_shows_what_people_are_actually_paid_in(db, payroll):
    """The one place a local figure belongs on the dashboard: "how much of payroll is
    exposed to the rupee" is a question USD totals cannot answer."""
    breakdown = {row.currency: row for row in summary(db).currency_breakdown}

    assert breakdown["INR"].employee_count == 2
    assert breakdown["INR"].total_local == Decimal("2505000.000")
    assert breakdown["INR"].total_usd == Decimal("30017.97")


# --- what it excludes -----------------------------------------------------------


def test_a_terminated_employee_leaves_the_totals(db, payroll, actor_id):
    """Otherwise total payroll is overstated by every leaver, permanently, and the
    number drifts further from reality every quarter."""
    set_status(
        db,
        payroll["EMP-D"].id,
        status="INACTIVE",
        termination_date=date(2026, 9, 30),
        actor_id=actor_id,
    )

    result = summary(db)

    assert result.employee_count == 3
    assert result.total_payroll_usd == Decimal("80017.97")


def test_a_superseded_salary_row_is_not_counted(db, payroll, actor_id):
    """Only the open row. Counting history would double a person for every raise
    they have ever had."""
    set_salary(
        db,
        employee_id=payroll["EMP-A"].id,
        amount=Decimal("1670000.000"),
        currency="INR",
        effective_from=date(2026, 10, 1),
        actor_id=actor_id,
    )

    result = summary(db)

    assert result.employee_count == 4
    assert result.total_payroll_usd == Decimal("190023.96")


def test_an_employee_with_no_salary_is_not_counted(db, payroll, reference_data, actor_id):
    """They are on the list - that is where someone goes to enter their pay - but
    they are not payroll until they have some."""
    create(
        db,
        employee_code="EMP-E",
        first_name="E",
        last_name="Person",
        email="e@acme.test",
        country_code="IN",
        department_id=reference_data["department_id"],
        job_title="Engineer",
        hire_date=date(2024, 4, 1),
        actor_id=actor_id,
    )

    assert summary(db).employee_count == 4


# --- filters --------------------------------------------------------------------


def test_a_country_filter_applies_to_every_figure(db, payroll):
    """The headline numbers and the charts must describe the same set, or the page
    contradicts itself."""
    result = summary(db, country="DE")

    assert result.employee_count == 2
    assert result.total_payroll_usd == Decimal("150000.00")
    assert [row.country_code for row in result.by_country] == ["DE"]
    assert sum(row.employee_count for row in result.by_band) == 2


def test_filters_combine_as_and(db, payroll, reference_data):
    result = summary(db, country="IN", department=reference_data["department_id"])

    assert result.employee_count == 2


def test_a_filter_matching_nobody_reports_zeroes(db, payroll):
    result = summary(db, country="JP")

    assert result.employee_count == 0
    assert result.highest is None


def test_an_unknown_sort_of_filter_is_not_silently_ignored(db, payroll):
    """A filter the service does not understand must not return an unfiltered page
    that looks filtered."""
    with pytest.raises(TypeError):
        summary(db, departement=1)  # deliberate typo
