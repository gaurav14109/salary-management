-- SNAPSHOT. This file is executed only by Alembic migration 0001.
-- Editing it changes what a fresh database gets and nothing else: a database
-- already past 0001 will never see the change, and the two diverge silently.
-- Schema changes go in a new alembic revision. See backend/README.md.

-- Who is on the payroll right now.
--
-- Every dashboard aggregate, list query and export reads this instead of salary.
-- Writing "effective_to IS NULL AND status = 'ACTIVE'" at each call site is how one
-- query eventually forgets the second half and counts leavers in total payroll -
-- permanently, with nothing raising. Stated here once, it cannot be forgotten,
-- because no call site writes it.
--
-- Salary HISTORY queries read salary directly, which is correct: history must
-- include people who have left.

CREATE VIEW current_salary AS
SELECT
    s.id,
    s.employee_id,
    s.amount,
    s.currency,
    s.usd_amount,
    s.fx_units_per_usd,
    s.fx_rate_date,
    s.effective_from,
    s.change_reason,
    e.employee_code,
    e.first_name,
    e.last_name,
    e.country_code,
    e.department_id,
    e.band_id,
    e.job_title
FROM salary s
JOIN employee e ON e.id = s.employee_id
WHERE s.effective_to IS NULL
  AND e.status = 'ACTIVE';

COMMENT ON VIEW current_salary IS
    'The one definition of who is on the payroll right now. The status filter is '
    'belt-and-braces on top of the closed row: terminating an employee closes their '
    'open salary row, so either condition alone would exclude a leaver.';
