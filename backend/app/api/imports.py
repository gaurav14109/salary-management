"""Upload endpoints.

The upload is synchronous: the response is the finished result, not a job id to poll.
10,000 rows is a few seconds, and a queue would add a broker, a worker, a result
store and a polling UI to avoid a wait nobody has complained about (ADR-0004).

`GET /imports/{id}` exists anyway, because the record of an attempt outlives the
request that made it - and after a failure it is the thing the user reads.
"""

import io
import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.access import Role, require_role
from app.db import get_db
from app.deps import current_actor
from app.errors import NotFound, PayloadTooLarge, UnprocessableEntity
from app.models import AppUser
from app.schemas import Page
from app.schemas.imports import ImportJobRead, ImportPreview, ImportPreviewRow
from app.services import importer

router = APIRouter(prefix="/imports", tags=["imports"])
log = logging.getLogger("salary.imports")

# 20,000 rows of this shape is well under 10 MB. The cap is on bytes as well as rows
# because the row count is only knowable after reading, and reading is the thing
# being limited.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# How many rows the preview will hand back - the same ceiling the import itself has,
# so any file that can be imported can be seen in full first. That is only safe
# because the grid mounts a window of rows rather than all of them; rendering 20,000
# rows outright was measured at 41 seconds followed by the browser running out of
# memory. Past this the response is explicitly `truncated`, so the page can say the
# preview is a sample rather than quietly showing part of a file as though it were
# all of it.
PREVIEW_MAX_ROWS = importer.MAX_ROWS


@router.post(
    "",
    response_model=ImportJobRead,
    # 202, not 201: the file has been accepted and nothing has been applied yet.
    # 201 would claim a resource exists that does not - no employee was created by
    # this request, and may never be if the file turns out to be wrong.
    status_code=status.HTTP_202_ACCEPTED,
    # Writing, so the HR manager only. Admin reads.
    dependencies=[Depends(require_role(Role.HR_MANAGER))],
)
async def upload(
    file: UploadFile = File(description=".csv or .xlsx"),
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> ImportJobRead:
    """Queue a file for import. Returns immediately.

    The work happens in a background worker, one file at a time (ADR-0004, revised).
    A 10,000-row import takes about 72 seconds; doing that inside the request put it
    past every default HTTP timeout, so the upload that mattered most was the one
    most likely to be cut off - with the import still running, invisibly, behind it.

    The response is a job in PENDING. Poll `GET /imports/{id}` for PROCESSING, then
    SUCCESS or FAILED. Nothing here parses or validates the file: rejecting a broken
    one in the request would read as helpful and would mean reading a 20,000-row
    spreadsheet twice. It fails in the worker, with a reason on the job row.
    """
    if not file.filename:
        raise UnprocessableEntity("No file was uploaded.")

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(
            f"That file is {len(body) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    job = importer.queue(db, io.BytesIO(body), file.filename, actor_id=actor.id)

    log.info(
        "import_queued",
        extra={
            "import_job_id": job.id,
            # Not "filename": logging owns that name on every LogRecord - it means
            # the source file that logged the line - and makeRecord raises rather
            # than let extra= overwrite one. The crash only appears when the logger
            # is enabled for INFO, so it reached the container and not the tests.
            "source_filename": file.filename,
            "bytes": len(body),
            "status": job.status,
            "actor_id": actor.id,
        },
    )
    return ImportJobRead.model_validate(job)


@router.post(
    "/preview",
    response_model=ImportPreview,
    # With the upload, not with the reads: a preview nobody is allowed to act on is
    # a button that fails at the last step.
    dependencies=[Depends(require_role(Role.HR_MANAGER))],
)
async def preview(
    file: UploadFile = File(description=".csv or .xlsx"),
    db: Session = Depends(get_db),
) -> ImportPreview:
    """Parse and check a file. Write nothing.

    Deliberately not `run_import(dry_run=True)`. A write path with a flag that
    sometimes turns the writes off is one wrong default away from importing during a
    preview, and the flag would have to be threaded through every function the import
    touches. This calls `parse` and `validate` - the same two the import calls before
    it writes anything - and then stops, which is a guarantee made of structure
    rather than of a boolean.

    No ImportJob row either: nothing was attempted, so there is nothing to record.
    The job row exists to outlive a real attempt.
    """
    if not file.filename:
        raise UnprocessableEntity("No file was uploaded.")

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(
            f"That file is {len(body) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    rows = importer.parse(io.BytesIO(body), file.filename)
    return _preview(db, rows)


def _preview(db: Session, rows: list[importer.RawRow]) -> ImportPreview:
    """The shared half: validate, truncate for display, count honestly.

    `row_count` and `error_count` describe the whole file; `rows` is only what the
    grid gets. Counting the truncated list instead would under-report a long file,
    which is the one number a user is deciding on.
    """
    _, errors = importer.validate(db, rows)

    return ImportPreview(
        # From the file rather than from REQUIRED_COLUMNS, so a column the file has
        # and the importer ignores is still visible to whoever is reading the grid.
        columns=list(rows[0].values) if rows else list(importer.REQUIRED_COLUMNS),
        rows=[
            ImportPreviewRow(row_number=row.row_number, values=row.values)
            for row in rows[:PREVIEW_MAX_ROWS]
        ],
        errors=[error.as_dict() for error in errors],
        row_count=len(rows),
        error_count=len(errors),
        truncated=len(rows) > PREVIEW_MAX_ROWS,
    )


@router.get(
    "/queue",
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def queue_depth(db: Session = Depends(get_db)) -> dict:
    """How many files are waiting, so a wait reads as a wait rather than a hang."""
    return {"pending": importer.queued_count(db)}


@router.get(
    "/{job_id}",
    response_model=ImportJobRead,
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def get_import(job_id: int, db: Session = Depends(get_db)) -> ImportJobRead:
    job = importer.get_job(db, job_id)
    if job is None:
        raise NotFound(f"No import with id {job_id}.")
    return ImportJobRead.model_validate(job)


@router.get(
    "",
    response_model=Page[ImportJobRead],
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def list_imports(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[ImportJobRead]:
    """Upload history, newest first. Who uploaded what, and what it did."""
    items, total = importer.list_jobs(db, page=page, size=size)
    return Page[ImportJobRead](
        items=[ImportJobRead.model_validate(job) for job in items],
        total=total,
        page=page,
        size=size,
    )
