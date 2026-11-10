# ADR-0004: Bulk import is synchronous, with no job queue

**Status:** ~~Accepted~~ **Superseded by ADR-0007** · **Date:** 2026-11-05
**Superseded:** 2026-11-09, on the measurement this ADR asked for.

> The trigger written below — "import p95 > 30 s" — was met by the first real
> measurement: 10,000 rows took 72 seconds. The decision was reversed rather than
> defended. The reasoning is kept because it is still correct about *why* the
> all-or-nothing rule matters, and that rule survived the change.

## Context

HR's system of record today is a spreadsheet. The import endpoint is therefore not a
convenience; it is how data gets into this system at all.

The textbook answer for file upload is a job queue: accept the file, return `202 Accepted`
with a job id, process in a worker, poll for status. It brings Celery or RQ, a broker,
a worker process, a deployment story for the worker, and a failure mode where the worker
dies mid-file and nobody knows what was applied.

## Decision

Import runs **synchronously, inside the request**, as **one transaction**.

- Parse (CSV or XLSX), validate **every** row, and only then write.
- A single bad row at line 7,431 means **nothing is applied** — not 7,430 rows and a
  message. The response lists every error, with row numbers, so the HR Manager fixes the
  file once rather than discovering errors in waves.
- An `ImportJob` row records the attempt and **persists even when the import fails**, which
  is why the data writes happen in a `begin_nested()` savepoint: the job survives the
  rollback of the rows it was reporting on.
- Limits are checked before parsing, not after: `MAX_ROWS = 20_000`, and a file too large
  is refused with 413 rather than discovered by loading it.
- Import writes go through the `employee` and `salary` services — the same code path the UI
  uses — so an imported salary gets the same effective-dating, FX freezing and audit row as
  a typed one.

## Consequences

**Good.** All-or-nothing is the property that matters to the person doing the work: a
half-applied payroll file is worse than a rejected one, because reconciling it is manual.
No broker, no worker, no job-state machine, and no "the worker died, what was applied?"
investigation. The error report is the feature.

**Bad, and measured.** A 10,000-row file takes **72 s** (`pytest -m perf`, reproduced at
72.3 s and 72.5 s). That is past any default HTTP timeout worth having — nginx is 60 s,
most browsers and load balancers are in the same range — and it holds one database
transaction open for over a minute, with the `ImportJob` row and every employee row it
touches locked for the duration.

So the honest reading of the plan's own trigger ("add a job queue when import p95 > 30 s")
is that **it is already met at the size the brief names**. What was built is correct for the
file sizes HR actually sends week to week — hundreds to low thousands of rows, which land
in a few seconds — and is not correct for a 10,000-row initial load over HTTP. For that
load today, run the import inside the container rather than through the browser.

The 72 s is not the database. Each row goes through `employee.create` and `salary.set_salary`
individually, which is several statements per row — roughly 50,000 round trips. The same
10,000 employees inserted in bulk take **6.9 s** (`scripts/seed.py`), so there is an order of
magnitude available without changing the architecture at all. Batching the import's writes
is the cheaper fix, and it should be tried before a queue is introduced.

**Bad.** Memory is bounded by `MAX_ROWS`, not by file size on disk: 20,000 rows of parsed
objects sit in the process at once. Fine at this limit, not a design that scales by raising
it.

## Triggers to revisit

1. **Batch the writes first** — collapse per-row service calls into bulk statements, keeping
   the same validation and the same single transaction. Expected to bring 10,000 rows well
   under 30 s, and it costs no new infrastructure.
2. **Then, if still needed:** a job queue, when p95 import time stays above 30 s after
   batching, or files routinely exceed 50 MB, or more than one import runs concurrently.
3. **Independently:** if HR ever needs partial application ("import what is valid, report the
   rest"), that is a product decision that changes the all-or-nothing rule, and it should be
   made explicitly rather than arrived at by adding a queue.

## Rejected

- **A job queue now.** Three new moving parts to solve a problem the real file sizes do not
  have, and it would not have surfaced the 72 s measurement — it would have hidden it behind
  a spinner.
- **Partial application with a skip list.** Faster to implement, and it leaves the HR
  Manager reconciling two sources of truth: the file and what the system took from it.
- **Background thread in the API process.** A queue with none of a queue's guarantees:
  no retry, no visibility, and the work dies with the container.
