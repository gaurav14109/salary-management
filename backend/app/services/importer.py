"""Bulk import of people and pay.

Two passes, and the split is the whole design:

  1. parse and validate everything in memory, writing nothing;
  2. apply, only if there is not a single error.

A file with one bad row commits nothing. Half an import is worse than none, because
nobody can tell which half landed, and the fix is reconciling ten thousand rows by
hand.

Every write goes through `employee` and `salary`, never straight to the tables. That
is why an imported salary freezes its FX rate, writes an audit row and refuses a
backdate exactly like one typed into the UI - the importer cannot forget a rule it
does not implement.

Synchronous on purpose. 10,000 rows is a few megabytes and a few seconds; a job
queue would add a broker, a worker, a result store and a polling UI to avoid a wait
nobody has complained about (ADR-0004).
"""

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import PayloadTooLarge, SalaryError, UnprocessableEntity
from app.models import Band, Country, Currency, Department, Employee, ImportJob
from app.services import employee as employee_service
from app.services import salary as salary_service

log = logging.getLogger("salary.importer")

REQUIRED_COLUMNS = (
    "employee_code",
    "first_name",
    "last_name",
    "country_code",
    "department",
    "job_title",
    "hire_date",
    "amount",
    "currency",
    "effective_from",
)
OPTIONAL_COLUMNS = ("email", "band")

# Twice the company. A larger file is a mistake, and reading it to find that out is
# the mistake repeated.
MAX_ROWS = 20_000

# The header is line 1 in every spreadsheet, so data starts at 2. Errors must point
# at the line the user sees, or they send someone to the wrong row.
FIRST_DATA_ROW = 2


@dataclass(frozen=True)
class RawRow:
    """One line, as read. No types yet - everything is still a string."""

    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class RowError:
    row_number: int
    field: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row_number": self.row_number, "field": self.field, "message": self.message}


@dataclass
class ValidRow:
    """A row that passed every check that can be made without writing."""

    row_number: int
    employee_code: str
    first_name: str
    last_name: str
    email: str | None
    country_code: str
    department_id: int
    job_title: str
    band_id: int | None
    hire_date: date
    amount: Decimal
    currency: str
    effective_from: date


@dataclass
class Counts:
    employees_created: int = 0
    employees_updated: int = 0
    salaries_created: int = 0
    salaries_unchanged: int = 0
    errors: list[RowError] = field(default_factory=list)


# --- pass one: read ------------------------------------------------------------


def parse(file: BinaryIO, filename: str) -> list[RawRow]:
    """Read a .csv or .xlsx into rows of strings.

    Format is chosen by extension rather than by sniffing: HR knows what they
    exported, and a file whose contents disagree with its name is a problem worth
    surfacing rather than working around.
    """
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "csv":
        return _parse_csv(file)
    if suffix in ("xlsx", "xlsm"):
        return _parse_xlsx(file)
    raise UnprocessableEntity(f"Cannot read .{suffix} files. Upload a .csv or .xlsx.")


def _parse_csv(file: BinaryIO) -> list[RawRow]:
    text = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    rows: list[RawRow] = []
    for offset, record in enumerate(reader):
        _refuse_oversize(len(rows))
        rows.append(
            RawRow(
                row_number=FIRST_DATA_ROW + offset,
                values={_key(k): _clean(v) for k, v in record.items() if k},
            )
        )
    return rows


def _parse_xlsx(file: BinaryIO) -> list[RawRow]:
    try:
        import openpyxl
    except ModuleNotFoundError as exc:  # pragma: no cover - a packaging failure
        raise UnprocessableEntity("Excel support is unavailable. Upload a .csv.") from exc

    try:
        # read_only streams rather than building the whole sheet in memory;
        # data_only takes the cached value of a formula instead of its text.
        workbook = openpyxl.load_workbook(file, read_only=True, data_only=True)
    except Exception as exc:
        raise UnprocessableEntity("That file is not a readable Excel workbook.") from exc

    sheet = workbook.worksheets[0]
    rows_iter = sheet.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if header is None:
        return []

    columns = [_key(str(cell)) if cell is not None else "" for cell in header]
    rows: list[RawRow] = []
    for offset, cells in enumerate(rows_iter):
        if all(cell is None or str(cell).strip() == "" for cell in cells):
            continue  # trailing blank rows are what Excel leaves behind, not data
        _refuse_oversize(len(rows))
        rows.append(
            RawRow(
                row_number=FIRST_DATA_ROW + offset,
                values={
                    column: _clean(_cell_text(value))
                    for column, value in zip(columns, cells, strict=False)
                    if column
                },
            )
        )
    workbook.close()
    return rows


