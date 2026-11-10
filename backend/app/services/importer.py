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
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import PayloadTooLarge, SalaryError, UnprocessableEntity
from app.models import Band, Country, Currency, Department, Employee, FxRate, ImportJob
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

    # Only once both are known good: "no rate for (blank) as of None" helps nobody,
    # and the currency and date errors above already say what is actually wrong.
    if currency in lookups.currencies and effective_from is not None:
        earliest = lookups.earliest_rate.get(currency)
        if earliest is None or effective_from < earliest:
            # Worded exactly as MissingFxRate words it, because the import raises
            # that on the same condition. Two messages for one problem would read as
            # two problems.
            errors.append(
                RowError(n, "currency", f"No FX rate for {currency} as of {effective_from}.")
            )

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
        # The earliest rate on file per currency. `fx.get_rate` takes the most recent
        # rate on or before a date, so a salary effective before this one has no rate
        # and cannot be converted. One grouped query rather than a lookup per row:
        # fx_rate holds a handful of rows per currency and the file holds thousands.
        self.earliest_rate = {
            currency: as_of
            for currency, as_of in db.execute(
                select(FxRate.currency, func.min(FxRate.as_of)).group_by(FxRate.currency)
            )
        }


# --- pass three: apply ----------------------------------------------------------


def queue(db: Session, file: BinaryIO, filename: str, *, actor_id: int) -> ImportJob:
    """Record the file and return. Nothing is parsed, validated or applied.

    This is the whole of what an upload request now does, and the reason it returns
    in milliseconds rather than the 72 seconds a 10,000-row file took when the work
    happened inline (ADR-0004, revised).

    Deliberately not parsed here. Rejecting a broken file in the request would read
    as helpful and would mean a 20,000-row spreadsheet is read twice - once to
    reject it, once to apply it - putting the slowest part of a bad upload back in
    front of the user. It fails in the worker instead, with a reason attached.
    """
    job = ImportJob(
        filename=filename,
        status="PENDING",
        payload=file.read(),
        created_by=actor_id,
    )
    db.add(job)
    db.flush()
    log.info("import_queued", extra={"import_job_id": job.id, "bytes": len(job.payload or b"")})
    return job


# The whole application's import queue, as one advisory lock. An arbitrary constant;
# what matters is that every replica uses the same one.
#
# Why a lock at all, when claiming already uses FOR UPDATE SKIP LOCKED: SKIP LOCKED
# stops two workers taking the *same* job. It does nothing to stop them taking two
# *different* jobs and applying both at once - which is what happens the moment the
# API runs more than one replica. Two imports interleaving would each have "is this
# employee code already taken?" answered against a database the other is halfway
# through changing.
#
# So "one file at a time" needs a lock over the queue, not over a row.
QUEUE_LOCK = 0x5A1A47


def acquire_queue_lock(db: Session) -> bool:
    """Take the right to process imports, or return False if another worker has it.

    Session-level, not transaction-level, and the distinction is load-bearing:
    `run_one` commits after claiming so PROCESSING is visible while the file is being
    applied, and a transaction-level lock would be released by exactly that commit -
    freeing the next worker halfway through the import. The bug would only appear
    under concurrency, which is the worst place to find one.

    `try_` rather than the blocking form: a worker that cannot have the lock should
    go back to sleep, not queue up behind the one that does.

    Held by the database connection, so a container that dies releases it without
    anything having to notice. No lease, no expiry, no stuck queue.
    """
    return bool(db.scalar(select(func.pg_try_advisory_lock(QUEUE_LOCK))))


def release_queue_lock(db: Session) -> None:
    """Give it back. Safe to call when the lock is not held."""
    db.scalar(select(func.pg_advisory_unlock(QUEUE_LOCK)))


