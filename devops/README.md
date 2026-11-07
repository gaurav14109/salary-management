# devops

The local stack and the database schema.

```
docker-compose.yml   web :5173 (the only address a user types), Keycloak :8080,
                     BFF :8001 and Postgres :5432. The API publishes no port
ddl/*.sql            the schema as plain SQL — a snapshot, executed only by 0001
alembic/versions/    the migration chain
alembic.ini          alembic is run from this directory
create_schema.py     local convenience: --reset drops everything, then migrates
keycloak/            the realm: clients, roles, seed users, imported on first start
create_user.py       add a user to a realm that is already running
postgres-init/       creates Keycloak's own database, once, on an empty volume
```

Everything about identity lives here rather than in `backend/`, for the same reason
the schema does: a realm is infrastructure. The salary service holds no realm, no
client, no password and never sees a token.

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