def _cell_text(value: Any) -> str:
    """Excel hands back typed values. Dates especially: a cell formatted as a date
    arrives as a datetime, and str() on one gives '2026-04-01 00:00:00'."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _refuse_oversize(seen: int) -> None:
    if seen >= MAX_ROWS:
        raise PayloadTooLarge(f"That file has more than {MAX_ROWS:,} rows.")


def _key(column: str) -> str:
    return column.strip().lower().replace(" ", "_")


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


# --- pass two: check ------------------------------------------------------------


def validate(db: Session, rows: list[RawRow]) -> tuple[list[ValidRow], list[RowError]]:
    """Check every row, collecting errors rather than stopping at the first.

    One round trip per error would make a ten-error file a ten-upload afternoon.
    """
    if not rows:
        return [], [RowError(0, "file", "That file has no rows.")]

    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0].values]
    if missing:
        # One error about the file, not one per row: a missing `amount` column would
        # otherwise produce ten thousand identical messages.
        return [], [RowError(0, "file", f"Missing required column(s): {', '.join(missing)}.")]

    lookups = _Lookups(db)
    valid: list[ValidRow] = []
    errors: list[RowError] = []
    seen_codes: dict[str, int] = {}

    for row in rows:
        row_errors: list[RowError] = []
        code = row.values.get("employee_code", "")

        if not code:
            row_errors.append(RowError(row.row_number, "employee_code", "Required."))
        elif code in seen_codes:
            # Two rows for one person is ambiguous - which salary wins? Refused
            # rather than resolved by row order, which would make the answer depend
            # on how Excel happened to sort.
            row_errors.append(
                RowError(
                    row.row_number,
                    "employee_code",
                    f"Duplicate of row {seen_codes[code]}: {code} appears twice.",
                )
            )

        parsed = _parse_row(row, lookups, row_errors)
        if row_errors:
            errors.extend(row_errors)
            continue

        seen_codes[code] = row.row_number
        valid.append(parsed)

    return valid, errors


def _parse_row(row: RawRow, lookups: "_Lookups", errors: list[RowError]) -> ValidRow | None:
    """Type and resolve one row. Every failure appends rather than raises, so one bad
    field does not hide the next."""
    values = row.values
    n = row.row_number

    hire_date = _as_date(values.get("hire_date"), n, "hire_date", errors)
    effective_from = _as_date(values.get("effective_from"), n, "effective_from", errors)
    amount = _as_amount(values.get("amount"), n, errors)

    currency = (values.get("currency") or "").upper()
    if currency not in lookups.currencies:
        errors.append(RowError(n, "currency", f"Unknown currency: {currency or '(blank)'}."))

    country = (values.get("country_code") or "").upper()
    if country not in lookups.countries:
        errors.append(RowError(n, "country_code", f"Unknown country: {country or '(blank)'}."))

    department_name = values.get("department", "")
    department_id = lookups.departments.get(department_name.lower())
    if department_id is None:
        # The import never creates reference data. A typo'd department would
        # otherwise become a department, and the dashboard would grow a row nobody
        # recognises.
        errors.append(
            RowError(n, "department", f"Unknown department: {department_name or '(blank)'}.")
        )

    band_name = values.get("band", "")
    band_id = None
    if band_name:
        band_id = lookups.bands.get(band_name.lower())
        if band_id is None:
            errors.append(RowError(n, "band", f"Unknown band: {band_name}."))

    for required in ("first_name", "last_name", "job_title"):
        if not values.get(required):
            errors.append(RowError(n, required, "Required."))

    if errors:
        return None

    return ValidRow(
        row_number=n,
        employee_code=values["employee_code"],
        first_name=values["first_name"],
        last_name=values["last_name"],
        email=values.get("email") or None,
        country_code=country,
        department_id=department_id,
        job_title=values["job_title"],
        band_id=band_id,
        hire_date=hire_date,
        amount=amount,
        currency=currency,
        effective_from=effective_from,
    )


def _as_date(raw: str | None, n: int, field_name: str, errors: list[RowError]) -> date | None:
    if not raw:
        errors.append(RowError(n, field_name, "Required."))
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        # ISO only. Accepting 01/04/2026 would mean guessing between the 1st of April
        # and the 4th of January, and guessing wrong is silent.
        errors.append(RowError(n, field_name, f"Use YYYY-MM-DD, got {raw!r}."))
        return None


def _as_amount(raw: str | None, n: int, errors: list[RowError]) -> Decimal | None:
    if not raw:
        errors.append(RowError(n, "amount", "Required."))
        return None
    try:
        # Thousands separators are what a spreadsheet exports; the currency symbol is
        # what someone types. Neither changes the number.
        amount = Decimal(raw.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        errors.append(RowError(n, "amount", f"Not a number: {raw!r}."))
        return None
    if amount <= 0:
        errors.append(RowError(n, "amount", f"Must be positive, got {amount}."))
        return None
    return amount


class _Lookups:
    """Reference data, read once.

    A query per row would be four thousand queries for a thousand-row file. These
    tables are small and static enough that holding them in memory for one import is
    obviously correct.
    """

    def __init__(self, db: Session) -> None:
        self.currencies = set(db.scalars(select(Currency.code)))
        self.countries = set(db.scalars(select(Country.code)))
        self.departments = {
            name.lower(): id_
            for id_, name in db.execute(
                select(Department.id, Department.name).where(Department.is_active)
            )
        }
        self.bands = {
            name.lower(): id_
            for id_, name in db.execute(select(Band.id, Band.name).where(Band.is_active))
        }


# --- pass three: apply ----------------------------------------------------------


def run_import(db: Session, file: BinaryIO, filename: str, *, actor_id: int) -> ImportJob:
    """Parse, validate, and apply if and only if nothing is wrong.

    The ImportJob row is written either way. The data rolls back on failure; the
    record of the attempt must not, or someone who uploaded a bad file has nothing
    to look at.
    """
    rows = parse(file, filename)
    valid, errors = validate(db, rows)

    job = ImportJob(
        filename=filename,
        status="PENDING",
        total_rows=len(rows),
        created_by=actor_id,
    )
    db.add(job)
    db.flush()

    if errors:
        return _finish(db, job, status="FAILED", errors=errors, counts=Counts())

    counts = Counts()
    try:
        # One savepoint around every row. A failure here - something validation could
        # not see, like a salary that would be backdated - rolls back the whole file
        # and leaves the job row behind to explain why.
        with db.begin_nested():
            for row in valid:
                _apply(db, row, job_id=job.id, actor_id=actor_id, counts=counts)
    except SalaryError as exc:
        errors = [*counts.errors, RowError(getattr(exc, "row_number", 0), "row", str(exc))]
        return _finish(db, job, status="FAILED", errors=errors, counts=Counts())

    if counts.errors:
        return _finish(db, job, status="FAILED", errors=counts.errors, counts=Counts())

    return _finish(db, job, status="SUCCESS", errors=[], counts=counts)


def _apply(db: Session, row: ValidRow, *, job_id: int, actor_id: int, counts: Counts) -> None:
    """One row, through the same services the UI uses.

    Nothing here writes a table directly. That is what makes an imported salary
    freeze its rate, write its audit row and refuse a backdate exactly like one typed
    into a form.
    """
    existing = db.scalar(select(Employee).where(Employee.employee_code == row.employee_code))

    if existing is None:
        person = employee_service.create(
            db,
            employee_code=row.employee_code,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            country_code=row.country_code,
            department_id=row.department_id,
            job_title=row.job_title,
            band_id=row.band_id,
            hire_date=row.hire_date,
            actor_id=actor_id,
        )
        person.import_job_id = job_id
        counts.employees_created += 1
    else:
        person = existing
        changes = {
            "first_name": row.first_name,
            "last_name": row.last_name,
            "email": row.email,
            "country_code": row.country_code,
            "department_id": row.department_id,
            "job_title": row.job_title,
            "band_id": row.band_id,
        }
        before = {f: getattr(person, f) for f in changes}
        employee_service.update(db, person.id, changes=changes, actor_id=actor_id)
        if before != {f: getattr(person, f) for f in changes}:
            counts.employees_updated += 1

    try:
        result = salary_service.set_salary(
            db,
            employee_id=person.id,
            amount=row.amount,
            currency=row.currency,
            effective_from=row.effective_from,
            change_reason="IMPORT",
            actor_id=actor_id,
        )
    except SalaryError as exc:
        # Caught per row so the report names the line. A missing FX rate is the
        # common case: the file is fine, one currency has no rate.
        counts.errors.append(RowError(row.row_number, _field_for(exc), str(exc)))
        return

    if result.changed:
        result.salary.import_job_id = job_id
        counts.salaries_created += 1
    else:
        # Pay has not moved. No row, no audit entry - this is what makes re-uploading
        # an exported file safe.
        counts.salaries_unchanged += 1


def _field_for(exc: SalaryError) -> str:
    """Point the error at the cell the user has to edit."""
    if hasattr(exc, "currency"):
        return "currency"
    if hasattr(exc, "effective_from"):
        return "effective_from"
    return "amount"


def _finish(
    db: Session,
    job: ImportJob,
    *,
    status: str,
    errors: list[RowError],
    counts: Counts,
) -> ImportJob:
    job.status = status
    job.errors = [e.as_dict() for e in errors]
    job.error_rows = len({e.row_number for e in errors})
    job.success_rows = job.total_rows - job.error_rows if status == "SUCCESS" else 0
    job.employees_created = counts.employees_created
    job.employees_updated = counts.employees_updated
    job.salaries_created = counts.salaries_created
    job.salaries_unchanged = counts.salaries_unchanged
    db.flush()

    log.info(
        "import_finished",
        extra={
            "import_job_id": job.id,
            "status": status,
            "total_rows": job.total_rows,
            "error_rows": job.error_rows,
            "actor_id": job.created_by,
        },
    )
    return job


def get_job(db: Session, job_id: int) -> ImportJob | None:
    return db.get(ImportJob, job_id)


def list_jobs(db: Session, *, page: int = 1, size: int = 20) -> tuple[list[ImportJob], int]:
    total = db.scalar(select(func.count()).select_from(ImportJob)) or 0
    items = list(
        db.scalars(
            select(ImportJob)
            .order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return items, total
