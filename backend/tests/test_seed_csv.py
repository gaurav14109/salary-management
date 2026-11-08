"""The dummy-data file: 10,000 employees as an import file rather than as rows.

`populate` builds people straight into the database, which is what the performance
tests measure against. This builds the same people as a .csv - the thing you can
actually drop on the Import screen, which is the only way to exercise the upload
path end to end at the size the brief names.

One generator, two outputs. A second set of constants describing "realistic
employees" would drift from the first, and the day it did, the import tests and the
query tests would be measuring different companies.
"""

import csv
import io

from scripts.seed import CODE_PREFIX, csv_rows, write_csv


def rows(db, count: int = 25) -> list[dict[str, str]]:
    return csv_rows(db, employees=count)


def test_the_header_is_exactly_what_the_importer_requires(db):
    """A file whose columns the importer rejects is a file that tests nothing."""
    from app.services.importer import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

    produced = set(rows(db)[0])

    assert set(REQUIRED_COLUMNS) <= produced
    assert produced <= set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS)


def test_it_produces_the_number_of_rows_asked_for(db):
    assert len(rows(db, 40)) == 40


def test_every_row_has_a_distinct_employee_code(db):
    """Duplicate codes inside one file are a validation error, so a generator that
    produced them would fail the import for a reason that is not the test."""
    produced = rows(db, 200)

    assert len({row["employee_code"] for row in produced}) == 200


def test_codes_do_not_collide_with_the_database_seed(db):
    """`scripts.seed` writes SEED- rows. A file reusing those codes would fail on
    duplicates against the database rather than importing."""
    assert not rows(db)[0]["employee_code"].startswith(CODE_PREFIX)


def test_it_is_deterministic(db):
    """Two runs, the same file. A dummy file that changes every time makes one
    import run incomparable with the next."""
    assert rows(db, 30) == rows(db, 30)


def test_amounts_respect_the_currency_minor_units(db):
    """JPY has none. `1234.56` yen is a figure no payroll system could pay out, and
    the import would store it."""
    produced = [row for row in rows(db, 400) if row["currency"] == "JPY"]

    assert produced, "no JPY rows generated - widen the sample"
    assert all("." not in row["amount"] for row in produced)


def test_every_row_validates_against_the_real_database(db):
    """The point of the whole file. Countries, departments, bands, currencies and FX
    dates all have to be ones this database actually has - which is why the rows are
    generated from reference data rather than from a list typed here.
    """
    from app.services.importer import parse, validate

    text = io.StringIO()
    write_csv(text, rows(db, 50))
    parsed = parse(io.BytesIO(text.getvalue().encode()), "dummy.csv")

    valid, errors = validate(db, parsed)

    assert errors == []
    assert len(valid) == 50


def test_write_csv_quotes_a_value_that_would_split_a_cell(db):
    """Generated names are plain, but the writer is the thing being used - it has to
    be correct for whatever it is handed."""
    text = io.StringIO()
    write_csv(text, [{"employee_code": "E-1", "first_name": "Rao, Anita"}])

    parsed = list(csv.DictReader(io.StringIO(text.getvalue())))
    assert parsed[0]["first_name"] == "Rao, Anita"
