# ADR-0008: The uploaded file lives on the job row, not in object storage

**Status:** Accepted · **Date:** 2026-11-10 · **Related:** ADR-0007

## Context

ADR-0007 moved imports off the request and into a background queue. That created a
question ADR-0004 never had to answer: the request that accepts a file no longer
applies it, so **the bytes have to outlive the request** and be readable by whichever
worker claims the job — possibly in a different process, possibly on a different
host.

Three places that could live:

1. **The container's filesystem.** Rejected outright. A file on replica A's disk is
   invisible to replica B, and a container restart loses it — so the queue would
   hold jobs whose files no longer exist.
2. **Object storage** (MinIO locally, S3 in a deployment). The textbook answer.
3. **A column on the `import_job` row.**

The constraints that decide it: uploads are capped at **10 MB**, the file is read
**once**, and it is needed for **seconds to minutes** — between being accepted and
being applied. Nothing in the product asks to read an uploaded file again.

## Decision

The file is stored as **`payload BYTEA` on the `import_job` row**, and set to `NULL`
the moment the job reaches a terminal state.

The deciding property is that **the bytes and the job commit together**. One
`INSERT`, one transaction. There is no moment at which a queued job points at a file
that was never written, and no moment at which a file exists that no job will ever
claim.

Object storage cannot offer that. Two systems means two writes, and both orderings
leak:

| Order | Crash between them | Result |
|---|---|---|
| Upload, then insert the row | object with no job | An orphan nobody will read. Needs a sweeper to find and delete it. |
| Insert the row, then upload | job with no object | A job stuck PENDING forever, pointing at nothing. Needs a timeout to detect it. |

Both are solvable, and the solutions are a background sweeper and a staleness
timeout — code whose only purpose is to clean up after a problem the single-column
version does not have.

Postgres also makes the column cheaper than it looks: **TOAST** compresses any value
over ~2 KB and moves it out of the main table, so a 10 MB spreadsheet neither bloats
the row nor slows the `SELECT id, status, filename` that the history page runs.

## Consequences

**Good.** No new container, no credentials, no bucket lifecycle policy, no second
service that can be down while the API is up. Salary data stays in exactly one place,
which is one thing to secure, back up and answer retention questions about. A worker
that dies mid-import can retry, because the file was never on the machine that died.

**Good.** The cleanup is a single `UPDATE ... SET payload = NULL` inside the
transaction that finishes the job, not a scheduled task that might not run. A table of
10 MB blobs nobody will read again only grows.

**Bad.** Memory, not just storage: the worker loads the whole file to parse it. Fine
at 10 MB; it is the cap, not the design, that makes it safe.

**Bad.** A database dump taken while an import is queued contains the raw spreadsheet.
It is salary data inside the salary database, so no boundary widens — but it is worth
knowing before anyone raises the cap or adds retention of past uploads.

**Bad, and accepted.** The file is not kept. "Show me the spreadsheet Priya uploaded
in March" is unanswerable; what survives is the record of what that import *did* —
counts, row errors, who ran it, when. Keeping the files is a retention decision (how
long, and who may read a file full of salaries) that nobody has asked for.

## Future scope — MinIO

The migration to object storage is deliberately small, and should happen when the
**size** of files changes, not when their number does:

1. Uploads above ~50 MB, or the 10 MB cap being raised.
2. A requirement to **keep** uploaded files — audit, re-download, re-run.
3. `payload` coming to dominate the size of the database or its backups.
4. Parsing needing to **stream**, because a file no longer fits comfortably in memory.

What it would look like:

- `payload BYTEA` becomes `payload_key TEXT` — the object key, not the bytes.
- A small client wraps `put`/`get`/`delete`, so the importer still asks for "the
  bytes of this job" and does not know where they came from.
- **Insert the row first, upload second**, and treat a PENDING job whose object is
  missing after N minutes as FAILED with a reason. Chosen over the other ordering
  because a visible stuck job is better than an invisible orphan.
- A sweeper deletes objects whose job row is gone.
- Bucket lifecycle expiry becomes the backstop for the `NULL`-on-finish step.

The real prize at that point is **pre-signed uploads**: the browser PUTs straight to
storage and the API never carries the file at all. That is the change that makes a
100 MB import reasonable, and it is not available with a column.

## Rejected

- **MinIO now.** A container, credentials, lifecycle rules, a second failure domain
  and an orphan sweeper, to avoid a column Postgres already stores out-of-line, for
  files bounded at 10 MB that are deleted within minutes. Correct pattern, wrong
  size — and introducing it now would mean writing the sweeper before anything needs
  it.
- **A shared volume.** Removes the replica problem and keeps the two-phase write,
  while adding a filesystem that has to be mounted identically everywhere and backed
  up separately. The worst of both.
- **Parsing in the request and storing rows instead of bytes.** Reads a 20,000-row
  spreadsheet twice, and puts the slowest part of a bad upload back in front of the
  user — which is the thing ADR-0007 exists to remove.
