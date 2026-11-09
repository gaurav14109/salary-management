# Feature plan — and what was actually built

One row per feature, read left to right: the BFF proves who you are, the API decides
what you may do and owns the rules, the SPA renders it. The assistant answers
questions about it without being handed any of those powers.

Every feature below is implemented. The build log at the end maps each step to the
commit that landed it.

## The four services

| Service | Owns | Never does |
|---|---|---|
| **BFF** | OIDC login, session cookie, token verification and refresh, CSRF, proxying | Business rules, database |
| **API** | Rules, money, audit, the database. Role checks only | Authenticate a user |
| **Chat** | Scope, grounding, tool calls, streaming the answer | Reach the database directly |
| **Frontend** | Rendering, filters, formatting | Compute money, validate against reference data |

The browser holds no token. The BFF verifies the JWT, then calls the API with the
claims as headers (`X-User-Sub`, `X-User-Roles`, `X-Gateway-Key`); `Authorization`
never crosses that hop. The API and the chat service are reachable only on the
compose network.

## Roles, as implemented

Two realm roles, no hierarchy.

| | Read employees, dashboard, imports, FX, reference | Write |
|---|---|---|
| `hr_manager` | yes | yes — the only role that writes anything |
| `admin` | yes | **no** |

`admin` ended up read-only on purpose. It was easier to argue about "who may change
pay" when the answer was one role rather than two, and the frontend hides what the
API would refuse rather than letting the user discover it as a 403.

## Features

### 1. Sign in

| | |
|---|---|
| **BFF** | `GET /auth/login` → Keycloak → `GET /auth/callback` → encrypted httpOnly cookie. `GET /auth/me`, `POST /auth/logout`. Refresh happens here. |
| **API** | — |
| **Frontend** | `AuthProvider` asks `/auth/me` once; `RequireAuth` gates every route, `RequireRole` gates the write ones. Any 401 returns the user to sign-in carrying the page they were on. |

### 2. Browse employees

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `GET /employees` — search, filter by country/department/band/status, sort on an allow-list, page of 50, current salary joined server-side |
| **Frontend** | `EmployeeListPage`. Type-to-search filters; 45 countries is a scroll, not a choice |

### 3. Employee record

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `GET /employees/{id}` with full salary history · `POST /employees` · `PATCH /employees/{id}` · `PUT /employees/{id}/status` — all writes `hr_manager` only |
| **Frontend** | `EmployeeDetailPage`. History as a timeline, because effective-dated pay is a sequence of periods, not a number |

### 4. Record a pay change

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `PUT /employees/{id}/salary` — closes the open period at `effective_from - 1 day`, opens the new one, freezes the FX rate, writes the audit row in the same transaction. Backdating refused (422); an unchanged amount is a no-op |
| **Frontend** | `SalaryEditForm`. Amount, currency, effective date, reason |

### 5. Bulk import

| | |
|---|---|
| **BFF** | Proxy, multipart passthrough |
| **API** | `POST /imports/preview` — parse and validate, write nothing · `POST /imports` — queued, one worker, one transaction, all-or-nothing; the `ImportJob` row persists even on failure · `GET /imports`, `GET /imports/{id}` |
| **Frontend** | `ImportPage`. A drop previews; Import is live only when the file would go in. The grid is read-only and windowed — 10,000 rows render as a few dozen. A running job is polled by id, so leaving the page does not lose it |

Preview and import call the **same** `validate()`, so the screen cannot promise a
clean run the import then refuses.

Import moved from inline to a background queue once a 10,000-row file made the
request hold the connection open for longer than any proxy wants to wait.

### 6. Export

| | |
|---|---|
| **BFF** | Streams the response through |
| **API** | `GET /employees/export` — server-side cursor, `yield_per`, CSV formula-injection escaped |
| **Frontend** | A link. The file is built server-side |

### 7. Dashboard

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `GET /dashboard` — count, total, mean, median (`percentile_cont`), high/low, and breakdowns by country, department, band, currency. Aggregated in SQL over `current_salary` |
| **Frontend** | `DashboardPage`. Charts plus tables, every figure in USD and labelled. Nothing summed in the browser |

### 8. FX rates

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `GET /fx-rates` (both roles), `POST /fx-rates` (`hr_manager`). No rate for a date ⇒ the salary write is refused, never converted at 1.0 |
| **Frontend** | Nav item shown only to the role that may use it |

### 9. Reference data

| | |
|---|---|
| **BFF** | Proxy |
| **API** | `GET /reference` — countries, currencies, departments, bands in one call |
| **Frontend** | Cached once; feeds every filter and form |

