"""Bulk import.

Two properties decide whether this feature is trustworthy, and both are about what
happens when a file is wrong:

  * a file with one bad row commits nothing. Half an import is worse than none,
    because nobody can tell which half;
  * every rejection carries its row number and field, so the person fixes it in
    Excel and re-uploads rather than guessing.

Re-uploading is the normal case, not the exception: HR exports, edits two cells, and
uploads the whole file back. Rows whose pay has not moved must produce no salary row
and no audit entry.
"""

import io
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, text

from app.errors import PayloadTooLarge, UnprocessableEntity
from app.models import Employee, FxRate, Salary
from app.services.importer import parse, run_import, validate

HEADER = (
    "employee_code,first_name,last_name,email,country_code,department,job_title,"
    "band,hire_date,amount,currency,effective_from"
)
ROW = (
    "EMP-7001,Anita,Rao,anita@acme.test,IN,Engineering,Engineer,L3,"
    "2024-04-01,1200000,INR,2026-04-01"
)


def csv_file(*rows: str, header: str = HEADER) -> io.BytesIO:
    body = "\n".join([header, *rows])
    return io.BytesIO(body.encode())


@pytest.fixture
def inr_rate(db, actor_id):
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("83.45000000"),
            as_of=date(2026, 1, 1),
            created_by=actor_id,
        )
    )
    db.flush()


def run(db, actor_id, *rows, filename="people.csv", header=HEADER):
    return run_import(db, csv_file(*rows, header=header), filename, actor_id=actor_id)


# --- parsing --------------------------------------------------------------------


def test_parses_csv_to_rows_with_their_line_numbers(db):
    rows = parse(csv_file(ROW), "people.csv")

    assert len(rows) == 1
    # 2, not 1: the header is line 1, and the number has to match what the user sees
    # in Excel or the error report sends them to the wrong line.
    assert rows[0].row_number == 2
    assert rows[0].values["employee_code"] == "EMP-7001"


def test_parses_xlsx_and_csv_to_the_same_rows(db):
    openpyxl = pytest.importorskip("openpyxl", reason="not installed in this sandbox")

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER.split(","))
    sheet.append(ROW.split(","))
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    from_xlsx = parse(buffer, "people.xlsx")
    from_csv = parse(csv_file(ROW), "people.csv")

    assert [r.values for r in from_xlsx] == [r.values for r in from_csv]


def test_an_unsupported_extension_is_refused(db):
    with pytest.raises(UnprocessableEntity):
        parse(csv_file(ROW), "people.pdf")


def test_a_file_that_is_not_what_its_name_claims_is_refused(db):
    """A .xlsx that is actually text. Refused with a sentence rather than whatever
    the zip reader says."""
    with pytest.raises(UnprocessableEntity):
        parse(io.BytesIO(b"not a workbook"), "people.xlsx")


# --- whole-file failures --------------------------------------------------------


def test_missing_required_column_fails_whole_file_before_any_row(db, actor_id, inr_rate):
    """One error about the file, not one per row. A missing `amount` column would
    otherwise produce ten thousand identical row errors."""
    header = HEADER.replace(",amount", "")
    job = run(db, actor_id, ROW.replace(",1200000", ""), header=header)

    assert job.status == "FAILED"
    assert len(job.errors) == 1
    assert "amount" in job.errors[0]["message"]
    assert job.errors[0]["row_number"] == 0


def test_an_empty_file_is_refused(db, actor_id):
    job = run(db, actor_id)

    assert job.status == "FAILED"
    assert "no rows" in job.errors[0]["message"].lower()


def test_a_file_over_the_row_limit_is_refused_before_parsing_rows(db, actor_id):
    """Twenty thousand is already twice the company. A file larger than that is a
    mistake, and reading it into memory to discover that is the mistake repeated."""
    with pytest.raises(PayloadTooLarge):
        parse(csv_file(*[ROW] * 20_001), "huge.csv")


# --- per-row errors -------------------------------------------------------------


def test_invalid_currency_code_reported_with_row_number(db, actor_id, inr_rate):
    job = run(db, actor_id, ROW.replace(",INR,", ",XYZ,"))

    assert job.status == "FAILED"
    assert job.errors[0]["row_number"] == 2
    assert job.errors[0]["field"] == "currency"


def test_an_unknown_department_is_reported_not_created(db, actor_id, inr_rate):
    """The import never invents reference data. A typo'd department would otherwise
    become a department, and the dashboard would grow a row nobody recognises."""
    job = run(db, actor_id, ROW.replace(",Engineering,", ",Egnineering,"))

    assert job.status == "FAILED"
    assert job.errors[0]["field"] == "department"
    assert (
        db.execute(text("SELECT count(*) FROM department WHERE name = 'Egnineering'")).scalar_one()
        == 0
    )


