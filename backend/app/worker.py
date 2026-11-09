"""The thing that drains the import queue.

One background task, started with the app, doing one job at a time. No broker, no
worker process, no result store: the queue is the `import_job` table and the lock is
`SELECT ... FOR UPDATE SKIP LOCKED`. That is enough for a queue which is empty
almost always and holds one or two files when it is not, and it leaves nothing to
deploy that could be down while the API is up.

What it is *not* is a general job system. There is no retry, no priority, no
scheduling, and a job that fails stays failed - the user fixes the spreadsheet and
uploads again, which is the correct remedy for the only failure that actually
happens. Adding any of those before something needs them would be guessing.

Each cycle is a transaction of its own. Claiming commits immediately, so PROCESSING
is visible to a user refreshing the page while the file is still being applied -
without that commit the status would appear only after the work finished, which is
the one moment nobody is watching.
"""

from __future__ import annotations

import asyncio
import logging

from app.db import SessionLocal
from app.services import importer

log = logging.getLogger("salary.worker")

# How long to wait when the queue is empty. Short enough that an upload starts
# almost at once, long enough that an idle service is not asking a question every
# few milliseconds forever.
IDLE_SECONDS = 1.0

# How long to wait after a cycle raised. Longer than the idle poll on purpose: if
# the database is unreachable, retrying every second fills the log with the same
# line and does nothing useful.
ERROR_SECONDS = 5.0


def run_one() -> bool:
    """Claim and process a single job. True if there was one.

    Synchronous and self-contained, with its own session: this is what runs on a
    worker thread, and sharing a session with the request that queued the job would
    mean the import's transaction and a request's transaction were the same one.
    """
    with SessionLocal() as db:
        job = importer.claim(db)
        if job is None:
            return False
        # Committed before the work starts, so PROCESSING is visible while the file
        # is being applied rather than only once it is over.
        db.commit()

        job_id = job.id
        importer.process(db, job)
        db.commit()
        log.info("import_processed", extra={"import_job_id": job_id, "status": job.status})
        return True


def recover() -> int:
    """Requeue anything a stopped worker left PROCESSING. Called once, at startup.

    Safe because an import is one transaction: a container that died mid-file
    applied none of it, so re-running the job is the same as running it.
    """
    with SessionLocal() as db:
        count = importer.requeue_abandoned(db)
        db.commit()
        return count


async def pump(stop: asyncio.Event) -> None:
    """Drain the queue until asked to stop.

    The work runs in a thread because the database layer is synchronous; doing it
    on the event loop would block every request in the process for the length of an
    import, which is the opposite of the point.
    """
    log.info("worker_started")
    while not stop.is_set():
        try:
            did_work = await asyncio.to_thread(run_one)
        except Exception:  # noqa: BLE001 - the pump must outlive any one failure
            log.exception("worker_cycle_failed")
            await _sleep_or_stop(stop, ERROR_SECONDS)
            continue

        # Straight back round when there was work: a queue of three files should
        # not take three seconds of waiting to get through.
        if not did_work:
            await _sleep_or_stop(stop, IDLE_SECONDS)
    log.info("worker_stopped")


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep, but wake immediately on shutdown rather than making it wait."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
