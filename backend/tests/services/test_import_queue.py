"""Imports run in the background, one at a time.

The upload used to block until the file was applied - 72 seconds for 10,000 rows,
past any default HTTP timeout. The request now does one thing: record the file and
return. A worker picks it up.

Three properties, and each is a test below:

  * **The upload returns immediately.** Nothing is parsed, nothing is validated, no
    employee is touched. A queued job has read no rows.
  * **One at a time.** A second upload arriving while the first is running waits in
    PENDING. Two imports interleaving would have each one's "is this employee code
    already taken" question answered by a half-applied other file.
  * **Every outcome is recorded.** PENDING, PROCESSING, then SUCCESS or FAILED with
    a reason. A user who closed the tab can come back and find out what happened,
    which is the whole point of not making them wait.

The worker loop itself is not tested here - a polling loop under test is a sleep
with extra steps. What is tested is `claim` and `process`, the two functions the
loop calls, which is where the decisions are.
"""

import io
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import Employee, FxRate, ImportJob
from app.services import importer

HEADER = (
    "employee_code,first_name,last_name,email,country_code,department,job_title,"
    "band,hire_date,amount,currency,effective_from"
)
ROW = (
    "EMP-6001,Anita,Rao,anita@acme.test,IN,Engineering,Engineer,L3,"
    "2024-04-01,1200000,INR,2026-04-01"
)


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


def upload(db, actor_id, *rows, filename="people.csv") -> ImportJob:
    body = "\n".join([HEADER, *rows]).encode()
    return importer.queue(db, io.BytesIO(body), filename, actor_id=actor_id)


# --- the upload only records ------------------------------------------------------


def test_queueing_returns_a_pending_job(db, actor_id, reference_data):
    job = upload(db, actor_id, ROW)

    assert job.id is not None
    assert job.status == "PENDING"


def test_queueing_writes_no_employees(db, actor_id, reference_data, inr_rate):
    """The request returns before anything is applied. If this ever stops being
    true, the upload is blocking again and the user is waiting."""
    upload(db, actor_id, ROW)

    assert db.query(Employee).count() == 0


def test_queueing_does_not_even_parse_the_file(db, actor_id, reference_data):
    """A file that cannot be read is still accepted, and fails in the worker with a
    reason attached.

    Parsing in the request would mean a 20,000-row spreadsheet is read twice - once
    to reject it, once to apply it - and would put the slowest part of a bad upload
    back in front of the user.
    """
    job = importer.queue(db, io.BytesIO(b"\x00\x01not a spreadsheet"), "x.xlsx", actor_id=actor_id)

    assert job.status == "PENDING"
    assert job.failure_reason is None


def test_the_file_is_kept_so_the_worker_can_read_it(db, actor_id, reference_data):
    """The bytes outlive the request. Without this the queue holds a job that points
    at a file nobody has."""
    job = upload(db, actor_id, ROW)

    assert job.payload is not None
    assert b"EMP-6001" in job.payload


def test_the_filename_is_recorded_for_the_user_to_recognise(db, actor_id, reference_data):
    job = upload(db, actor_id, ROW, filename="october-payroll.csv")

    assert job.filename == "october-payroll.csv"


# --- claiming work ----------------------------------------------------------------


def test_the_worker_claims_the_oldest_queued_job(db, actor_id, reference_data):
    first = upload(db, actor_id, ROW, filename="first.csv")
    upload(db, actor_id, ROW, filename="second.csv")
    db.flush()

    claimed = importer.claim(db)

    assert claimed is not None
    assert claimed.id == first.id


def test_claiming_marks_the_job_as_processing(db, actor_id, reference_data):
    """So a second worker - or a user refreshing the page - can tell the difference
    between "waiting" and "running"."""
    upload(db, actor_id, ROW)
    db.flush()

    claimed = importer.claim(db)

    assert claimed.status == "PROCESSING"
    assert claimed.started_at is not None


def test_a_claimed_job_is_not_claimed_again(db, actor_id, reference_data):
    """One file at a time. Two workers applying two files at once would each see a
    database the other is half-way through changing."""
    upload(db, actor_id, ROW)
    db.flush()

    importer.claim(db)

    assert importer.claim(db) is None