def test_missing_fx_rate_reported_as_row_error_not_exception(db, actor_id):
    """Review Focus #1. The file is not broken - one currency has no rate - so the
    user gets a row error naming it, not a 500."""
    db.execute(text("DELETE FROM fx_rate WHERE currency = 'INR'"))
    db.flush()

    job = run(db, actor_id, ROW)

    assert job.status == "FAILED"
    assert job.errors[0]["field"] == "currency"
    assert "INR" in job.errors[0]["message"]


def test_a_malformed_date_is_reported_with_its_field(db, actor_id, inr_rate):
    job = run(db, actor_id, ROW.replace(",2026-04-01", ",01/04/2026"))

    assert job.errors[0]["field"] == "effective_from"


def test_a_negative_amount_is_reported(db, actor_id, inr_rate):
    job = run(db, actor_id, ROW.replace(",1200000,", ",-5,"))

    assert job.errors[0]["field"] == "amount"


def test_a_duplicate_employee_code_within_one_file_is_reported(db, actor_id, inr_rate):
    """Two rows for one person is ambiguous - which salary wins? Refused rather than
    resolved by row order, which would make the answer depend on how Excel sorted."""
    job = run(db, actor_id, ROW, ROW)

    assert job.status == "FAILED"
    assert any("duplicate" in e["message"].lower() for e in job.errors)


def test_every_bad_row_is_reported_not_just_the_first(db, actor_id, inr_rate):
    """One round trip per error would make a ten-error file a ten-upload afternoon."""
    job = run(
        db,
        actor_id,
        ROW.replace(",INR,", ",XYZ,"),
        ROW.replace("EMP-7001", "EMP-7002").replace(",1200000,", ",-5,"),
    )

    assert len({e["row_number"] for e in job.errors}) == 2


# --- atomicity ------------------------------------------------------------------


def test_file_with_one_bad_row_commits_nothing(db, actor_id, inr_rate):
    """Review Focus #2. Half an import is worse than none: nobody can tell which half
    landed, and the fix is a manual reconciliation of ten thousand rows."""
    job = run(
        db,
        actor_id,
        ROW,
        ROW.replace("EMP-7001", "EMP-7002").replace(",INR,", ",XYZ,"),
    )

    assert job.status == "FAILED"
    assert db.query(Employee).filter(Employee.employee_code.like("EMP-70%")).count() == 0
    assert db.query(Salary).count() == 0


def test_the_job_row_survives_a_failed_import(db, actor_id, inr_rate):
    """The data rolls back; the record of the attempt must not. Otherwise a user who
    uploaded a bad file has nothing to look at."""
    job = run(db, actor_id, ROW.replace(",INR,", ",XYZ,"))

    assert job.id is not None
    assert (
        db.execute(text("SELECT status FROM import_job WHERE id = :i"), {"i": job.id}).scalar_one()
        == "FAILED"
    )


# --- the happy path, and re-uploading -------------------------------------------


def test_a_valid_file_creates_employees_and_salaries(db, actor_id, inr_rate):
    job = run(db, actor_id, ROW)

    assert job.status == "SUCCESS"
    assert job.employees_created == 1
    assert job.salaries_created == 1
    assert job.success_rows == 1


def test_re_uploading_an_unchanged_file_writes_no_salary_row(db, actor_id, inr_rate):
    """The property that makes this feature safe to use twice. HR exports, edits two
    cells and uploads the whole file back - the untouched rows must be no-ops."""
    run(db, actor_id, ROW)
    before = db.query(Salary).count()

    # Byte-identical to the first upload, which is what a re-export actually is.
    job = run(db, actor_id, ROW)

    assert job.status == "SUCCESS"
    assert job.salaries_unchanged == 1
    assert job.salaries_created == 0
    assert db.query(Salary).count() == before


def test_an_existing_employee_is_updated_not_duplicated(db, actor_id, inr_rate):
    run(db, actor_id, ROW)

    job = run(db, actor_id, ROW.replace(",Engineer,", ",Senior Engineer,"))

    assert job.employees_updated == 1
    assert job.employees_created == 0
    assert db.query(Employee).filter_by(employee_code="EMP-7001").one().job_title == (
        "Senior Engineer"
    )


