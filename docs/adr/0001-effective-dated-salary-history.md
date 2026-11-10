# ADR-0001: Salary is effective-dated, never updated in place

**Status:** Accepted · **Date:** 2026-11-05

## Context

An HR Manager gives someone a raise. The obvious implementation is one row per employee
with an `amount` column and an `UPDATE` when it changes.

That design answers "what does Priya earn?" and cannot answer anything else. Three
questions the brief implies, all unanswerable from a mutable column:

- What did Priya earn in March, when Finance is reconciling March's payroll?
- When did this change, and who approved it?
- What was total payroll last quarter?

An audit log alongside the mutable column is the usual patch, and it fails in a specific
way: the log and the table can disagree, and when they do nobody knows which is right.
The history becomes commentary on the data rather than the data.

## Decision

A salary is a row with a validity period. `salary` holds `effective_from` (inclusive) and
`effective_to` (inclusive, `NULL` for the current row). A pay change does not update
anything:

1. close the open row — `effective_to = new effective_from - 1 day`;
2. insert the new row, open.

Enforced by the database, not by the service:

```sql
CREATE UNIQUE INDEX ux_salary_open ON salary (employee_id) WHERE effective_to IS NULL;
CONSTRAINT ck_salary_range CHECK (effective_to IS NULL OR effective_to > effective_from)
```

A `current_salary` view joins the open row to the active employee, and every report reads
it rather than re-deriving the rule.

A salary effective on or before the one currently in force is **refused**
(`BackdatedSalary`, 422). Accepting it would mean closing the open row at a date before its
own start — a row that ends before it begins — and, worse, silently rewriting a period
that has already been reported on.

## Consequences

**Good.** Every historical question is a query, not a migration. "Who changed this, when,
from what" survives in the database alone, with Keycloak unreachable and the realm long
since rebuilt. The partial unique index makes "two open salaries" unrepresentable rather
than merely discouraged — which matters because every report joins on `effective_to IS NULL`
and a second open row would double-count one person in total payroll with nothing raising.

**Bad.** Every read of "current salary" is a join, and the salary table grows without bound
— 18,741 rows for 10,000 employees today, more every year. Measured at that volume the join
costs ~8 ms ([performance.md](../performance.md)), so this is a cost to watch, not one to
pay down yet.

**Also bad, and accepted.** Correcting a genuine mistake in a past period is not possible
through this path at all. That is deliberate: a correction to a reported period has
different consequences from a raise and should look different. It is not built
([tradeoffs.md](../tradeoffs.md)).

## Rejected

- **Mutable column + audit log.** Two sources of truth that can disagree.
- **A separate `salary_history` table.** The current row and the historical row have
  identical shape; two tables means two write paths and the chance to update one.
- **`tstzrange` with an exclusion constraint.** More precise, and it would express the
  no-overlap rule directly. Rejected for legibility: effective dates in HR are whole days
  in local reasoning, and `[2026-04-01, 2027-03-31]` is read correctly by everyone,
  including the person writing a report in a SQL console. The partial index covers the one
  overlap case that actually occurs.
