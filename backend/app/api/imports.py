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
from app.schemas.imports import ImportJobRead
from app.services import importer

router = APIRouter(prefix="/imports", tags=["imports"])
log = logging.getLogger("salary.imports")

# 20,000 rows of this shape is well under 10 MB. The cap is on bytes as well as rows
# because the row count is only knowable after reading, and reading is the thing
# being limited.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post(
    "",
    response_model=ImportJobRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
async def upload(
    file: UploadFile = File(description=".csv or .xlsx"),
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> ImportJobRead:
    """Import a file, atomically.

    Returns 201 whether the import succeeded or failed: the ImportJob was created
    either way, and its `status` and `errors` say what happened. A 4xx here would
    mean "we could not record your attempt", which is a different and worse thing
    than "your file had errors".
    """
    if not file.filename:
        raise UnprocessableEntity("No file was uploaded.")

    body = await file.read()
    if len(body) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(
            f"That file is {len(body) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )

    job = importer.run_import(db, io.BytesIO(body), file.filename, actor_id=actor.id)

    log.info(
        "import_uploaded",
        extra={
            "import_job_id": job.id,
            "filename": file.filename,
            "bytes": len(body),
            "status": job.status,
            "actor_id": actor.id,
        },
    )
    return ImportJobRead.model_validate(job)


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
