"""Ten thousand employees, so the performance numbers are measured rather than claimed.

The brief names 10,000 employees across multiple countries. A dashboard that is fast
on the twenty rows a developer typed in by hand has been tested on nothing, so this
builds the volume the brief describes and `tests/test_performance.py` measures against
it.

Three properties the generated data has to have, or the measurement is worthless:

  * **the real shape.** One open salary row per active employee, closed history behind
    it, leavers with no open row at all. The dashboard's cost is dominated by the
    partial index on the open row, and a dataset where every row is open would make
    that index look free.
  * **spread across the dimensions the UI filters on.** All the countries, departments,
    bands and currencies, unevenly - a dataset where every employee is in one country
    makes a country filter look like a no-op.
  * **determinism.** A fixed RNG seed, so two runs produce identical data and two
    measurements are comparable. A benchmark over data that changes between runs
    measures the data.

Bulk INSERT rather than the employee/salary services. Those enforce rules this data
is built to satisfy by construction - and 10,000 round trips through them takes
roughly a minute, which is a test nobody runs. The FX conversion is the exception:
it goes through `fx.to_usd`, because a stored USD figure that disagrees with the one
the application would have computed is the one defect this data could hide.

Usage:

    python -m scripts.seed              # 10,000 employees, committed
    python -m scripts.seed --employees 500
    python -m scripts.seed --clear      # remove what a previous run inserted
    python -m scripts.seed --csv out.csv  # an import file, nothing written to the db
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from app.models import Band, Currency, Department, Employee, Salary
from app.services import fx
from app.services.importer import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

# Every generated employee carries this prefix, which is what makes --clear able to
# remove exactly the rows this script created and nothing a human entered.
CODE_PREFIX = "SEED-"

# Fixed, so the dataset is reproducible. Changing it changes every measurement.
RNG_SEED = 20261108

# Country to currency: the currency an employing entity pays in. Both sides exist in
# migration 0002, and the script fails loudly below if that ever stops being true.
COUNTRY_CURRENCY: dict[str, str] = {
    "US": "USD",
    "CA": "CAD",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    "IE": "EUR",
    "IN": "INR",
    "SG": "SGD",
    "AU": "AUD",
    "JP": "JPY",
    "BR": "BRL",
    "AE": "AED",
}

# Headcount weights. Real companies are lopsided - a dataset with 833 employees in
# each of twelve countries would make every country-filtered query cost the same,
# which is not the question the dashboard asks.
COUNTRY_WEIGHTS: dict[str, int] = {
    "IN": 30,
    "US": 25,
    "GB": 8,
    "DE": 6,
    "CA": 5,
    "SG": 5,
    "AU": 4,
    "FR": 4,
    "IE": 4,
    "BR": 4,
    "JP": 3,
    "AE": 2,
}

DEPARTMENT_WEIGHTS: dict[str, int] = {
    "Engineering": 30,
    "Sales": 12,
    "Customer Success": 10,
    "Operations": 8,
    "Product": 7,
    "Data": 6,
    "Marketing": 6,
    "Finance": 5,
    "People": 5,
    "IT": 5,
    "Design": 4,
    "Legal": 2,
}

# Band ranks, pyramid-shaped: more juniors than principals.
BAND_WEIGHTS: dict[int, int] = {1: 14, 2: 22, 3: 26, 4: 18, 5: 11, 6: 6, 7: 3}

# Base annual pay in USD by band rank, before jitter. Indicative figures - the point
# is a realistic spread for the median and the high/low cards, not a pay scale.
USD_BY_RANK: dict[int, int] = {
    1: 35_000,
    2: 55_000,
    3: 80_000,
    4: 110_000,
    5: 150_000,
    6: 200_000,
    7: 280_000,
}

TITLE_BY_RANK: dict[int, str] = {
    1: "Associate",
    2: "Analyst",
    3: "Engineer",
    4: "Senior Engineer",
    5: "Staff Engineer",
    6: "Principal Engineer",
    7: "Director",
}

FIRST_NAMES = [
    "Priya",
    "Arjun",
    "Mei",
    "Liam",
    "Sofia",
    "Omar",
    "Hannah",
    "Diego",
    "Yuki",
    "Noah",
    "Amara",
    "Lucas",
    "Ines",
    "Tomas",
    "Zara",
    "Ethan",
    "Nadia",
    "Felix",
]
LAST_NAMES = [
    "Sharma",
    "Okafor",
    "Nguyen",
    "Muller",
    "Rossi",
    "Dubois",
    "Silva",
    "Tanaka",
    "Novak",
    "Haddad",
    "Kowalski",
    "Andersen",
    "Costa",
    "Ivanov",
    "Reyes",
    "Fischer",
]

# Salary history. Most people have had one or two raises; a few have had none.
RAISE_COUNT_WEIGHTS: dict[int, int] = {1: 25, 2: 40, 3: 25, 4: 10}

# Roughly one in twenty has left. Leavers are the reason the dashboard filters on
# status at all, so a dataset without them does not exercise the filter.
INACTIVE_SHARE = 0.05

# Hiring window. The floor is deliberate: migration 0002 dates its FX rates at
# 2020-01-01, so a salary effective before that has no rate and could not have been
# written by the application either.
FX_FLOOR = date(2020, 1, 2)
HIRE_LATEST = date(2025, 12, 31)

# Batch size for the bulk inserts. Large enough that the round trips disappear,
# small enough that neither side has to hold 10,000 rows of parameters at once.
BATCH = 1_000


@dataclass(frozen=True)
class SeedResult:
    """What was written. Returned rather than printed, so the test can assert on it."""

    employees: int
    inactive: int
    salaries: int
    open_salaries: int


class _Reference(NamedTuple):
    """The reference rows the generated data points at, read once."""

    departments: dict[str, int]  # name -> id
    bands: dict[int, int]  # rank -> id
    minor_units: dict[str, int]  # currency -> decimal places
    rates: dict[str, Decimal]  # currency -> units per USD


def _reference(db: Session) -> _Reference:
    """Read the reference data, and refuse to invent any of it.

    Every currency, department and band this script uses must already exist. Creating
    them here would mean a seeded database differs from a migrated one, and the
    difference would show up as a dashboard that groups by rows no deployment has.
    """
    departments = {name: id_ for id_, name in db.execute(select(Department.id, Department.name))}
    bands = {rank: id_ for id_, rank in db.execute(select(Band.id, Band.rank))}
    minor_units = dict(db.execute(select(Currency.code, Currency.minor_units)).all())

    missing = sorted(set(DEPARTMENT_WEIGHTS) - set(departments))
    if missing:
        raise RuntimeError(f"Departments missing from the database: {missing}. Run the migrations.")
    missing_bands = sorted(set(BAND_WEIGHTS) - set(bands))
    if missing_bands:
        raise RuntimeError(f"Band ranks missing from the database: {missing_bands}.")

    # One rate lookup per currency, through the service, so a missing rate raises
    # MissingFxRate here rather than silently producing a salary at parity.
    rates = {
        currency: fx.get_rate(db, currency, FX_FLOOR) for currency in set(COUNTRY_CURRENCY.values())
    }
    return _Reference(departments, bands, minor_units, rates)


def _weighted(rng: random.Random, weights: dict):
    """One key, chosen in proportion to its weight."""
    return rng.choices(list(weights), weights=list(weights.values()), k=1)[0]


def _local_amount(usd: int, rate: Decimal, places: int) -> Decimal:
    """USD figure to the local currency, at the currency's own precision.

    JPY has no minor units and INR has two; storing 1234.56 yen would be a number no
    payroll system could pay out.
    """
    quantum = Decimal(1).scaleb(-places)
    return (Decimal(usd) * rate).quantize(quantum)


def populate(
    db: Session,
    *,
    actor_id: int,
    employees: int = 10_000,
    rng_seed: int = RNG_SEED,
) -> SeedResult:
    """Generate `employees` people with their salary history, in this session.

    Writes, flushes and does not commit - the caller decides. That is what lets the
    same function serve the CLI, which commits, and the test fixture, which rolls
    back. A function that committed could not be used by the test at all.
    """
    reference = _reference(db)
    rng = random.Random(rng_seed)

    employee_rows: list[dict] = []
    # Parallel to employee_rows, holding what the salary rows need and the employee
    # table does not store.
    plans: list[tuple[str, int, date, date | None]] = []  # code, rank, hire, termination

    for i in range(employees):
        country = _weighted(rng, COUNTRY_WEIGHTS)
        rank = _weighted(rng, BAND_WEIGHTS)
        code = f"{CODE_PREFIX}{i:06d}"

        hire_date = FX_FLOOR + timedelta(days=rng.randint(0, (HIRE_LATEST - FX_FLOOR).days))
        left = rng.random() < INACTIVE_SHARE
        # A leaver left after they were hired, and not in the future.
        termination = hire_date + timedelta(days=rng.randint(180, 2_000)) if left else None
        if termination and termination > HIRE_LATEST:
            termination = HIRE_LATEST

        employee_rows.append(
            {
                "employee_code": code,
                "first_name": rng.choice(FIRST_NAMES),
                "last_name": rng.choice(LAST_NAMES),
                "email": f"{code.lower()}@acme.test",
                "country_code": country,
                "department_id": reference.departments[_weighted(rng, DEPARTMENT_WEIGHTS)],
                "job_title": TITLE_BY_RANK[rank],
                "band_id": reference.bands[rank],
                "status": "INACTIVE" if termination else "ACTIVE",
                "hire_date": hire_date,
                "termination_date": termination,
            }
        )
        plans.append((code, rank, hire_date, termination))

    # sort_by_parameter_order is what makes the returned ids line up with the rows
    # that produced them. Without it the mapping below is correct only by luck.
    ids: list[int] = []
    for start in range(0, len(employee_rows), BATCH):
        batch = employee_rows[start : start + BATCH]
        ids.extend(
            db.scalars(insert(Employee).returning(Employee.id, sort_by_parameter_order=True), batch)
        )

    salary_rows: list[dict] = []
    open_rows = 0
    for employee_id, row, (_, rank, hire_date, termination) in zip(
        ids, employee_rows, plans, strict=True
    ):
        currency = COUNTRY_CURRENCY[row["country_code"]]
        rate = reference.rates[currency]
        places = reference.minor_units[currency]

        # One row per raise, each closing the one before it at the day before the new
        # one starts - the same effective-dating the salary service writes.
        dates = [hire_date]
        for _ in range(_weighted(rng, RAISE_COUNT_WEIGHTS) - 1):
            nxt = dates[-1] + timedelta(days=rng.randint(300, 500))
            if termination and nxt >= termination:
                break
            if nxt > HIRE_LATEST:
                break
            dates.append(nxt)

        usd = int(USD_BY_RANK[rank] * rng.uniform(0.82, 1.18))
        for position, effective_from in enumerate(dates):
            last = position == len(dates) - 1
            if last:
                # A leaver's final row is closed on the day they left; everyone else's
                # stays open, which is the row every report reads.
                effective_to = termination
            else:
                effective_to = dates[position + 1] - timedelta(days=1)

            amount = _local_amount(usd, rate, places)
            salary_rows.append(
                {
                    "employee_id": employee_id,
                    "amount": amount,
                    "currency": currency,
                    "usd_amount": fx.to_usd(amount, rate),
                    "fx_units_per_usd": rate,
                    "fx_rate_date": FX_FLOOR,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    # The vocabulary ck_salary_change_reason allows. Inventing a
                    # value here is what the constraint exists to stop.
                    "change_reason": "NEW_HIRE" if position == 0 else "MERIT",
                    "created_by": actor_id,
                }
            )
            if last and effective_to is None:
                open_rows += 1
            # Each raise is worth something; a history where the figure never moves
            # makes a salary-change timeline that shows nothing.
            usd = int(usd * rng.uniform(1.04, 1.15))

    for start in range(0, len(salary_rows), BATCH):
        db.execute(insert(Salary), salary_rows[start : start + BATCH])

    db.flush()
    return SeedResult(
        employees=len(employee_rows),
        inactive=sum(1 for row in employee_rows if row["termination_date"]),
        salaries=len(salary_rows),
        open_salaries=open_rows,
    )


# The import file's columns: what the importer requires, plus the two it accepts.
# Taken from the importer rather than typed again, so a column added there turns up
# here instead of silently going missing from every test file.
CSV_COLUMNS = (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)


def csv_rows(
    db: Session,
    *,
    employees: int = 10_000,
    rng_seed: int = RNG_SEED,
) -> list[dict[str, str]]:
    """The same people `populate` builds, shaped as import rows.

    Reference data is still read from the database - countries, departments, bands
    and FX rates - because a file naming a department ACME does not have is a file
    that tests the error path rather than the import. Nothing is written.

    One salary each rather than a history: an import file carries the pay someone is
    on now, which is what the importer takes.
    """
    reference = _reference(db)
    rng = random.Random(rng_seed)

    rows: list[dict[str, str]] = []
    for i in range(employees):
        country = _weighted(rng, COUNTRY_WEIGHTS)
        rank = _weighted(rng, BAND_WEIGHTS)
        currency = COUNTRY_CURRENCY[country]
        rate = reference.rates[currency]
        # A different prefix from CODE_PREFIX: a file reusing the codes `populate`
        # wrote would fail on duplicates against the database instead of importing.
        code = f"BULK-{i:06d}"
        hire_date = FX_FLOOR + timedelta(days=rng.randint(0, (HIRE_LATEST - FX_FLOOR).days))
        usd = int(USD_BY_RANK[rank] * rng.uniform(0.82, 1.18))

        rows.append(
            {
                "employee_code": code,
                "first_name": rng.choice(FIRST_NAMES),
                "last_name": rng.choice(LAST_NAMES),
                "email": f"{code.lower()}@acme.test",
                "country_code": country,
                "department": _weighted(rng, DEPARTMENT_WEIGHTS),
                "job_title": TITLE_BY_RANK[rank],
                "band": f"L{rank}",
                "hire_date": hire_date.isoformat(),
                "amount": str(_local_amount(usd, rate, reference.minor_units[currency])),
                "currency": currency,
                # On or after the hire date, and after the earliest FX rate, or the
                # row has no rate to convert at.
                "effective_from": max(hire_date, FX_FLOOR).isoformat(),
            }
        )
    return rows


def write_csv(handle, rows: list[dict[str, str]]) -> None:
    """Write the rows as a .csv the import screen will take.

    `csv.DictWriter` rather than joining on commas: a name like `Rao, Anita` written
    by hand splits one cell into two, and every column after it shifts.
    """
    writer = csv.DictWriter(
        handle,
        fieldnames=list(CSV_COLUMNS),
        # The generator produces every column; this makes that a guarantee rather
        # than something that fails at write time with a KeyError.
        extrasaction="ignore",
        restval="",
    )
    writer.writeheader()
    writer.writerows(rows)


def clear(db: Session) -> int:
    """Remove what a previous run inserted, and nothing else.

    Keyed on the code prefix rather than truncating the tables: a developer who has
    been clicking around the UI has rows in there worth keeping, and a seed script
    that wipes them is one nobody runs twice.
    """
    deleted = db.execute(
        text(
            "DELETE FROM salary WHERE employee_id IN "
            "(SELECT id FROM employee WHERE employee_code LIKE :prefix)"
        ),
        {"prefix": f"{CODE_PREFIX}%"},
    ).rowcount
    deleted += db.execute(
        text("DELETE FROM employee WHERE employee_code LIKE :prefix"),
        {"prefix": f"{CODE_PREFIX}%"},
    ).rowcount
    return deleted


def _actor(db: Session) -> int:
    """The app_user every generated row is attributed to.

    Migration 0002 seeds `system:seed` for exactly this: rows that no human wrote
    still have to name someone, and inventing a plausible human in the audit trail
    is worse than naming the process that did it.
    """
    actor_id = db.execute(
        text("SELECT id FROM app_user WHERE external_id = 'system:seed'")
    ).scalar_one_or_none()
    if actor_id is None:
        raise RuntimeError("app_user 'system:seed' is missing. Run the migrations first.")
    return actor_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--employees", type=int, default=10_000)
    parser.add_argument("--clear", action="store_true", help="delete seeded rows and exit")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete seeded rows before inserting, so re-running is idempotent",
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="write an import file instead of inserting rows. Nothing is written to "
        "the database - the file is for testing the Import screen.",
    )
    args = parser.parse_args(argv)

    # Imported here rather than at module scope: importing this module must not open
    # a database connection, since the test imports it to call populate() against a
    # session it already owns.
    from app.db import SessionLocal

    with SessionLocal() as db:
        if args.csv:
            # Reads reference data, writes nothing. The file is the output.
            rows = csv_rows(db, employees=args.employees)
            with open(args.csv, "w", newline="") as handle:
                write_csv(handle, rows)
            print(f"Wrote {len(rows)} rows to {args.csv}.")
            return 0

        if args.clear:
            removed = clear(db)
            db.commit()
            print(f"Removed {removed} seeded rows.")
            return 0

        if args.replace:
            clear(db)

        result = populate(db, actor_id=_actor(db), employees=args.employees)
        db.commit()

    print(
        f"Seeded {result.employees} employees "
        f"({result.inactive} inactive) and {result.salaries} salary rows, "
        f"{result.open_salaries} of them open."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
