-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Indexes that are part of the design, not tuning. Each one is here because a
-- query in this product depends on it, or because an invariant does.

-- ────────────────────────────────────────────────────────────────────────────
-- The single most important line in the schema.
--
-- "Exactly one current salary per employee" becomes a database guarantee rather
-- than an application convention. Every dashboard figure, list row and export
-- joins on effective_to IS NULL; if a bug ever left two open rows, total payroll
-- would be silently wrong and nothing would raise. This makes the second insert
-- fail instead.
-- ────────────────────────────────────────────────────────────────────────────
CREATE UNIQUE INDEX ux_salary_open
    ON salary (employee_id)
    WHERE effective_to IS NULL;

-- Salary history for one employee, newest first.
CREATE INDEX ix_salary_history ON salary (employee_id, effective_from DESC);

-- Dashboard currency breakdown.
CREATE INDEX ix_salary_currency ON salary (currency);

-- Dashboard group-bys.
CREATE INDEX ix_employee_country ON employee (country_code);
CREATE INDEX ix_employee_dept    ON employee (department_id);
CREATE INDEX ix_employee_band    ON employee (band_id);

-- Case-insensitive name search.
CREATE INDEX ix_employee_name ON employee (lower(last_name));

-- Every payroll aggregate filters on ACTIVE. A partial index keeps it cheap and
-- small, since the filtered set is the one that is read constantly.
CREATE INDEX ix_employee_active ON employee (status) WHERE status = 'ACTIVE';

-- "Which rows did that upload create."
CREATE INDEX ix_employee_import ON employee (import_job_id) WHERE import_job_id IS NOT NULL;
CREATE INDEX ix_salary_import   ON salary   (import_job_id) WHERE import_job_id IS NOT NULL;
