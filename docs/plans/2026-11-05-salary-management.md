# Employee Salary Management — Implementation Plan


**Goal:** Build a web application that lets one HR Manager manage salary data for 10,000
employees across multiple countries, and answer questions about how the org pays people.

**Architecture:** A FastAPI backend owns all business rules and talks to a single Postgres
database; a React SPA is a pure client of its REST API. Salary is modelled as an
*effective-dated history* (one row per change, never an update in place), with the USD
equivalent and the FX rate frozen onto each row at write time so historical reports never
move when an FX rate is corrected. The AI chatbot does **not** generate SQL — it calls a
small set of whitelisted, already-authorized query functions, so the blast radius of a bad
model output is "wrong chart", not "data leak".

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · Postgres 16 ·
pytest + httpx · React 18 + TypeScript + Vite · TanStack Query + TanStack Table · Recharts ·
openpyxl (xlsx) + stdlib `csv` · Anthropic SDK (tool calling) for the chatbot.

**Spec:** [`requirements.md`](../requirements.md)

**Execution order:** [`implementation-plan.md`](../implementation-plan.md) is
authoritative for *when* each task runs. It groups these tasks into vertical feature
slices (backend → frontend per feature) so every slice ends in something usable; this
file holds the per-task TDD steps, exact signatures and exact test names.

---

## Global Constraints

- **Reporting currency is USD.** Salary is *stored* in the employee's local currency; every
  aggregate, dashboard figure and export total is reported in USD.
- **FX rates are frozen at write time.** Every salary row persists `usd_amount`,
  `fx_units_per_usd` and `fx_rate_date`. Converting at read time is forbidden — correcting a
  rate must never silently rewrite last quarter's payroll total.
- **Money is `NUMERIC(14,3)` locally and `NUMERIC(14,2)` in USD; FX rates are `NUMERIC(18,8)`.** No floats touch money, in
  Python (`decimal.Decimal`) or in the database.
- **Salary is never updated in place.** A change closes the current row (`effective_to`)
  and inserts a new one. Exactly one open row (`effective_to IS NULL`) per employee.
- **Every salary mutation writes an `audit_log` row** in the same transaction as the
  mutation. If the audit write fails, the mutation fails.
- **Two roles only:** `ADMIN`, `HR_MANAGER`, carried in the Keycloak token and read by the
  salary service from the claims the BFF decoded — never from the UI.
  Unauthenticated → 401, authorized-but-wrong-role
  → 403. Never 404-as-authz.
- **Server-side everything** for lists: pagination, sorting, filtering and aggregation run
  in SQL. The API never returns an unbounded collection; default page size 50, max 200.
- **10,000 employees is small.** No Redis, no message queue, no read replica, no caching
  layer. Any such addition must be justified by a measured number, not a guess.
- **Currency codes are ISO 4217 three-letter uppercase**; country codes are ISO 3166-1
  alpha-2 uppercase.

## Review Focus

Input classes the spec implies but does not describe. Each has a test pinned to the task
that owns the code.

1. **Salary in a currency with no FX rate on the effective date** — import and manual edit
   must both reject the row with a named error (`no FX rate for XAF as of 2026-11-05`),
   never store `usd_amount = NULL` and never fall back to rate 1.0. *(Tasks 6, 7, 11)*
2. **An import file that is half valid** — valid rows must not be partially committed. The
   whole file commits or none of it does, and the user gets a per-row error report with
   row numbers. *(Task 11)*
3. **A backdated salary change** — effective date earlier than the current open row must be
   rejected in the MVP with a clear message, not silently corrupt the history chain.
   *(Task 7)*
4. **An HR_MANAGER calling an admin-only endpoint** (FX rate edit, user management) must get
   403, and the dashboard must not leak admin-only aggregates through a filter parameter.
   *(Task 5, 12)*
5. **A chatbot question the data cannot answer** ("what will Priya earn next year?",
   "show me Priya's home address") must produce a refusal that says why — never a
   hallucinated number, and never a number without the filter that produced it. *(Task 19)*

---

## File Structure

```
backend/
  app/
    main.py                 wiring only — handlers, middleware, routers. Owns nothing.
    config.py               Pydantic Settings (DB url, gateway key, log level)
    db.py                   engine, Base, SessionLocal, get_db dependency

    gateway.py              is the caller the BFF? middleware, constant-time key check
    access.py               is this person allowed? Role, Principal, require_role
    deps.py                 current_actor — the things a route depends on
    middleware.py           registration order, which is inverted and easy to get wrong
    errors.py               typed failures -> HTTP, in one place
    observability.py        request id + one log line per request
    logging_setup.py        JSON formatter, redaction at source

    models/                 SQLAlchemy ORM — mirrors devops/ddl, never defines it
      employee.py  salary.py  fx_rate.py  user.py  audit.py  import_job.py
      reference.py          country, currency, department, band — four small tables
    schemas/                Pydantic — everything that crosses the wire
      common.py             ErrorResponse, FieldError, Page[T], health
      fx.py  salary.py  employee.py  reference.py  dashboard.py
    services/               business rules — the only layer that writes
      employee.py           create, update, set_status, search
      salary.py             effective-dating, freeze-on-write
      fx.py                 rate lookup + conversion
      audit.py              audit_log writer
      user.py               resolve_actor — Keycloak sub -> local audit row
      importer.py           parse -> validate -> commit
      analytics.py          dashboard aggregates (raw SQL)
      chat.py               tool definitions + Anthropic loop
    api/                    thin HTTP layer — validate, authorize, call, serialize
      employees.py  fx.py  reference.py  imports.py  dashboard.py  chat.py
  tests/                    mirrors app/ — services against Postgres, api via TestClient
  scripts/seed.py           generate 10,000 employees for perf work
devops/
  docker-compose.yml  Dockerfile targets  alembic/  ddl/  seed/  keycloak/
frontend/
  src/
    api/client.ts           typed fetch wrapper, cookie session + CSRF, error normalization
    pages/                  EmployeeListPage EmployeeDetailPage
                            DashboardPage ImportPage ChatPage
    components/             EmployeeTable SalaryEditForm StatCard ImportErrorReport
    auth/                   useAuth (GET /auth/me), route guard. No token storage.
plan_doc/
  requirements.md  architecture.md  tradeoffs.md  ai-usage.md  plans/
docs/
  adr/  diagrams/  *.docx
```

