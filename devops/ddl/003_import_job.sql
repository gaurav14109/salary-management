-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- One upload attempt, its outcome, and its per-row errors.
--
-- Written before employee and salary because both carry a nullable reference to it:
-- "which rows did that file create" must be answerable for both tables.

CREATE TABLE import_job (
    id                 SERIAL       PRIMARY KEY,
    filename           VARCHAR(255) NOT NULL,
    status             VARCHAR(20)  NOT NULL,
    total_rows         INTEGER      NOT NULL DEFAULT 0,
    success_rows       INTEGER      NOT NULL DEFAULT 0,
    error_rows         INTEGER      NOT NULL DEFAULT 0,
    employees_created  INTEGER      NOT NULL DEFAULT 0,
    employees_updated  INTEGER      NOT NULL DEFAULT 0,
    salaries_created   INTEGER      NOT NULL DEFAULT 0,
    salaries_unchanged INTEGER      NOT NULL DEFAULT 0,
    errors             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_by         INTEGER      NOT NULL REFERENCES app_user (id),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- A CHECK rather than a Postgres ENUM type: adding a value to an enum needs
    -- ALTER TYPE and cannot run inside every migration path, while a CHECK is a
    -- one-line change. The rigidity an enum buys is not worth that.
    CONSTRAINT ck_import_job_status CHECK (status IN ('PENDING', 'SUCCESS', 'FAILED')),
    CONSTRAINT ck_import_job_counts CHECK (
        total_rows >= 0 AND success_rows >= 0 AND error_rows >= 0
        AND employees_created >= 0 AND employees_updated >= 0
        AND salaries_created >= 0 AND salaries_unchanged >= 0
    )
);

COMMENT ON COLUMN import_job.salaries_unchanged IS
    'Rows whose pay matched the current open salary row, so no row was written and no '
    'audit entry made. This is what makes re-uploading an exported file safe.';
COMMENT ON COLUMN import_job.errors IS
    '[{row_number, field, message}] - precise enough to fix in Excel and re-upload. '
    'When error_rows > 0 nothing was committed at all.';
