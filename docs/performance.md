# Performance

Every number here was measured on this machine, against a real Postgres, at the volume
the brief names. Nothing is extrapolated, and the one result that contradicts a design
decision is reported rather than dropped.

## How to reproduce

```bash
cd devops && docker compose up -d --wait db     # Postgres + migrations
cd backend
python -m scripts.seed                          # 10,000 employees, committed
python -m pytest -m perf -v                     # the budgets, asserted
python -m scripts.seed --clear                  # remove the generated rows
```

`scripts/seed.py` is deterministic (`RNG_SEED = 20261108`), so two runs produce byte-identical
data and two measurements are comparable. `tests/test_performance.py` calls the same
`populate()` the script does, inside a transaction it rolls back, so the measurement runs
against the dataset a reviewer can see rather than a tidier one built for the test.

**Environment:** Apple Silicon laptop, Postgres 16 in Docker Desktop, SQLAlchemy 2 over
psycopg 3, FastAPI exercised through `TestClient` — so the figures include routing,
dependency resolution, serialization and Pydantic validation, but no network. Medians of
seven runs after a discarded warm-up.

## The dataset

| | 10,000 employees | 100,000 employees |
|---|---|---|
| Employees | 10,000 | 100,000 |
| of which leavers | 517 | 4,988 |
| Salary rows | 18,741 | 187,280 |
| Open salary rows | 9,483 | 95,012 |
| Generation time | 6.9 s | 66.6 s |

Twelve countries, twelve departments, seven bands, ten currencies, all unevenly weighted —
30% of headcount in India, 2% in the UAE. An evenly spread dataset would make a country
filter look like a no-op, which is the opposite of the thing being measured.

## Results

Median response time, end to end, in milliseconds.

| Endpoint | 10k | 100k | Budget |
|---|---|---|---|
| `GET /employees?size=50` (first page) | **13.7** | 61.8 | 300 |
| `GET /employees?size=50&page=last` | **18.4** | 107.5 | 300 |
| `GET /employees?country=IN` | **10.7** | 27.8 | 300 |
| `GET /dashboard` | **46.0** | 280.4 | 500 |
| `GET /dashboard?country=US` | **28.5** | 190.2 | 500 |

Both budgets hold at 10,000 with roughly an order of magnitude to spare, and both still
hold at 100,000 — ten times the brief — without a change to the schema or the queries.

## Finding 1: at 10,000 rows the indexes do nothing, and that is the correct result

The honest version of "we added indexes and it got faster" is that, at this volume, it did
not. The experiment: drop all eight indexes `007_indexes.sql` creates, `ANALYZE`, and
re-measure.

| Endpoint | 10k with indexes | 10k without | 100k with | 100k without |
|---|---|---|---|---|
| list, first page | 13.7 | 14.7 | 61.8 | 60.5 |
| list, last page | 18.4 | 17.5 | 107.5 | 105.0 |
| **list, country filter** | 10.7 | 10.0 | **27.8** | **44.6** |
| dashboard | 46.0 | 45.8 | 280.4 | 275.9 |
| dashboard, filtered | 28.5 | 33.6 | 190.2 | 198.5 |

The plans say why. With every index present, the planner still chooses a sequential scan:

```
Limit  (cost=1187.51..1187.63 rows=50 width=174) (actual time=8.466..8.471 rows=50 loops=1)
  ->  Sort  (cost=1187.51..1212.51 rows=10000 width=174) (actual time=8.465..8.468 rows=50 loops=1)
        Sort Key: employee.last_name, employee.id
        Sort Method: top-N heapsort  Memory: 45kB
        ->  Hash Right Join  (cost=401.00..855.31 rows=10000 width=174) (actual time=1.305..5.168 rows=10000 loops=1)
              Hash Cond: (salary.employee_id = employee.id)
              ->  Seq Scan on salary  (cost=0.00..429.41 rows=9483 width=67) (actual time=0.002..1.504 rows=9483 loops=1)
                    Filter: (effective_to IS NULL)
                    Rows Removed by Filter: 9258
              ->  Hash  (cost=276.00..276.00 rows=10000 width=107) (actual time=1.298..1.299 rows=10000 loops=1)
                    Buckets: 16384  Batches: 1  Memory Usage: 1560kB
                    ->  Seq Scan on employee  (cost=0.00..276.00 rows=10000 width=107) (actual time=0.002..0.539 rows=10000 loops=1)
Planning Time: 0.106 ms
Execution Time: 8.487 ms
```

10,000 employees is about 276 pages. Reading all of them sequentially costs less than
walking an index and then fetching the heap rows it points at, and Postgres is right about
that. An index is not a speed-up at this size; it is insurance against a size this
application does not yet have.