**Why this split:** `services/` holds every business rule and is the only layer that
writes, so the rules are unit-testable without HTTP and cannot be bypassed by adding an
endpoint. `api/` is deliberately dumb. Files are split by aggregate, not by technical
layer, so a change to "how salary works" touches `models/salary.py`,
`services/salary.py`, `schemas/salary.py` and nothing else.

Salary has no route module: pay is reached as a sub-resource of a person
(`PUT /employees/{id}/salary`), so it lives in `api/employees.py`. The rule is one
module per aggregate in `services/`, one per *URL root* in `api/` — those are not
always the same thing, and pretending they are would create a file holding one route.

---

## Phase 1 — Backend foundation

### Task 1: App skeleton and test harness

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/config.py`, `backend/app/db.py`, `devops/docker-compose.yml`
- Test: `backend/tests/test_health.py`, `backend/tests/conftest.py`

**Interfaces:**
- Produces: `app` (FastAPI instance), `get_db()` session dependency, `client` pytest fixture (`httpx.AsyncClient` bound to the app), `db_session` fixture (transaction rolled back per test).

- [x] **Step 1: Write the failing test** — `tests/test_health.py::test_health_returns_ok`
  asserts `GET /health` → 200 and `{"status": "ok"}`.
- [x] **Step 2: Run** `pytest tests/test_health.py -v` → FAIL (no module `app.main`).
- [x] **Step 3: Implement** `app/config.py` with a Pydantic `Settings` reading
  `DATABASE_URL`, `GATEWAY_KEY` (the shared secret proving a request came from the BFF),
  `ANTHROPIC_API_KEY`
  from env; `app/db.py` with engine + `get_db()`; `app/main.py` with the FastAPI app and
  `GET /health`. `docker-compose.yml` runs Postgres 16 only.
- [x] **Step 4: Run** `pytest -v` → PASS.
- [ ] **Step 5: Commit** — `feat(backend): scaffold FastAPI app with health check and test harness`

### Task 2: Data model and migrations

**Files:**
- Create: `backend/app/models/{employee,salary,fx_rate,user,audit,import_job,reference}.py`,
  `devops/alembic/` + first revision, `devops/ddl/*.sql`
- Test: `backend/tests/test_models.py`

Migrations live in `devops/` with the rest of the infrastructure: the database is shared,
not one service's private store. The cost is stated in `devops/README.md` — a schema change
and the code using it are a two-directory commit, and backend CI triggers on `devops/**`
so a DDL-only push still runs the suite that proves the DDL works.

**Interfaces:**
- Produces: ORM classes `Employee`, `Salary`, `FxRate`, `User`, `AuditLog`, `ImportJob`.
  - `Employee(id, employee_code UNIQUE, first_name, last_name, email UNIQUE, country_code CHAR(2), department, job_title, band, status, hire_date)`
  - `Salary(id, employee_id FK, amount NUMERIC(14,2), currency CHAR(3), usd_amount NUMERIC(14,2), fx_units_per_usd NUMERIC(18,8), fx_rate_date DATE, effective_from DATE, effective_to DATE NULL, created_at, created_by FK)`
  - `FxRate(id, currency CHAR(3), units_per_usd NUMERIC(18,8), as_of DATE)` — unique `(currency, as_of)`
  - `AppUser(id, external_id UNIQUE, email, display_name, created_at)` — the Keycloak
    `sub` is the key. No password hash, no role column: this service never authenticates
    anyone, and the token is the only role authority.
  - `AuditLog(id, entity_type, entity_id, action, old_value JSONB, new_value JSONB, actor_id, created_at)`
  - `ImportJob(id, filename, status, total_rows, success_rows, error_rows, errors JSONB, created_by, created_at)`

- [x] **Step 1: Write the failing tests** —
  `test_one_open_salary_row_per_employee` (inserting a second row with
  `effective_to IS NULL` for the same employee raises `IntegrityError`);
  `test_duplicate_fx_rate_for_currency_and_date_rejected`;
  `test_duplicate_employee_code_rejected`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement** the models plus these indexes, then
  `alembic revision --autogenerate`:
  - `UNIQUE INDEX ux_salary_open ON salary(employee_id) WHERE effective_to IS NULL` — the
    partial unique index is what makes "exactly one current salary" a database guarantee
    rather than an application convention.
  - `INDEX ON salary(employee_id, effective_from DESC)` — history lookup.
  - `INDEX ON employee(country_code)`, `INDEX ON employee(department)` — dashboard group-bys.
  - `INDEX ON employee(lower(last_name))` — case-insensitive name search.
- [x] **Step 4: Run** `alembic upgrade head && pytest tests/models -v` → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add effective-dated salary schema with partial unique index`

### Task 3: Access control in the salary service

The service authenticates nobody. Keycloak issued the token, the BFF verified it
(signature against JWKS, `iss`, `aud`, `exp`) and keeps it in the encrypted session
cookie — the browser never holds a token and never sends one. There is no second login,
no password, no token parsing and no `security` module here. There is one question:
**is this caller allowed to do this?**

**Files:**
- Create: `backend/app/access.py` (the whole of it — principal, roles, dependency)
- Modify: `devops/docker-compose.yml` (drop the `api` host port), `bff/app/proxy/forwarder.py`
- Test: `backend/tests/test_access.py`, `bff/tests/test_proxy.py`

**Interfaces:**
- Produces: `Role` enum (`ADMIN`, `HR_MANAGER`),
  `Principal(sub, email, display_name, roles: frozenset[str])`,
  `require_role(*roles) -> Depends` — the only thing routes import.
- Consumes: headers the BFF adds after decoding the token. Never anything the browser sent.

| Header | Source |
|---|---|
| `X-Gateway-Key` | shared secret — the caller is the BFF |
| `X-User-Sub` | `sub` claim, the audit identity |
| `X-User-Email` / `X-User-Name` | `email` / `preferred_username`, display only |
| `X-User-Roles` | `realm_access.roles`, comma-separated — **the only role source** |
| `X-Request-ID` | correlates BFF and service logs |

**The service never sees a token.** Not "does not verify one" — never receives one. The
BFF verifies every token, at every request, and forwards claims. One process parses
tokens, so there is one JWKS cache, one clock-skew policy, one decision about which claim
holds the roles. A second implementation would have to agree with the first forever, and
the day they disagree is a security incident.

The cost is that the service trusts its caller, which holds only while nothing else
*can* be its caller:

- `api` publishes **no host port** — reachable only over the compose network. (This is a
  change: it currently publishes `8000:8000`, which makes every header above forgeable
  with one `curl` from the host.)
- Every request carries `X-Gateway-Key` equal to `GATEWAY_KEY`, checked in middleware
  before routing. This authenticates *the BFF*, not the user — the one thing here that
  resembles authentication, and it is about the network hop, not the person.
- The BFF strips `Authorization` rather than forwarding it, and the service reads no such
  header. A token nobody is responsible for verifying is worse than no token: it invites
  a later reader to trust it. One trust path, stated once.

- [x] **Step 1: Write the failing tests** —
  `test_request_without_gateway_key_is_401`,
  `test_request_with_wrong_gateway_key_is_401`,
  `test_roles_header_is_split_and_normalised`,
  `test_missing_user_sub_is_401_even_with_a_valid_gateway_key`,
  `test_authorization_header_alone_grants_nothing` (the no-fallback rule),
  `test_forwarder_does_not_send_the_access_token_upstream`,
  `test_hr_manager_blocked_from_admin_route_with_403`,
  `test_admin_allowed_on_admin_route`,
  `test_hr_manager_allowed_on_shared_route`,
  `test_unknown_role_grants_nothing`,
  and in the BFF, `test_forwarder_sends_decoded_identity_headers`,
  `test_client_supplied_identity_headers_are_stripped` (the allowlist in
  `forwarder.py` already drops them — this pins it).
  *(Covers Review Focus #4.)*
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** In the BFF, `forward()` adds the identity headers from the
  `Session` the caller already resolved and stops sending `Authorization` upstream. In
  the service, middleware checks the gateway key; `require_role` raises 403 when
  `principal.roles` is disjoint from the allowed set. 401 means "I do not know who you
  are" and is settled upstream; 403 means "I know, and no".
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add role-based access control from forwarded claims`

### Task 4: Actor resolution for the audit trail

Not access control — storage. `audit_log.actor_id` is a foreign key into `app_user`, and
the only identity the request carries is a Keycloak `sub`. Something has to turn one into
the other, and "who changed this salary" must still answer in three years from the
database alone, with Keycloak unreachable and the realm long since rebuilt.

**Files:**
- Create: `backend/app/services/user.py`
- Test: `backend/tests/services/test_user.py`

**Interfaces:**
- Produces: `resolve_actor(db, principal) -> AppUser` — just-in-time row keyed on
  `external_id = sub`.
- Consumes: `Principal` (Task 3), `AppUser` (Task 2).

- [x] **Step 1: Write the failing tests** —
  `test_first_request_from_a_new_subject_creates_one_app_user_row`,
  `test_second_request_from_the_same_subject_creates_no_further_row`,
  `test_changed_email_in_the_token_updates_the_row_but_never_external_id`,
  `test_two_concurrent_first_requests_create_exactly_one_row` (the unique index on
  `external_id` decides it; the loser catches `IntegrityError` and re-reads).
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Read first, write only when something differs, so the steady
  state of a request is one `SELECT` and no transaction upgrade. Roles are **never**
  written to this row: a stored copy of an authorization fact drifts the moment someone
  edits the realm, and it drifts open.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): resolve the audit actor from the Keycloak subject`

---

## Phase 2 — Salary domain

### Task 5: FX service

**Files:**
- Create: `backend/app/services/fx.py`, `backend/app/api/fx.py`, `backend/app/schemas/fx.py`
- Test: `backend/tests/services/test_fx.py`

**Interfaces:**
- Produces: `get_rate(db, currency: str, as_of: date) -> Decimal` (most recent rate on or
  before `as_of`; raises `MissingFxRateError(currency, as_of)`),
  `to_usd(amount: Decimal, rate: Decimal) -> Decimal` (quantized to 2dp, `ROUND_HALF_UP`).
  Admin-only `GET/POST /fx-rates`.

- [x] **Step 1: Write the failing tests** — `test_returns_exact_rate_on_matching_date`,
  `test_returns_most_recent_rate_before_date`,
  `test_raises_missing_fx_rate_when_currency_has_no_rate` *(Review Focus #1)*,
  `test_raises_when_only_future_rates_exist`,
  `test_usd_to_usd_uses_rate_one`,
  `test_conversion_rounds_half_up_to_two_decimals`,
  `test_hr_manager_cannot_post_fx_rate` (403).
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Query is `ORDER BY as_of DESC LIMIT 1 WHERE as_of <= :as_of`.
  No silent fallback to `1.0` under any condition.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add FX rate lookup and USD conversion`

### Task 6: Audit trail writer

**Files:**
- Create: `backend/app/services/audit.py`
- Test: `backend/tests/services/test_audit.py`

**Interfaces:**
- Produces: `record(db, *, entity_type: str, entity_id: int, action: str, old: dict | None, new: dict | None, actor_id: int) -> AuditLog`.
  Caller supplies the session; `record` never commits.

- [x] **Step 1: Write the failing tests** — `test_record_persists_old_and_new_values`,
  `test_record_does_not_commit` (the row is absent after the caller rolls back),
  `test_decimal_values_serialize_as_strings_in_jsonb` (no float precision loss).
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Sharing the caller's session is the whole point: the audit row
  lives or dies with the mutation it describes.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add transactional audit trail writer`

### Task 7: Salary write service

**Files:**
- Create: `backend/app/services/salary.py`, `backend/app/schemas/salary.py`
- Test: `backend/tests/services/test_salary.py`

**Interfaces:**
- Consumes: `fx.get_rate`, `fx.to_usd` (Task 5), `audit.record` (Task 6).
- Produces: `set_salary(db, *, employee_id, amount, currency, effective_from, actor_id, change_reason=None) -> SalaryChange`
  — a frozen dataclass `(salary, changed)` rather than a bare `Salary`. An identical amount
  writes no row and no audit entry, and the caller still has to know which happened: the
  importer counts it as `salaries_unchanged`, the UI says "no change" instead of claiming a
  raise. Returning `None` for a no-op would lose the row the caller still needs;
  `get_current_salary(db, employee_id) -> Salary | None`;
  `get_salary_history(db, employee_id) -> list[Salary]`.

- [x] **Step 1: Write the failing tests** —
  `test_first_salary_creates_open_row`,
  `test_second_salary_closes_previous_row_effective_to_day_before`,
  `test_usd_amount_and_rate_frozen_on_row`,
  `test_later_fx_rate_change_does_not_alter_existing_row` (the constraint that justifies the
  whole design),
  `test_missing_fx_rate_raises_and_writes_nothing` *(Review Focus #1)*,
  `test_backdated_effective_from_rejected` *(Review Focus #3)*,
  `test_audit_row_written_with_old_and_new_amount`,
  `test_negative_amount_rejected`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Order inside one transaction: resolve rate → close the open row
  (`effective_to = effective_from - 1 day`) → insert the new row with frozen
  `usd_amount`/`fx_units_per_usd`/`fx_rate_date` → write audit. Backdating raises
  `BackdatedSalaryError`.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add effective-dated salary writes with frozen FX`

### Task 8: Employee service and API

Splits into two commits: the service, then the HTTP surface over it. `EmployeeService`
exists for the reason the component diagram gives — `EmployeeAPI` and `ImporterService`
both call it, so "department and band must exist, `employee_code` is unique, every
change is audited" is written once rather than once per caller. A second way to create
an employee would otherwise be a second way to create one wrongly.

**Files:**
- Create: `backend/app/services/employee.py`, `backend/app/api/employees.py`,
  `backend/app/api/reference.py`, `backend/app/schemas/employee.py`,
  `backend/app/schemas/reference.py`
- Modify: `backend/app/services/salary.py` (add `close_for_termination`)
- Test: `backend/tests/services/test_employee.py`, `backend/tests/api/test_employees.py`,
  `backend/tests/api/test_reference.py`

**Interfaces**, matching the class diagram in `design-notes.md`:
- Consumes: `salary.set_salary`, `salary.get_current_salary`, `salary.get_salary_history`,
  `salary.close_for_termination`, `audit.record`, `require_role`, `current_actor`.
- Produces, in `EmployeeService`:
  - `create(db, *, data, actor_id) -> Employee`
  - `update(db, employee_id, *, changes, actor_id) -> Employee` — partial, audited per field
  - `set_status(db, employee_id, *, status, termination_date, actor_id) -> Employee`
  - `search(db, *, q, country, department, band, status, sort, order, page, size) -> tuple[list, int]`
  - `get(db, employee_id) -> Employee | None`
- Produces, in `SalaryService`:
  - `close_for_termination(db, employee_id, *, termination_date, actor_id) -> Salary | None`
- Produces, in `api/`:
  - `GET /employees?q=&country=&department=&band=&status=&sort=&order=&page=&size=` → `{items, total, page, size}`
  - `GET /employees/{id}` → employee + current salary + history, each row carrying
    `created_at` and `created_by_name` so the detail page can render the history as a
    timeline of *changes* rather than a table of date ranges
  - `POST /employees` → create one by hand, for the people who arrive outside a file
  - `PATCH /employees/{id}` body `{job_title?, department_id?, band_id?, status?, termination_date?}`
  - `PUT /employees/{id}/salary` body `{amount, currency, effective_from, change_reason?}`
  - `GET /reference` → `{countries, currencies, departments, bands}`

**Currency on screen: USD is what the UI shows, local is what it stores.**
`SalaryRead` carries both on every payload - `amount` + `currency` (what the person is
actually paid) and `usd_amount` + `fx_units_per_usd` + `fx_rate_date` (what it was worth
when it was written). The list and the dashboard display USD, because it is the only
figure comparable across countries and the only one that can be summed.

Two screens stay local, and they are not an inconsistency:

  * **the edit form**, because `SalaryWrite` refuses `usd_amount` on purpose - letting
    the browser decide what a salary is worth in the reporting currency would make every
    reported total client-controlled;
  * **the detail page**, which shows the local amount with the USD figure and the rate
    beside it, because a converted number nobody can explain is a number nobody trusts.

The USD figure is frozen at write time, so two people on identical local pay can show
different USD amounts if their rows were written under different rates. That is correct
and is the whole point of freezing - but it is why the rate and its date are published
alongside, rather than a bare dollar figure that looks wrong.

**Why salary is a sub-resource and not a field on PATCH.** Pay is effective-dated: a
change closes the open row and inserts a new one. Editing it through the same endpoint
that renames a job title would make one request half an UPDATE and half an INSERT, with
two different audit meanings. Separate endpoints, separate rules.

**Why PATCH and not PUT on the employee.** The UI edits one or two fields from a form
that was loaded minutes ago. A PUT would send the whole record back and silently
overwrite whatever changed in between.

**Setting status to INACTIVE is not a field edit.** It must close the open salary row
with `termination_date` in the same transaction — `ck_employee_inactive_dated` already
refuses a leaver with no date, so the two move together or the write fails. That is what
`close_for_termination` is for, and it is the one place `EmployeeService` calls
`SalaryService`. The dependency runs in that direction only: `salary.py` reads the
`Employee` model but never calls `employee.py`, or the two would cycle.

**`GET /reference` exists because three features need it.** The edit form needs a
department dropdown, the dashboard needs filter options, and the import error report
says "unknown department" about a row that failed a foreign key. Without it the UI
hard-codes lists that must match the database exactly, and drift between them reads as
a bug in the import.

#### 8a — the service

- [x] **Step 1: Write the failing tests** —
  `test_create_writes_an_audit_row`,
  `test_duplicate_employee_code_raises_rather_than_integrity_error`,
  `test_an_unknown_department_raises_a_named_error`,
  `test_update_audits_only_the_fields_that_changed`,
  `test_update_with_no_actual_change_writes_no_audit_row`,
  `test_set_status_inactive_closes_the_open_salary_row`,
  `test_set_status_inactive_without_a_date_is_refused`,
  `test_reactivating_does_not_reopen_the_closed_salary_row` *(the asymmetry is deliberate:
  rehiring is a new salary decision, not an undo)*,
  `test_search_matches_name_or_employee_code_case_insensitively`,
  `test_filters_combine_as_and`,
  `test_an_employee_with_no_salary_still_appears_in_the_list` *(a LEFT JOIN, not an inner
  one — a new hire with no pay row yet must not vanish from the list)*.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Every mutation writes its audit row in the same transaction.
  `update` diffs before writing, so an unchanged field is not recorded as a change.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add employee service with audited partial updates`

#### 8b — the API

- [x] **Step 1: Write the failing tests** — `test_list_paginates_with_total_count`,
  `test_page_size_capped_at_200`,
  `test_invalid_sort_field_returns_422_not_500` *(no SQL injection via sort column)*,
  `test_sort_by_usd_amount_desc`,
  `test_hr_manager_can_edit_a_salary`,
  `test_hr_manager_cannot_create_an_employee` *(creation is an admin act; editing pay is
  the HR manager's job)*,
  `test_put_salary_returns_new_row_and_closes_old`,
  `test_put_salary_with_an_unchanged_amount_reports_no_change`,
  `test_reference_returns_every_list_the_edit_form_needs`,
  `test_detail_includes_current_salary_and_history`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** The list query LEFT JOINs `salary` on `effective_to IS NULL`
  so it hits the partial index. Sort fields come from an allow-list dict mapping API name
  → column; anything else is 422 and never reaches SQL.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add employee search, detail, edit and reference endpoints`

---

## Phase 3 — Bulk data and analytics

### Task 9: Excel / CSV import

**Files:**
- Create: `backend/app/services/importer.py`, `backend/app/api/imports.py`, `backend/app/schemas/imports.py`
- Test: `backend/tests/services/test_importer.py`, fixtures in `backend/tests/fixtures/`

**Interfaces:**
- Consumes: `salary.set_salary`.
- Produces: `parse(file: BinaryIO, filename: str) -> list[RawRow]`,
  `validate(rows) -> tuple[list[ValidRow], list[RowError]]`,
  `run_import(db, file, filename, actor_id) -> ImportJob`.
  `POST /imports` (multipart) → `ImportJob`; `GET /imports/{id}` → status + errors.
  `RowError = {row_number: int, field: str, message: str}`.

- [x] **Step 1: Write the failing tests** —
  `test_parses_xlsx_and_csv_to_same_rows`,
  `test_missing_required_column_fails_whole_file_before_any_row`,
  `test_invalid_currency_code_reported_with_row_number`,
  `test_unknown_employee_code_reported_with_row_number`,
  `test_missing_fx_rate_reported_as_row_error_not_exception` *(Review Focus #1)*,
  `test_file_with_one_bad_row_commits_nothing` *(Review Focus #2)*,
  `test_valid_file_of_10000_rows_commits_all`,
  `test_a_file_over_the_row_limit_is_refused_before_parsing_rows` (413),
  `test_re_uploading_an_unchanged_file_writes_no_salary_row`,
  `test_a_duplicate_employee_code_within_one_file_is_reported`,
  `test_one_bad_row_in_ten_thousand_still_commits_nothing`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Two passes: parse+validate everything in memory first, then
  commit in one transaction only if `errors` is empty. 10,000 rows is ~5 MB and a few
  seconds — synchronous is correct here; a job queue is deferred until a measurement says
  otherwise *(see ADR-0004)*.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add atomic Excel/CSV import with per-row error reporting`

### Task 10: Dashboard aggregates

**Files:**
- Create: `backend/app/services/analytics.py`, `backend/app/api/dashboard.py`, `backend/app/schemas/dashboard.py`
- Test: `backend/tests/services/test_analytics.py`

**Interfaces:**
- Produces: `summary(db, *, country=None, department=None) -> DashboardSummary`
  `{total_payroll_usd, average_salary_usd, median_salary_usd, highest, lowest, employee_count,
  by_country: [...], by_department: [...], by_band: [...], currency_breakdown: [...]}`.
  `GET /dashboard?country=&department=&band=`.

**`by_band` carries `rank`, and is ordered by it.** Bands are ordered and their names are
not: sorted as text they come out L1, L10, L11, L2, and an average-pay-by-band chart in
that order is meaningless. `band.rank` exists for exactly this.

Every figure aggregates `salary.usd_amount`, frozen at write time. Nothing here converts
a currency - correcting an FX rate must not silently rewrite a quarter Finance has
already seen.

- [x] **Step 1: Write the failing tests** —
  `test_totals_use_usd_amount_not_local_amount`,
  `test_only_current_salary_rows_counted` (closed history rows excluded),
  `test_employees_without_salary_excluded_from_average_but_counted_in_headcount`,
  `test_empty_filter_result_returns_zeros_not_null`,
  `test_filters_apply_to_every_aggregate_consistently`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement** as one SQL statement per group-by over
  `employee JOIN salary ON effective_to IS NULL`. Aggregation happens in Postgres;
  no row set is pulled into Python.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add dashboard aggregates computed in SQL`

### Task 11: CSV export

**Files:**
- Modify: `backend/app/api/employees.py`, `backend/app/services/employee.py` (`export_rows`),
  `backend/app/db.py` (`streaming_session`, injectable so a transactional test can see its own data)
- Test: `backend/tests/api/test_export.py`

**Interfaces:**
- Produces: `GET /employees/export?<same filters as list>` → `text/csv` streaming response.

- [x] **Step 1: Write the failing tests** — `test_export_respects_active_filters`,
  `test_export_ignores_pagination_and_returns_all_matches`,
  `test_export_includes_local_amount_currency_and_usd_amount`,
  `test_formula_injection_prefix_escaped` (a name starting `=` is quoted, not exported raw).
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement** with `StreamingResponse` over a server-side cursor so a 10,000-row
  export never materializes in memory.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(backend): add filtered CSV export`

---

## Phase 4 — Frontend

### Task 12: Frontend scaffold, API client, session wiring

There is no login page. The SPA has no credentials to collect, no token to store and no
role to assert — it asks the BFF who it is talking to, and is redirected to Keycloak if
the answer is nobody.

**Files:**
- Create: `frontend/src/api/client.ts` (+ `patch`), `frontend/src/api/types.ts`,
  `frontend/src/api/resources.ts`, `frontend/src/auth/` (`useAuth`, `<RequireAuth>`),
  `frontend/src/theme/` (`tokens.css`, `utilities.css`, `theme.ts`),
  `frontend/src/layout/AppShell.tsx`, `frontend/src/providers.tsx`, `frontend/src/queryClient.ts`
- Test: `frontend/src/api/client.test.ts`, `frontend/src/auth/RequireAuth.test.tsx`

**Interfaces:**
- Produces: `api.get/post/put<T>(path, body?)` sending `credentials: 'include'` and the
  `X-CSRF-Token` header on every mutating call, throwing a normalized
  `ApiError {status, message, fieldErrors}`; `useAuth()` returning
  `{user, roles, loading}` from `GET /auth/me`; `<RequireAuth>` guard.

- [x] **Step 1: Write the failing tests** — `test_client_sends_credentials_include`,
  `test_mutating_call_sends_csrf_header_from_the_readable_cookie`,
  `test_get_request_sends_no_csrf_header`,
  `test_401_redirects_to_auth_login_and_stores_no_token`,
  `test_no_token_is_ever_written_to_localstorage_or_sessionstorage`,
  `test_require_auth_renders_children_once_auth_me_resolves`.
- [x] **Step 2: Run** `npm test` → FAIL.
- [x] **Step 3: Implement.** Nothing auth-bearing is stored by JavaScript: the session is
  an httpOnly encrypted cookie the SPA cannot read. `roles` from `/auth/me` drives what
  is *rendered* — which menu items exist, which buttons are enabled — and is never sent
  anywhere. Hiding a button is a courtesy; Task 3 is the control.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): scaffold React app with cookie-session API client`

### Task 13: Employee list page

**Files:**
- Create: `frontend/src/pages/EmployeeListPage.tsx` (+ `.module.css`),
  `frontend/src/format/money.ts`, `frontend/src/test/render.tsx`,
  `frontend/src/test/fixtures.ts`

  No separate `EmployeeTable.tsx`: the table is twenty lines of Mantine markup used by
  one page. Extracting it would buy a prop interface and an indirection, not reuse.
- Test: `frontend/src/pages/EmployeeListPage.test.tsx`

- [x] **Step 1: Write the failing tests** — `test_renders_page_of_employees`,
  `test_typing_in_search_debounces_and_refetches`,
  `test_changing_country_filter_resets_to_page_one`,
  `test_column_header_click_toggles_sort_order`,
  `test_shows_empty_state_when_no_matches`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement** with TanStack Query (server state) + TanStack Table in *manual*
  pagination/sorting mode — the server is the source of truth; the table never sorts
  client-side over a partial page. Search debounce 300 ms.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): add employee list with server-side search, filter, sort, pagination`

### Task 14: Employee detail and salary edit

**Files:**
- Create: `frontend/src/pages/EmployeeDetailPage.tsx` (+ `.module.css`),
  `frontend/src/components/SalaryEditForm.tsx`,
  `frontend/src/components/SalaryTimeline.tsx` (+ `.module.css`)
- Test: `frontend/src/pages/EmployeeDetailPage.test.tsx`

- [x] **Step 1: Write the failing tests** — `test_shows_current_salary_in_local_and_usd`,
  `test_shows_salary_history_newest_first`,
  `test_submitting_edit_posts_amount_currency_and_effective_date`,
  `test_missing_fx_rate_error_surfaces_as_readable_message` *(Review Focus #1 at the UI)*,
  `test_history_refreshes_after_successful_edit`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Display both figures everywhere money appears —
  `₹12,00,000 INR · $14,380 USD @ 83.45` — the rate is shown because an unexplained
  converted number is the first thing an HR Manager will distrust.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): add employee detail with salary history and edit`

### Task 15: Dashboard page

**Files:**
- Create: `frontend/src/pages/DashboardPage.tsx`, `frontend/src/components/StatCard.tsx`
- Test: `frontend/src/pages/DashboardPage.test.tsx`

- [x] **Step 1: Write the failing tests** — `test_renders_total_average_highest_lowest_in_usd`,
  `test_country_filter_updates_every_stat`,
  `test_renders_zero_state_without_crashing`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** Stat cards + three charts via `@mantine/charts` (Recharts
  underneath): payroll by country, average pay by band in rank order, headcount by
  department. Each chart is paired with its own table, because a bar conveys the ranking
  and a table conveys the figure, and people need both. Every figure labelled **USD**.
  Currency breakdown is a table only - it carries local totals, which do not belong on a
  USD axis.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): add salary analytics dashboard`

### Task 16: Import page

**Files:**
- Create: `frontend/src/pages/ImportPage.tsx` (+ `.module.css`),
  `frontend/src/components/ImportErrorReport.tsx`
- Modify: `frontend/src/api/client.ts` (`upload`, multipart), `frontend/src/api/resources.ts`

  The template is generated in the browser from a constant rather than fetched: an
  endpoint for a constant is an endpoint that can be down.
- Test: `frontend/src/pages/ImportPage.test.tsx`

- [x] **Step 1: Write the failing tests** — `test_uploads_file_and_shows_success_count`,
  `test_renders_row_errors_in_a_table_with_row_numbers` *(Review Focus #2 at the UI)*,
  `test_states_clearly_that_nothing_was_imported_when_errors_exist`,
  `test_offers_template_download`.
- [x] **Step 2: Run** → FAIL.
- [x] **Step 3: Implement.** The error report is the feature — a failed import must tell the
  HR Manager exactly which rows to fix, because the alternative is them going back to Excel.
- [x] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat(frontend): add bulk import with per-row error report`

---

## Phase 5 — AI chatbot (P1)

### Task 17: Chatbot over whitelisted query tools

**Files:**
- Create: `backend/app/services/chat.py`, `backend/app/api/chat.py`, `frontend/src/pages/ChatPage.tsx`
- Test: `backend/tests/services/test_chat.py`

**Interfaces:**
- Consumes: `analytics.summary`, `employee` list service.
- Produces: `TOOLS` (Anthropic tool schemas) and `answer(db, question: str, user: User) -> ChatAnswer {text, used_tools: [...], figures: [...]}`.
  `POST /chat` body `{question}`.

Tools exposed to the model — **these and nothing else**:
`get_dashboard_summary(country?, department?)`,
`count_employees(country?, department?, min_usd?, max_usd?)`,
`top_paid(n, country?, department?)`,
`salary_distribution(bucket_size_usd, country?)`,
`list_countries()`, `list_departments()`.

- [ ] **Step 1: Write the failing tests** (model mocked — assert the *loop*, not the LLM) —
  `test_question_about_average_salary_calls_get_dashboard_summary`,
  `test_tool_results_are_echoed_as_figures_in_the_answer`,
  `test_model_cannot_invoke_an_undeclared_tool`,
  `test_tool_call_runs_under_the_requesting_users_role` *(an HR_MANAGER's chat cannot read
  what their roles cannot read)*,
  `test_unanswerable_question_returns_refusal_without_numbers` *(Review Focus #5)*,
  `test_pii_question_refused`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the tool-calling loop (max 5 turns). System prompt: answer only
  from tool results, always state the filter that produced a figure, refuse rather than
  estimate. No raw SQL generation, no table schema in the prompt. *(ADR-0003.)*
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `feat: add AI chatbot over whitelisted salary query tools`

---

## Phase 6 — Scale proof and artifacts

### Task 18: Seed 10,000 employees and measure

**Files:**
- Create: `backend/scripts/seed.py`, `plan_doc/performance.md`
- Test: `backend/tests/test_performance.py`

`plan_doc/performance.md` rather than `docs/performance.md`: `docs/` was retired, and a
performance note is a design artefact, not a user manual.

- [x] **Step 1: Write the failing test** — `test_list_endpoint_under_300ms_with_10000_employees`
  and `test_dashboard_endpoint_under_500ms_with_10000_employees` (marked `@pytest.mark.perf`,
  excluded from the default run). Four more came with them: a last-page test (the trigger for
  keyset pagination), a filtered-dashboard test (a filter must narrow the work, not add to
  it), and `test_the_fixture_really_built_ten_thousand_employees`, which guards the other
  five — a budget met against an empty table is met for the wrong reason.
- [x] **Step 2: Run** → FAIL (`ModuleNotFoundError: No module named 'scripts'`).
- [x] **Step 3: Implement** `seed.py` generating 10,000 employees across 12 countries,
  12 departments, 10 currencies, with 1–4 salary rows each (18,741 salary rows, 517 of the
  people leavers with no open row). Deterministic (`RNG_SEED`), weighted unevenly, written as
  bulk INSERT — but the FX conversion goes through `fx.to_usd`, since a stored USD figure
  that disagrees with the application's is the one defect this data could hide. `EXPLAIN
  ANALYZE` recorded in `plan_doc/performance.md`, with and without the indexes.
- [x] **Step 4: Run** `pytest -m perf -v` → PASS (5 passed, 35.7 s).
- [ ] **Step 5: Commit** — `perf: add 10k-employee seed and measured query baselines`

**What the measurement found, and it is not what the plan assumed:** at 10,000 rows the
indexes make no measurable difference — Postgres chooses a sequential scan over all eight of
them, correctly, because the table is 276 pages. They only start paying at 100,000 rows, and
then only for selective filters (`ix_employee_country`, 27.8 ms against 44.6 ms). Both budgets
hold at 10,000 with an order of magnitude to spare, and still hold at 100,000. Reported rather
than quietly dropped, because "we added indexes and it got faster" would have been false.

### Task 19: Architecture notes, ADRs, trade-offs, AI usage log

**Files:**
- Create: `plan_doc/architecture.md`, `plan_doc/tradeoffs.md`, `plan_doc/ai-usage.md`,
  `plan_doc/adr/0001-effective-dated-salary-history.md`,
  `plan_doc/adr/0002-freeze-fx-rate-at-write-time.md`,
  `plan_doc/adr/0003-chatbot-uses-whitelisted-tools-not-sql-generation.md`,
  `plan_doc/adr/0004-synchronous-import-no-job-queue.md`,
  `plan_doc/adr/0005-two-roles-no-approval-workflow.md`,
  `plan_doc/adr/0006-no-token-at-the-salary-service.md`
- Modify: `README.md`

- [ ] **Step 1: Write** `plan_doc/architecture.md` — context diagram, component diagram and the
  salary-write sequence, as Mermaid so they diff in review.
- [ ] **Step 2: Write** the five ADRs in Context / Decision / Consequences form. Each states
  what was rejected and why.
- [ ] **Step 3: Write** `plan_doc/tradeoffs.md` — what was deliberately not built and the
  condition that would change the answer (e.g. "add a job queue when import p95 > 30 s or
  files exceed 50 MB").
- [ ] **Step 4: Write** `plan_doc/ai-usage.md` — where AI was used (plan drafting, test
  scaffolding, chatbot tool design), the prompts that mattered, and what was rejected from
  AI output and why.
- [ ] **Step 5: Commit** — `docs: add architecture, ADRs, trade-offs and AI usage log`

---

## Git Strategy

The submission is graded on commits showing the development process, so the history is
itself a deliverable.

- **One commit per task**, in plan order. Message format: Conventional Commits
  (`feat(backend):`, `test:`, `docs:`, `perf:`, `refactor:`, `chore:`). The body says *why*,
  not what — the diff already says what.
- **Tests land in the same commit as the code they cover.** A commit that adds behaviour
  without its test is incomplete, and splitting them hides the TDD order.
- **Commit on green.** Never commit a failing suite; the red step is a local state, not a
  commit.
- **`main` only.** A solo one-week build does not need branches, and a linear history reads
  as a narrative.
- **Documentation commits are first-class** and interleaved with code, so the history shows
  decisions being made as the work happened, not retro-fitted at the end.
- The first three commits (`chore: init`, `docs: requirements`, `docs: plan`) land **before
  any application code** — evidence that the requirement and the design preceded the build.

## Open Questions for the Reviewer

Deliberately left to the implementer, flagged so they are visible choices and not oversights:

1. **FX rate source.** The MVP assumes an admin-maintained table. A live feed (e.g. ECB
   daily) is a one-file change to `services/fx.py` and is listed in `tradeoffs.md`.
2. **Median salary** requires `percentile_cont` over the full set; if it measures slow at
   10k rows, it drops from the dashboard rather than gaining a cache.
3. **Chatbot model choice** — `claude-sonnet-5` is assumed as the default for cost/latency;
   tool-calling behaviour is identical across the Claude 5 family, so this is reversible.
