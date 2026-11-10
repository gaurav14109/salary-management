# ADR-0007: Imports run in a background queue, one file at a time

**Status:** Accepted · **Date:** 2026-11-09 · **Supersedes:** ADR-0004

## Context

ADR-0004 chose a synchronous import and named the condition that would reverse it:
import p95 above 30 seconds. The first measurement against the size the brief names
came in at **72 seconds for 10,000 rows**, reproduced twice.

That is past every default HTTP timeout — nginx is 60 s, most browsers and load
balancers are in the same range. So the upload that matters most is the one most
likely to be cut off, and when it is cut off the import *keeps running*: the user
sees a failure, the data lands anyway, and the two facts never meet.

The slowness is not the database. Each row goes through `employee.create` and
`salary.set_salary` individually — roughly 50,000 round trips. The same 10,000
employees inserted in bulk take 6.9 s. Batching would help, and would not fix the
timeout: a 30-second request is still a request nobody can navigate away from.

## Decision

The upload records the file and returns **202 Accepted** with a job in `PENDING`. A
worker applies it in the background, **one file at a time**.

- The **queue is the `import_job` table**. No broker, no worker container, no result
  store. `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` claims a job: a second worker
  skips a locked row rather than blocking on it or picking it up twice.
- **One file at a time is enforced by a Postgres advisory lock**, taken before the
  claim and held across the whole cycle. This is a correction, recorded rather than
  quietly fixed: `SKIP LOCKED` was originally credited with this guarantee and does
  not provide it. It stops two workers taking the *same* job; it does nothing to stop
  them taking two *different* jobs and applying both at once. Until the lock existed,
  serial processing was a property of running a single container - and
  `--scale api=2` would have removed it silently.
- The **file is stored on the job row** (`payload BYTEA`). The bytes and the job
  commit together, so there is no state where a queued job points at a file that was
  never written. The payload is dropped the moment the job is terminal — a table of
  10 MB blobs nobody will reopen only grows. Why a column and not object storage,
  and what would change that: [ADR-0008](0008-uploaded-files-live-on-the-job-row.md).
- **Statuses are `PENDING → PROCESSING → SUCCESS | FAILED`.** `PENDING` used to be a
  transient value inside one request; it is now a state a user sits in, and the UI
  says which one they are in.
- **`failure_reason`** carries what `errors` cannot: an unreadable format, a crash, a
  worker restart. A file-level failure has no row to point at.
- The **worker runs inside the API process**, started by the lifespan. A separate
  container is one more thing to deploy, monitor and have be down while the API is
  up, for a queue that is empty almost all of the time.
- **Jobs left `PROCESSING` by a stopped container are requeued at startup.** Safe
  precisely because an import is one transaction: a container that died mid-file
  applied none of it, so re-running the job is the same as running it.

The all-or-nothing rule from ADR-0004 is **unchanged**. One bad row still means
nothing is applied, and the error report is still the feature.

## Consequences

**Good.** The upload returns in milliseconds regardless of file size. The user can
close the tab; the record of what happened outlives the request, which is what the
`import_job` row was always for. Two people uploading at once no longer have each
one's "is this code taken" question answered by a half-applied other file.

**Good.** `PROCESSING` is committed before the work starts, so a user refreshing the
page sees it while the file is being applied, rather than only after it is over.

**Bad.** The worker is in the API process, so an import competes for CPU with
requests, and scaling the API scales the workers. At one file a minute that is
noise; it would not be at one a second.

**Bad, and the price of the advisory lock.** Import throughput does not scale with
the API. Ten replicas still apply one file at a time, because nine of them are asleep
holding nothing. That is the point - two imports interleaving would each have "is
this employee code already taken?" answered against a database the other is halfway
through changing - but it means the queue is a single lane. If imports ever became
the bottleneck, the fix is to make one import faster by batching its per-row writes,
not to run several at once.

**Bad.** The file lives in the database until the job finishes. At a 10 MB cap and a
queue that is nearly always empty this is bounded, but it is a blob in a relational
table and that is a smell worth remembering.

**Bad, and deliberate.** No retry, no priority, no scheduling, no cancellation. A
failed job stays failed and the user uploads a corrected file, which is the right
remedy for the only failure that actually happens — a wrong spreadsheet.

**Still open.** The 72 seconds has not improved; it has moved off the request path.
Batching the per-row service calls is the obvious next step and is now a performance
question rather than a usability one.

## Triggers to revisit

| Change | When |
|---|---|
| Batch the per-row writes | Any time — it is free latency, now that nobody is waiting |
| A real broker (Celery/RQ) | More than one import a minute, or imports needing retry across restarts |
| A separate worker container | Imports starving request latency, measurably |
| Cancellation | Users asking to stop a running import — the `CANCELLED` status exists for it |

## Rejected

- **Keeping it synchronous and batching the writes.** Would get 10,000 rows under
  30 s and leaves the user watching a spinner for 25 of them, with a hard ceiling
  the next bigger file walks straight through.
- **A broker now.** Three new moving parts to serialise a queue that holds one item.
  The table and an advisory lock already do it, and `SKIP LOCKED` is the same
  primitive a broker uses underneath.
- **Running the import in a thread per upload.** A queue with none of a queue's
  guarantees: no ordering, no visibility, no recovery, and two uploads racing.
