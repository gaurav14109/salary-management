# Employee Salary Management

Web application replacing ACME's Excel-based salary process for 10,000 employees across
multiple countries. Single persona: the HR Manager.

## The documents

Grouped by what you would be checking. Each group is in reading order.

### Start here — 15 minutes

| Document | Why |
|---|---|
| [`requirements.md`](requirements.md) | What was asked for, what was deliberately left out, and why |
| [`Architecture Diagram.docx`](architecture.md) | The system at four zoom levels: context, containers, one request end to end, deployment |
| [`adr/`](adr/) | The decisions that shaped it — six short records, one decision each |
| [`security.md`](security.md) | What protects the salary data, mapped to the OWASP Top 10 |

### 1. Requirements and scope

| Document | What it covers |
|---|---|
| [`requirements.md`](requirements.md) | Goal, persona, features, and the exclusions with their reasoning |
| `Employee Salary Management.docx` | The same requirement as originally authored |

### 2. Planning and design notes

| Document | What it covers |
|---|---|
| [`implementation-plan.md`](implementation-plan.md) | The build order: lifecycle stages and seven vertical slices, backend then frontend for each |
| [`feature-plan.md`](feature-plan.md) | The same order read feature-first: BFF, then backend API, then frontend |
| [`design-notes.md`](design-notes.md) | The working design note — ER diagram, class diagram, key flows |
| [`model-review.md`](model-review.md) | The data model reviewed against the requirements: 10 findings, the fixes, the revised ER diagram |
| [`plans/2026-11-05-salary-management.md`](plans/2026-11-05-salary-management.md) | Task-level detail: exact signatures, exact test names, one commit each |
| `Planning.docx`, `Design.docx` | The same material as originally authored |

### 3. Architecture and diagrams

| Document | What it covers |
|---|---|
| [`chat-request-flow.md`](chat-request-flow.md) | One chat question end to end: scope gate, notes, tools, grounding guard |
| `diagrams/` | Rendered PNGs: context, containers, deployment, login, save salary, import, request flow |
| `Architecture Diagram.docx` | The same diagrams as originally authored |

### 4. Trade-offs and decisions

Each ADR states the decision, what was given up, and the condition that would
change the answer. Two were reversed by later evidence — those are the interesting
ones.

| Record | Decision |
|---|---|
| [`adr/0001`](adr/0001-effective-dated-salary-history.md) | Salary is a dated history, never a field that is overwritten |
| [`adr/0002`](adr/0002-freeze-fx-rate-at-write-time.md) | The USD figure and its FX rate are frozen onto the row at write time |
| [`adr/0004`](adr/0004-synchronous-import-no-job-queue.md) | Import runs inside the request — **superseded**, on its own measurement |
| [`adr/0006`](adr/0006-no-token-at-the-salary-service.md) | No token crosses to the salary service; claims arrive as headers |
| [`adr/0007`](adr/0007-imports-run-in-a-background-queue.md) | Import runs in a background queue, one file at a time / celeary/RabbitMQ can be the future scope for backgrund processing |
| [`adr/0008`](adr/0008-uploaded-files-live-on-the-job-row.md) | The uploaded file lives on the job row; MinIO is future scope, with the trigger written down |

### 5. Performance

| Document | What it covers |
|---|---|
| [`performance.md`](performance.md) | Measured baselines at 10,000 and 100,000 employees, with raw `EXPLAIN ANALYZE` output — including the index that turned out to make no difference, and the 72-second import that reversed ADR-0004 |

### 6. Security

| Document | What it covers |
|---|---|
| [`security.md`](security.md) | Each OWASP Top 10 risk in one line, what was built against it, and the two settings that are deliberately relaxed for local development |

### 7. AI usage

| Document | What it covers |
|---|---|
| [`ai-usage.md`](ai-usage.md) | The prompts as actually written, what was rejected and why, how outputs were verified, and a table of four bugs AI introduced with how each was caught |

---

## The three decisions that shape everything else

1. **Salary is an effective-dated history, not a field.** A change closes the current row
   and inserts a new one. A partial unique index (`WHERE effective_to IS NULL`) makes
   "exactly one current salary per employee" a database guarantee rather than an
   application convention. Without this, "what did we pay in Q2" is unanswerable.

2. **The USD figure and the FX rate are frozen onto the salary row at write time.** Salary
   is stored in the employee's local currency; every report is in USD. Converting at read
   time would mean correcting one FX rate silently rewrites last quarter's payroll total.

3. **The AI chatbot calls whitelisted query functions; it does not generate SQL.** The
   model picks from six aggregate tools and never sees the schema. A bad model output is a
   wrong chart, not a data leak — and the tools execute under the asking user's role, so
   the chatbot cannot read what the JWT cannot.

## Reading the commit history

Commits are feature-by-feature, in build order, with tests in the same commit as the
code they cover. Tags mark the phases, so the history can be read either way:

| Tag | What it added |
|---|---|
| `v1.0.0` | Authentication — Keycloak, the BFF, the schema |
| `v2.0.0` | Models, logging, access control |
| `v3.0.0` | Bulk import |
| `v4.0.0` | The HR Manager interface |
| `v5.0.0` | The chat assistant, as a separate service |
| `v5.1.0` | Imports move to a background queue |
| `v5.2.0` | Admin becomes read-only |

```bash
git log --oneline --reverse          # the whole build, oldest first
git tag -n20                         # what each version added, and why
git log --oneline v4.0.0..v5.0.0     # the commits behind one version
```

## Running it

```bash
cd devops
docker compose up -d --wait
```

Then open <http://localhost:5173>.

That is the whole system: Postgres, Keycloak, the API, the BFF and the SPA behind
nginx. Migrations run as a one-shot service and the API waits for them to finish.

To work on one piece with hot reload, stop that container and run it on the host:

```bash
docker compose stop web
cd ../frontend && npm install && npm run dev     # Vite on :5173, same proxying

docker compose stop api
cd ../backend && pip install -e ".[dev]" && uvicorn app.main:app --reload
```

A host-run API also needs the BFF's `SALARY_API_URL` repointed from `http://api:8000`
to `http://host.docker.internal:8000`, since the compose network name does not resolve
from the host.

The browser only ever addresses `http://localhost:5173`. nginx serves the SPA there
and proxies `/api` and `/auth` to the BFF, including the OIDC callback, so the
application is one origin. The one exception is Keycloak's own login page on :8080,
which must not be proxied: the password is typed on the identity provider's origin,
and that is the point of delegating authentication to it.

:8000 and :8001 are published for inspection and for host-run development. Nothing
in the frontend addresses either.

See [`backend/README.md`](../backend/README.md) for the database workflow — migrations,
schema changes, and why `ddl/` is a snapshot rather than the live schema.
