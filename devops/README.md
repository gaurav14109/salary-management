# devops

The local stack and the database schema.

```
docker-compose.yml   web :5173 (the only address a user types), Keycloak :8080,
                     BFF :8001 and Postgres :5432. The API publishes no port
ddl/*.sql            the schema as plain SQL — a snapshot, executed only by 0001
alembic/versions/    the migration chain
alembic.ini          alembic is run from this directory
create_schema.py     local convenience: --reset drops everything, then migrates
seed/*.sql           reference data every database needs before anything can be created
keycloak/            the realm: clients, roles, seed users, imported on first start
create_user.py       add a user to a realm that is already running
postgres-init/       creates Keycloak's own database, once, on an empty volume
```

Everything about identity lives here rather than in `backend/`, for the same reason
the schema does: a realm is infrastructure. The salary service holds no realm, no
client, no password and never sees a token.

## Reference data

`employee.department_id` and `salary.currency` are NOT NULL foreign keys, the import
refuses an unknown value rather than inventing one, and there is no screen for
creating departments. So an empty reference table means nothing can be created by any
route — which makes these rows part of getting the database to a working state, and
therefore a migration's job. Revision `0002` applies `seed/` in filename order.

| File | Holds | Changes when |
|---|---|---|
| `001_currency.sql` | 36 ISO 4217 currencies with their minor units | ISO does |
| `002_country.sql` | 45 ISO 3166-1 countries ACME employs in | ACME opens an office |
| `003_department.sql` | 12 departments | ACME reorganises |
| `004_band.sql` | L1–L7 with their ranks | ACME changes its levelling |
| `005_system_user.sql` | the `system:seed` actor | never |
| `006_fx_rate.sql` | one indicative rate per currency, USD = 1 | an admin posts a real one |

The first two are facts about the world. The last two are ACME's own structure and are
expected to be edited — they are seeded only because an empty table would be a dead end.

**The seeded FX rates are indicative, and dated 2020-01-01 — which is not when they were
true.** The alternative was dating them recently, which makes every salary effective
before that date fail with `MissingFxRate`, so no historical import works on a fresh
database. Dated early, every realistic salary date resolves. They are designed to be
superseded rather than corrected: the lookup takes the most recent rate on or before the
salary date, so an admin posting real dated rates overrides these from that date onward
without deleting anything. What they never are is a fallback to 1.0 — a missing rate
raises, because a salary stored at parity is wrong by a factor of eighty and still looks
like money.

`fx_rate.created_by` is NOT NULL on purpose: a rate moves every reported figure in its
currency, so it has to be attributable. The seeded rates are owned by `system:seed`
rather than a fabricated human — nobody can log in as it, and it is obviously not a
person when it shows up in a report.

Every statement is `ON CONFLICT DO NOTHING`, so the revision is safe against a database
where some rows were added by hand. The downgrade deletes only rows nothing references,
so it cannot fail halfway and leave the tables partly emptied.

## Users

The realm import seeds `hr` and `admin-user`, and runs **only when the realm does not
exist** — so editing `keycloak/realm-salary.json` does nothing to a stack that is
already up. For that, `create_user.py` talks to the admin API:

```bash
python3 create_user.py anita --password s3cret --roles hr_manager \
    --email anita@acme.test --first Anita --last Rao
python3 create_user.py --list
```

Standard library only, so it runs before any virtualenv exists. Idempotent on
username, and roles are validated against the realm before the user is written — a
misspelled role creates nothing rather than leaving an account that logs in and is
refused everywhere. A user who should survive `down -v` goes in the realm JSON too.

## Running it

```bash
docker compose up -d --wait
```

That is the whole stack, migrations included. A one-shot `migrate` service runs
`alembic upgrade head` and exits; the API waits on its exit code rather than racing
it, via `service_completed_successfully`.

Two build targets, one Dockerfile and one dependency resolution, so the schema and
the code that queries it cannot come from different versions. What they do not share
is contents: the API image has neither alembic nor `ddl/` in it, because the API
never imports one and never reads the other.

| Target | Carries | Runs as |
|---|---|---|
| `runtime` | `backend/app` | uvicorn on :8000, inside the network only |
| `migrator` | `backend/app`, `devops/alembic`, `devops/ddl` | `alembic upgrade head`, then exits |

The migrator carries `backend/app` because `alembic/env.py` reads the database URL
from the service's `Settings` — one definition of where the database is, shared by
the app and its migrations.

`--wait` matters: without it `up -d` returns when containers start, not when they
are ready, and the API's readiness probe queries `current_salary` — it fails until
the migration has run.

The API deliberately publishes no host port. It is told who the caller is by headers
the BFF sets after verifying the token, so a published port would make an `ADMIN`
role settable with one `curl`. Reach it through the BFF, or for a direct look:

```bash
docker compose exec api curl -s localhost:8000/health/ready
```

Open <http://localhost:5173>. nginx serves the built SPA there and proxies `/api`
and `/auth` to the BFF, so the whole application is same-origin and the session
cookie travels without CORS.

That image is a production build with no Node in it — fine for running the stack,
wrong for working on the UI. For hot reload, stop the container and use Vite, which
proxies to the BFF exactly as nginx does:

```bash
docker compose stop web
cd ../frontend && npm run dev
```

## Migrations against a host-run API

When the API runs on the host instead, alembic needs the backend's virtualenv — it reads the database URL from that
service's `Settings` rather than from `alembic.ini`, so the app and its migrations
cannot point at different databases.

```bash
source ../backend/.venv/bin/activate
alembic upgrade head
```

Re-running migrations against the containerised stack needs no virtualenv at all:

```bash
docker compose run --rm migrate
```

The full workflow — creating a revision, why `ddl/` is a snapshot, how to start over —
is in [`../backend/README.md`](../backend/README.md#database). It is documented there
because that is the service whose tests fail when the schema is wrong.

## Why the schema is here and not in backend/

Because the database is shared infrastructure, not one service's private store: the
BFF, the chat service and anything else added later all read it eventually, and
`docker compose up` and `alembic upgrade head` are the same job — getting a working
environment.

The cost, stated plainly: migrations no longer sit beside the code that depends on
them, so a schema change and the code using it are now a two-directory commit, and
`alembic/env.py` reaches across to `../backend` for the database URL. Backend CI
therefore triggers on `devops/**` as well — without that, a push touching only a DDL
file would skip the suite that proves the DDL works.