**The exception is the one that matters.** At 100,000 rows, a *selective* filter does use
its index, and `ix_employee_country` pays for itself: 27.8 ms against 44.6 ms, a 1.6×
difference on the single most-used filter in the UI. That is the volume at which the
indexes stop being insurance and start being load-bearing.

## Finding 2: the dashboard is seven scans, not one

One `GET /dashboard` issues **seven** statements: the headline aggregate, highest paid,
lowest paid, and one group-by each for country, department, band and currency. Each one
independently joins `salary` to `employee`:

```
Sort  (cost=1175.43..1181.43 rows=2400 width=325) (actual time=7.434..7.435 rows=12 loops=1)
  Sort Key: (sum(s.usd_amount)) DESC
  Sort Method: quicksort  Memory: 25kB
  ->  HashAggregate  (cost=1004.69..1040.69 rows=2400 width=325) (actual time=7.412..7.430 rows=12 loops=1)
        Group Key: e.country_code, country.name
        Batches: 1  Memory Usage: 121kB
        ->  Hash Join  (cost=436.51..914.76 rows=8993 width=227) (actual time=1.885..5.802 rows=9483 loops=1)
              Hash Cond: (e.country_code = country.code)
              ->  Hash Join  (cost=419.54..873.85 rows=8993 width=9) (actual time=1.873..4.540 rows=9483 loops=1)
                    Hash Cond: (s.employee_id = e.id)
                    ->  Seq Scan on salary s  (cost=0.00..429.41 rows=9483 width=10) (actual time=0.002..1.461 rows=9483 loops=1)
                          Filter: (effective_to IS NULL)
                          Rows Removed by Filter: 9258
                    ->  Hash  (cost=301.00..301.00 rows=9483 width=7) (actual time=1.869..1.869 rows=9483 loops=1)
                          Buckets: 16384  Batches: 1  Memory Usage: 499kB
                          ->  Seq Scan on employee e  (cost=0.00..301.00 rows=9483 width=7) (actual time=0.001..1.153 rows=9483 loops=1)
                                Filter: ((status)::text = 'ACTIVE'::text)
                                Rows Removed by Filter: 517
              ->  Hash  (cost=13.10..13.10 rows=310 width=230) (actual time=0.010..0.011 rows=45 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 10kB
                    ->  Seq Scan on country  (cost=0.00..13.10 rows=310 width=230) (actual time=0.003..0.005 rows=45 loops=1)
Planning Time: 0.131 ms
Execution Time: 7.455 ms
```

Seven times through ~9,500 open salary rows is 46 ms, which is inside budget and was left
alone. At 100,000 it is 280 ms — still inside budget, and the trend is linear rather than
quadratic, because each query is one pass.

This is a deliberate non-optimization. Collapsing six group-bys into one `GROUPING SETS`
query would cut the scans to one and the response to perhaps 15 ms, at the cost of a single
query returning four differently-shaped result sets that application code has to
demultiplex. The six-query version is six obvious queries. **The trigger to change it:
`/dashboard` p95 above 400 ms, or headcount past 250,000.**

## Finding 3: the name index cannot serve the default sort

The list page sorts by `last_name`. The index is `ix_employee_name ON employee (lower(last_name))`
— built for case-insensitive *search*, and unusable for an `ORDER BY last_name`, because the
expression does not match. The plan confirms it: `Sort Method: top-N heapsort`, every time.

The sort is bounded (`top-N`, 45 kB) so it costs ~8 ms at 10,000 and the page is served well
inside budget. Adding a second index on `(last_name, id)` to make the sort disappear would
buy a few milliseconds nobody is waiting for, and cost a write-time index maintenance on
every employee insert — including the 10,000-row import. Not taken. **The trigger: list p95
above 200 ms.**

## Finding 4: offset pagination holds at this scale

Page 200 of 200 costs 18.4 ms against 13.7 ms for page 1 — Postgres walks the 9,950 rows it
is skipping, which is real but cheap. At 100,000 the same comparison is 107.5 ms against
61.8 ms, so the gap grows with depth exactly as the mechanism predicts.

`test_list_endpoint_is_not_slower_on_the_last_page` exists to catch the point where this
stops being acceptable. **The trigger to switch to keyset pagination: last-page latency
above 300 ms**, which on this trend arrives somewhere past 300,000 employees — and by then
nobody is paging to the end of a 6,000-page list anyway, which is the better fix.

## What is not measured here

- **Concurrency.** Every number is a single request against an idle database. Nothing here
  says what happens with fifty HR Managers at once; measuring that needs a load generator
  and a deployment that is not Docker Desktop.
- **The import path**, which has its own perf tests in `tests/services/test_importer.py`:
  10,000 rows parsed, validated and committed as one transaction.
- **The CSV export**, covered by `tests/api/test_export.py`, which asserts the body is
  streamed rather than materialized — a memory property, not a latency one.
