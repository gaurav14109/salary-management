# Backend — Employee Salary Management

FastAPI · SQLAlchemy · Alembic · Postgres 16

## Running it

```bash
cd devops
docker compose up -d --wait      # Postgres, Keycloak, this API, the BFF
```

That runs this service in a container, migrations included. For a reloading server,
stop that one container and run it on the host:

```bash
docker compose stop api

cd ../backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload    # API on :8000
```

Compose, the Dockerfile's build context, and the migrations all centre on
[`devops/`](../devops/). Alembic runs from there and reads this service's `Settings`
for the database URL, so the app and its migrations can never point at different
databases. The image carries the migrations for the same reason: the container that
creates the schema and the container that queries it are built from one source.

`--wait` matters: without it `up -d` returns when the container starts, not when Postgres
accepts connections, and `alembic upgrade head` run immediately after fails with a
connection-refused error that looks like a migration problem and is not.

Check it: <http://localhost:8000/health> (liveness) and
<http://localhost:8000/health/ready> (liveness + schema). API docs at
<http://localhost:8000/docs>.

Keycloak persists to its own `keycloak` database on the same Postgres server, created
by `docker/postgres-init/` on first start. Separate database and separate role, so
restoring payroll data cannot roll back accounts and the API's credentials cannot read
the identity tables. See [`keycloak/README.md`](keycloak/README.md) for the realm, the
test users, and how to get a token by hand.

Configuration is read from the environment — copy `.env.example` to `.env`. There is no
JWT signing secret: authentication is delegated to Keycloak, so this service validates
tokens and never issues one.

## Tests

```bash
pytest                 # needs Postgres running
ruff check . && ruff format --check .
```

The suite builds the schema once per session and wraps every test in a transaction that
is rolled back, so tests share a schema, never share data, and pass in any order.

They run against a **real Postgres**, deliberately — the schema relies on a partial
unique index, JSONB and expression indexes, so a pass against SQLite would prove nothing
about the database that actually runs.

## Database

### Layout

All of it lives in `devops/`, and every command below is run from there with this
service's virtualenv active.

| Path | What it is |
|---|---|
| `devops/ddl/*.sql` | The schema as plain, commented SQL — **a snapshot, see below** |
| `devops/alembic/versions/` | The migration chain. `0001` executes `ddl/` in order |
| `devops/create_schema.py` | Local convenience: `--reset` drops everything, then migrates |

Alembic is the runner and the version ledger, not the author. There is no
autogeneration: the partial unique index, the check constraints and the `current_salary`
view are exactly what autogenerate expresses badly or silently drops, so every migration
is explicit SQL.

### Everyday commands

```bash
cd devops                # alembic.ini lives here

alembic upgrade head     # apply anything missing. Safe to run repeatedly
alembic current          # which revision is this database at
alembic history          # the whole chain
alembic downgrade -1     # undo the last migration
```

`upgrade head` only ever *applies*; it never creates a revision. Running it twice does
nothing the second time.

### Changing the schema

Three steps, and the middle one is the one people skip:

```bash
# 1. create the file
alembic revision -m "add approval columns"
#    -> devops/alembic/versions/0002_add_approval_columns.py

# 2. EDIT IT. The file is generated empty, because autogeneration is off.
#    def upgrade():
#        op.execute("ALTER TABLE salary ADD COLUMN approved_by INTEGER REFERENCES app_user(id)")
#    def downgrade():
#        op.execute("ALTER TABLE salary DROP COLUMN approved_by")

# 3. apply it
alembic upgrade head
```

Skip step 2 and you apply a migration that does nothing, bump the version, and leave the
chain claiming a change that never happened.

Commit the migration **with** the code that needs it. A migration without its code, or
code without its migration, breaks whoever pulls next.

Write `downgrade()` properly. CI runs `downgrade base && upgrade head` on every backend
push, because a migration that cannot be undone is one nobody will dare run on a database
that matters.

### `ddl/` is a snapshot, not the live schema

**`devops/ddl/*.sql` is executed only by migration `0001`.** Editing a file there changes what a
*fresh* database gets and nothing else — a database already past `0001` will never see
it, and the two environments diverge with nothing failing.

After `0001`, the schema is `ddl/` **plus** every later migration. Changes go in a new
revision, never into `ddl/`.

### Starting over locally

```bash
cd devops && python create_schema.py --reset    # destroys all data
```

Local and CI only. On a database with rows in it the answer is `alembic upgrade head`
and nothing else.

## Layout

```
app/
  main.py      FastAPI app, request-id middleware, health endpoints
  config.py    Settings from the environment
  db.py        engine, session, get_db — one transaction per request
tests/         mirrors app/; test_schema.py asserts the database enforces the design
```

The schema is not here — `devops/ddl/`, `devops/alembic/` and `devops/create_schema.py`
carry it. The suite still proves it: `tests/conftest.py` builds the database by running
those migrations, so a broken DDL file fails this service's tests.

`get_db` commits on success and rolls back on any exception, so the transaction boundary
lives there rather than in handlers: a handler cannot leave a half-written state because
it does not control the commit.