def test_a_raise_in_the_file_closes_the_previous_row(db, actor_id, inr_rate):
    run(db, actor_id, ROW)

    job = run(
        db, actor_id, ROW.replace(",1200000,", ",1400000,").replace(",2026-04-01", ",2026-10-01")
    )

    assert job.salaries_created == 1
    closed = db.execute(
        text("SELECT effective_to FROM salary WHERE amount = 1200000 ")
    ).scalar_one()
    assert closed == date(2026, 9, 30)


def test_the_import_is_attributed_to_the_person_who_uploaded_it(db, actor_id, inr_rate):
    job = run(db, actor_id, ROW)

    assert job.created_by == actor_id
    rows = (
        db.execute(text("SELECT import_job_id FROM salary WHERE import_job_id IS NOT NULL"))
        .scalars()
        .all()
    )
    assert rows == [job.id]


def test_imported_salaries_carry_the_import_change_reason(db, actor_id, inr_rate):
    run(db, actor_id, ROW)

    reason = db.execute(text("SELECT change_reason FROM salary")).scalar_one()
    assert reason == "IMPORT"


# --- scale ----------------------------------------------------------------------


@pytest.mark.perf
def test_a_valid_file_of_10000_rows_commits_all(db, actor_id, inr_rate):
    """The size the brief actually names. Synchronous is a defensible choice only if
    this is seconds, not minutes - and only if it is all one transaction."""
    rows = [
        f"EMP-9{i:05d},First{i},Last{i},p{i}@acme.test,IN,Engineering,Engineer,L3,"
        f"2024-04-01,{1000000 + i},INR,2026-04-01"
        for i in range(10_000)
    ]

    job = run(db, actor_id, *rows)

    assert job.status == "SUCCESS"
    assert job.employees_created == 10_000
    assert job.salaries_created == 10_000
    assert db.query(Employee).filter(Employee.employee_code.like("EMP-9%")).count() == 10_000


@pytest.mark.perf
def test_one_bad_row_in_ten_thousand_still_commits_nothing(db, actor_id, inr_rate):
    """The property that matters most at scale: a single typo on line 7,431 must not
    leave 7,430 rows applied and the rest not."""
    rows = [
        f"EMP-9{i:05d},First{i},Last{i},p{i}@acme.test,IN,Engineering,Engineer,L3,"
        f"2024-04-01,{1000000 + i},INR,2026-04-01"
        for i in range(10_000)
    ]
    rows[7_430] = rows[7_430].replace(",INR,", ",XYZ,")

    job = run(db, actor_id, *rows)

    assert job.status == "FAILED"
    assert job.errors[0]["row_number"] == 7_432  # header + zero-indexed
    assert db.query(Employee).filter(Employee.employee_code.like("EMP-9%")).count() == 0


# --- the check the import makes and validation used not to ----------------------


def valid_file(*rows: str) -> list:
    return parse(io.BytesIO("\n".join([HEADER, *rows]).encode()), "x.csv")


EARLY = "EMP-7801,A,B,b@acme.test,IN,Engineering,Engineer,L3,2019-04-01,1200000,INR,2019-06-01"


def test_validation_rejects_a_salary_dated_before_any_fx_rate(db, actor_id, inr_rate):
    """`validate` has to find every error the import would, or a preview lies.

    The import catches MissingFxRate while applying a row and turns it into a row
    error. Validation did not look at FX at all, so a file whose dates predate the
    rates passed validation and failed on apply - survivable while the only caller
    was the import itself, and not once a preview screen is telling somebody their
    file is clean.
    """
    _, errors = validate(db, valid_file(EARLY))

    assert [(e.field, e.message) for e in errors] == [
        ("currency", "No FX rate for INR as of 2019-06-01.")
    ]


def test_validation_accepts_a_date_on_the_rate_itself(db, actor_id, inr_rate):
    """`get_rate` takes the most recent rate on or before the date, so the rate's own
    date is valid. An off-by-one here would reject the first day of every rate."""
    on_the_day = EARLY.replace("2019-04-01", "2020-01-01").replace("2019-06-01", "2020-01-01")

    _, errors = validate(db, valid_file(on_the_day))

    assert errors == []


def test_the_fx_check_costs_one_query_for_the_whole_file(db, actor_id, inr_rate):
    """Per row would be a query per row - ten thousand round trips to read a table
    with one row per currency."""
    rows = valid_file(
        *(
            EARLY.replace("EMP-7801", f"EMP-79{i:03d}").replace("b@acme", f"b{i}@acme")
            for i in range(50)
        )
    )

    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        validate(db, rows)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)

    fx_queries = [s for s in seen if "fx_rate" in s.lower()]
    assert len(fx_queries) <= 1, f"{len(fx_queries)} FX queries for 50 rows"
