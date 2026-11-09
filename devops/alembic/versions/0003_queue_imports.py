"""Imports run in the background, one at a time.

Reverses the synchronous-import decision in ADR-0004, on the measurement that ADR
asked for: 10,000 rows took 72 seconds, against a stated trigger of 30. That is past
any default HTTP timeout, so the upload that mattered most was the one most likely
to be cut off mid-flight - with the import still running, invisibly, behind it.

What this revision adds:

  * **PROCESSING and CANCELLED** statuses. PENDING now means "queued, not started",
    which it did not before - it was a transient value inside one request.
  * **payload** - the uploaded bytes, so the file outlives the request that brought
    it. Cleared when the job reaches a terminal state, because a table of 10 MB
    blobs nobody will read again is a table that only grows.
  * **failure_reason** - why a file failed when the answer is not a row error:
    unreadable format, a crash, a worker restart. `errors` already carries the
    per-row detail and says nothing about these.
  * **started_at / finished_at** - how long it waited and how long it ran, which is
    the measurement that decides whether one worker is still enough.

The queue is the table itself. `SELECT ... FOR UPDATE SKIP LOCKED` is what makes
"one file at a time" true even with several API replicas: the second worker skips
the locked row rather than waiting on it or picking it up twice. A broker would add
three moving parts to do the same thing for a queue that is almost always empty.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_job",
        # BYTEA rather than a file on a volume: the bytes and the job row commit
        # together, so there is no state where a queued job points at a file that
        # was never written. The 10 MB upload cap bounds it.
        sa.Column("payload", sa.LargeBinary(), nullable=True),
    )
    op.add_column("import_job", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.add_column("import_job", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("import_job", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("ck_import_job_status", "import_job", type_="check")
    op.create_check_constraint(
        "ck_import_job_status",
        "import_job",
        "status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'CANCELLED')",
    )

    # The worker's own query: oldest queued job first. Partial, because PENDING is a
    # handful of rows in a table that accumulates thousands of finished ones.
    op.create_index(
        "ix_import_job_queued",
        "import_job",
        ["created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ix_import_job_queued", table_name="import_job")

    # Any job in a status 0002 did not know about has to land somewhere legal before
    # the old constraint goes back on. FAILED, not SUCCESS: a job that was still
    # processing did not finish, and recording it as succeeded would be a lie the
    # counts do not support.
    op.execute(
        "UPDATE import_job SET status = 'FAILED' WHERE status IN ('PROCESSING', 'CANCELLED')"
    )
    op.drop_constraint("ck_import_job_status", "import_job", type_="check")
    op.create_check_constraint(
        "ck_import_job_status",
        "import_job",
        "status IN ('PENDING', 'SUCCESS', 'FAILED')",
    )

    op.drop_column("import_job", "finished_at")
    op.drop_column("import_job", "started_at")
    op.drop_column("import_job", "failure_reason")
    op.drop_column("import_job", "payload")
