# ADR-0002: The exchange rate is frozen into the salary row at write time

**Status:** Accepted · **Date:** 2026-11-05

## Context

ACME pays people in ten currencies and the dashboard reports one number: total payroll in
USD. Something has to convert, and the question is *when*.

Converting at read time — store the local amount, multiply by today's rate when the
dashboard renders — is the simpler code and is wrong in a way that is hard to see. Two
consequences:

- **Last month's report changes.** An HR Manager who exported March's payroll in April and
  exports it again in June gets a different total, with no change to any salary. The
  system is now unable to reproduce its own output, which is the property Finance cares
  about most.
- **A rate lookup sits in the hot path** of the one query that already scans every open
  salary row.

## Decision

Convert once, when the salary is written, and store the evidence. Every `salary` row
carries:

| Column | Why |
|---|---|
| `amount`, `currency` | what the person is actually paid |
| `usd_amount` | the converted figure, computed once |
| `fx_units_per_usd` | the rate used |
| `fx_rate_date` | the `as_of` date of the rate row that was used |

The rate is chosen by `fx.get_rate(db, currency, as_of)`, which selects the most recent
rate **on or before** the salary's `effective_from` — so a salary effective in 2024
converts at a 2024 rate, not at today's.

**There is no fallback to 1.0.** If no rate exists for that currency on or before that
date, the write is refused with `MissingFxRate` (422), naming the currency and the date.
A salary silently stored at parity is wrong by a factor of eighty for INR, and no report
reveals it — the figure looks like money.

USD short-circuits to a rate of exactly 1, and migration 0002 seeds a `USD` rate row of
1.0 anyway, so the data is consistent whether or not the short-circuit is taken.

## Consequences

**Good.** A report is reproducible forever. The dashboard sums a stored column instead of
joining to a rate table and multiplying, which is both faster and simpler to read. The
stored rate is self-documenting: anyone asking "why is this $80,045?" has the rate and its
date in the same row.

**Bad.** Historical totals do not move when rates do, which some people will expect them to
— an INR salary set in 2021 still reports at its 2021 rate. That is the point, but it has
to be explained once to every new reader, which is what the column comments in
`004_core_tables.sql` are for.

**Bad.** Rates must be maintained, and a missing rate blocks a salary write outright.
Admins manage them through `/fx-rates`. Migration 0002 seeds one indicative rate per
currency dated **2020-01-01** — deliberately early, so that a historical import on a fresh
database resolves rather than failing on every row. The honest cost is spelled out in
`devops/seed/006_fx_rate.sql`: a 2021 salary converts at a roughly-2026 rate. The lesser of
two bad options, and the only one that lets a fresh database work.

**Bad, and the reason this is a judgement call.** Re-stating a salary's USD value after a
rate correction means re-writing rows. There is no backfill path, because a backfill that
changed historical reports would undo the property this decision exists to protect.

## Rejected

- **Convert at read time.** Reports stop being reproducible.
- **Store only the rate id, join at read.** The rate row could be edited or deleted after
  the fact, and the salary row would silently change meaning. Freezing the *number*, not a
  pointer to it, is what makes the row self-contained.
- **A daily rate feed.** Correct for a real deployment, out of scope here, and it does not
  change this decision — a feed would populate `fx_rate` and everything above still holds.