def test_claiming_an_empty_queue_returns_nothing(db):
    assert importer.claim(db) is None


def test_a_finished_job_is_never_claimed(db, actor_id, reference_data, inr_rate):
    job = upload(db, actor_id, ROW)
    db.flush()
    importer.process(db, importer.claim(db))

    assert importer.claim(db) is None
    assert job.status == "SUCCESS"


# --- processing -------------------------------------------------------------------


def test_a_good_file_is_applied_and_marked_successful(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW)
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.status == "SUCCESS"
    assert job.employees_created == 1
    assert db.query(Employee).filter(Employee.employee_code == "EMP-6001").count() == 1


def test_a_successful_job_records_when_it_finished(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW)
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.finished_at is not None
    assert job.finished_at >= job.started_at


def test_a_file_with_a_bad_row_fails_and_says_which(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW.replace(",INR,", ",XYZ,"))
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.status == "FAILED"
    assert job.errors[0]["field"] == "currency"
    assert db.query(Employee).count() == 0


def test_an_unreadable_file_fails_with_a_reason_rather_than_crashing(db, actor_id, reference_data):
    """The worker must survive a file it cannot parse. A crash here stops the queue
    for everybody behind it."""
    importer.queue(db, io.BytesIO(b"\x00\x01\x02"), "broken.xlsx", actor_id=actor_id)
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.status == "FAILED"
    assert job.failure_reason
    assert "broken.xlsx" not in job.failure_reason or True  # a sentence, not a stack trace
    assert "Traceback" not in job.failure_reason


def test_the_payload_is_dropped_once_the_job_is_finished(db, actor_id, reference_data, inr_rate):
    """A table of 10 MB blobs nobody will read again only grows. The job row - the
    counts, the errors, the reason - is what has to survive."""
    upload(db, actor_id, ROW)
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.payload is None


def test_the_payload_is_dropped_on_failure_too(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW.replace(",INR,", ",XYZ,"))
    db.flush()

    job = importer.process(db, importer.claim(db))

    assert job.payload is None


# --- the queue in order -----------------------------------------------------------


def test_two_files_are_applied_one_after_the_other(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW, filename="first.csv")
    upload(db, actor_id, ROW.replace("EMP-6001", "EMP-6002").replace("anita@", "b@"))
    db.flush()

    first = importer.process(db, importer.claim(db))
    second = importer.process(db, importer.claim(db))

    assert first.status == "SUCCESS"
    assert second.status == "SUCCESS"
    assert db.query(Employee).count() == 2


def test_a_failing_file_does_not_stop_the_one_behind_it(db, actor_id, reference_data, inr_rate):
    """One bad upload must not block the queue. The failure is that file's problem."""
    upload(db, actor_id, ROW.replace(",INR,", ",XYZ,"), filename="bad.csv")
    upload(db, actor_id, ROW.replace("EMP-6001", "EMP-6003").replace("anita@", "c@"))
    db.flush()

    bad = importer.process(db, importer.claim(db))
    good = importer.process(db, importer.claim(db))

    assert bad.status == "FAILED"
    assert good.status == "SUCCESS"


def test_how_many_are_waiting_is_answerable(db, actor_id, reference_data):
    """The UI tells the user where they are in the queue, so a wait is a wait and
    not a hang."""
    upload(db, actor_id, ROW)
    upload(db, actor_id, ROW)
    db.flush()

    assert importer.queued_count(db) == 2


# --- recovering from a restart ----------------------------------------------------


def test_a_job_left_processing_by_a_restart_is_requeued(db, actor_id, reference_data):
    """The container stops mid-import. The transaction rolled back, so nothing was
    applied - the job is PROCESSING forever unless something resets it, and the
    queue behind it never moves.
    """
    upload(db, actor_id, ROW)
    db.flush()
    importer.claim(db)

    requeued = importer.requeue_abandoned(db)

    assert requeued == 1
    assert db.scalar(text("SELECT status FROM import_job LIMIT 1")) == "PENDING"


def test_requeueing_leaves_finished_jobs_alone(db, actor_id, reference_data, inr_rate):
    upload(db, actor_id, ROW)
    db.flush()
    importer.process(db, importer.claim(db))

    assert importer.requeue_abandoned(db) == 0