def claim(db: Session) -> ImportJob | None:
    """Take the oldest queued job, or return None if there is nothing to do.

    `FOR UPDATE SKIP LOCKED` stops two workers taking the *same* row: a second worker
    skips a row another already holds rather than blocking on it or picking it up
    twice. Without SKIP LOCKED the second worker waits for the first to finish and
    then processes the same job again.

    It does *not* make "one file at a time" true on its own - two workers would
    simply take two different jobs. That guarantee comes from `acquire_queue_lock`
    above, which is held across the whole cycle.

    The claim is a write, so the caller owns the transaction - committing is what
    publishes PROCESSING to everyone else.
    """
    job = db.scalars(
        select(ImportJob)
        .where(ImportJob.status == "PENDING")
        .order_by(ImportJob.created_at, ImportJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None

    job.status = "PROCESSING"
    job.started_at = datetime.now(UTC)
    db.flush()
    log.info("import_claimed", extra={"import_job_id": job.id})
    return job


def process(db: Session, job: ImportJob | None) -> ImportJob | None:
    """Apply a claimed job, whatever happens.

    Never raises. A worker that dies on a file it cannot read stops the queue for
    everybody behind it, so every failure becomes a reason on the job row - which
    is also the thing the user came back to read.
    """
    if job is None:
        return None

    try:
        rows = parse(io.BytesIO(job.payload or b""), job.filename)
    except (SalaryError, ValueError, KeyError, TypeError) as exc:
        # The file itself is unusable. One sentence, not a traceback: the user can
        # act on "that is not a spreadsheet" and cannot act on a stack.
        return _abandon(db, job, _reason(exc))

    try:
        return _apply_rows(db, job, rows)
    except SalaryError as exc:
        return _abandon(db, job, str(exc))
    except Exception as exc:  # noqa: BLE001 - the queue must survive any one file
        log.exception("import_crashed", extra={"import_job_id": job.id})
        return _abandon(db, job, _reason(exc))


def _apply_rows(db: Session, job: ImportJob, rows: list[RawRow]) -> ImportJob:
    """Validate and apply, with the same all-or-nothing rule as before."""
    valid, errors = validate(db, rows)
    job.total_rows = len(rows)

    if errors:
        return _finish(db, job, status="FAILED", errors=errors, counts=Counts())

    counts = Counts()
    try:
        # One savepoint around the whole file. A failure here - something validation
        # could not see - rolls the file back and leaves the job row to explain why.
        with db.begin_nested():
            for row in valid:
                _apply(db, row, job_id=job.id, actor_id=job.created_by, counts=counts)
    except SalaryError as exc:
        errors = [*counts.errors, RowError(getattr(exc, "row_number", 0), "row", str(exc))]
        return _finish(db, job, status="FAILED", errors=errors, counts=Counts())

    if counts.errors:
        return _finish(db, job, status="FAILED", errors=counts.errors, counts=Counts())

    return _finish(db, job, status="SUCCESS", errors=[], counts=counts)


def _abandon(db: Session, job: ImportJob, reason: str) -> ImportJob:
    """Mark a job failed for a reason that is not a row error."""
    log.warning("import_failed", extra={"import_job_id": job.id, "reason": reason})
    job.failure_reason = reason
    return _finish(db, job, status="FAILED", errors=[], counts=Counts())


def _reason(exc: Exception) -> str:
    """A sentence for the user, from an exception that is not one.

    The type name alone ("UnicodeDecodeError") is meaningless to an HR Manager, and
    the full message can carry file content. This keeps the first line and caps it.
    """
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return (text[:200] + "...") if len(text) > 200 else text


def requeue_abandoned(db: Session) -> int:
    """Put jobs left PROCESSING by a stopped worker back in the queue.

    A container that stops mid-import leaves a row saying PROCESSING and a
    transaction that rolled back - so nothing was applied, and the queue behind it
    never moves again. Called once at startup.

    Safe precisely because the import is one transaction: there is no half-applied
    file to reason about, so re-running it is the same as running it.
    """
    jobs = db.scalars(select(ImportJob).where(ImportJob.status == "PROCESSING")).all()
    for job in jobs:
        job.status = "PENDING"
        job.started_at = None
    if jobs:
        log.warning("imports_requeued", extra={"count": len(jobs)})
    db.flush()
    return len(jobs)


def queued_count(db: Session) -> int:
    """How many files are waiting. The UI shows it so a wait reads as a wait."""
    return (
        db.scalar(select(func.count()).select_from(ImportJob).where(ImportJob.status == "PENDING"))
        or 0
    )


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
    job.finished_at = datetime.now(UTC)
    # The file has been read for the last time. Keeping it would make this table
    # grow by the size of every upload, for bytes nobody will open again - the job
    # row is what has to survive, and it is small.
    job.payload = None
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