### 10. Assistant

| | |
|---|---|
| **BFF** | Proxies `/ask` and refuses to buffer it — the stream passes through chunk by chunk |
| **Chat** | `POST /ask`. Scope check first, then a LangChain turn over a whitelisted tool set, filtered by the caller's roles. It calls the API as the user, so it cannot read what the user cannot. Refuses to answer what the product rules do not cover. Salary figures never reach the logs |
| **Frontend** | `ChatWidget` in the shell — askable from any page, answer streams in |

The assistant has no database credentials. Every fact it states came back from a
tool call that went through the same role check as the UI.

## How it was built

Thirty commits, in this order. Each step was usable before the next began.

### Stage 1 — foundations (2026-11-07)

| Commit | What landed |
|---|---|
| `d35c27c` | Repo skeleton, CI, compose stack. BFF end to end: OIDC login, encrypted session cookie, CSRF, token verification, proxy. Alembic `0001` — the full schema and the constraints that make bad states unrepresentable. Frontend auth shell |
| `24a11b1` | Backend models, structured logging, the error taxonomy, `gateway` (is the caller the BFF?) and `access` (what may this principal do?), `UserService` to resolve the audit actor. Keycloak config moved to `devops/` |
| `b6df5e0` | Every service and route: employee, salary, FX, audit, importer, analytics. Reference seed data. ~3,000 lines of tests alongside |

### Stage 2 — import and scale proof (2026-11-08)

| Commit | What landed |
|---|---|
| `acbfd16` | Import wired through the API, `POST /imports/preview` added, seed script, and the performance tests that measure 10,000 employees rather than assume them |

### Stage 3 — the frontend (2026-11-08)

Built bottom-up: tokens before components, client before pages.

| Commit | What landed |
|---|---|
| `7e94f4c` | Design system — tokens, theme, utilities |
| `bfbf047` | Typed API client, resources, money formatting, query client, test harness and fixtures |
| `d0c286f` | Role-aware application shell |
| `23f4aaa` | Employee list: search, filters, CSV export |
| `57a98bb` | Employee detail: salary timeline and the pay-change form |
| `3fd3481` | Payroll dashboard |
| `fec78f4` | Import page with preview grid and error report; sample CSVs including a 10,000-row file |
| `a07130a` | A session that expires mid-use sends the user to sign in, carrying the page |
| `033b85d` | Routes wired behind the auth guard |
| `53b8a16` | SPA served from the stack; Keycloak fixed for local HTTP |
| `4a68bfa` | Axis formatter made deterministic |

### Stage 4 — the assistant (2026-11-09)

A separate deployable from the first commit, so it could never quietly grow database
access.

| Commit | What landed |
|---|---|
| `b30a657` | Chat service stood up: config, gateway key, access, errors, logging, Dockerfile |
| `2ca6ece` | Scope check and the written-down product knowledge it answers from |
| `8c99c10` | Tool set, filtered by the caller's roles; the salary API client |
| `2a537e2` | The LangChain turn, and the refusal to answer ungrounded |
| `58ddc0b` | `/ask` exposed behind the gateway; pay figures kept out of the logs |
| `be4e6f1` | End-to-end streaming — fixed every hop that would have buffered it (BFF forwarder, nginx) |
| `a598344` | Eval harness scoring the assistant against the real database and the real model |
| `c38c0f0` | `ChatWidget` in the shell, streaming client |
| `f4db690` | Assistant added to the compose stack; the stack still starts without an API key |

### Stage 5 — import as a queue (2026-11-09)

| Commit | What landed |
|---|---|
| `97896b9` | Alembic `0003`: import jobs queued and claimed. Background worker, one file at a time |
| `2cb1032` | The frontend follows a running import by id — leaving the page no longer loses it |
| `222d140` | Department column showed the job title. Fixed |

### Stage 6 — locking down admin (2026-11-09)

| Commit | What landed |
|---|---|
| `17d7f37` | Every write route narrowed to `hr_manager`. `admin` reads everything, writes nothing |
| `e35dc73` | `RequireRole` guard; the UI stops offering an admin the things the API would refuse |
| `d857768` | The seeded admin user holds `admin`, and nothing else |

## Rules that cut across every feature

- Money is `Decimal` in Python, a string on the wire, never a float
- FX frozen at write time; a report run twice gives the same answer
- Salary is never updated in place
- Audit rows are written in the same transaction as the change
- The database enforces what the application merely remembers
- The assistant reads through the same role checks as the UI, never around them
