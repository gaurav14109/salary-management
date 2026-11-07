# Salary API, and the migrator that builds its schema.
#
# Two targets from one file, because they share a source tree and must share a
# dependency resolution — a migrator built from different sources than the API is
# how a schema and the code that queries it drift apart.
#
# What they do not share is contents. The API never imports alembic and never reads
# ddl/, so neither is in its image: a process that cannot reach the migration tool
# cannot be made to run one.
#
# The build context is the repository root, because the migrations live in devops/.
# Paths below are therefore repo-relative.

# ── dependencies ────────────────────────────────────────────────────────────
# One builder, two virtualenvs. /opt/venv is what the API runs on; /opt/venv-migrate
# adds alembic on top of the same resolved set.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Dependencies resolve from pyproject alone, so copy it first: application code
# changes on every commit, dependencies rarely.
COPY backend/pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir . \
 && python -m venv /opt/venv-migrate \
 && /opt/venv-migrate/bin/pip install --no-cache-dir ".[migrations]"

# ── shared runtime base ─────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --uid 10002 --create-home --shell /usr/sbin/nologin api

# Code owned by root, process running as api: it can read its own source and
# cannot rewrite it.
WORKDIR /srv
COPY --chown=root:root backend/app ./backend/app

# ── the API ─────────────────────────────────────────────────────────────────
FROM base AS runtime

ENV PATH="/opt/venv/bin:$PATH"
COPY --from=builder /opt/venv /opt/venv

USER api
WORKDIR /srv/backend
EXPOSE 8000

# Readiness, not liveness: this one queries current_salary, so it fails until the
# migration has run. An empty database accepts connections happily and fails every
# real query, which is the failure this catches.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# ── the migrator ────────────────────────────────────────────────────────────
# Runs to completion and exits. It carries backend/app from the base stage because
# alembic/env.py reads the database URL from this service's Settings — one
# definition of where the database is, shared by the app and its migrations.
FROM base AS migrator

ENV PATH="/opt/venv-migrate/bin:$PATH"
COPY --from=builder /opt/venv-migrate /opt/venv-migrate

# The two trees keep their repo-relative positions: env.py counts directories up
# from __file__ to reach ../backend, and 0001 counts up to reach ../ddl. Flattening
# this layout breaks both silently — the migration finds no SQL files and reports
# success.
COPY --chown=root:root devops/alembic     ./devops/alembic
COPY --chown=root:root devops/ddl         ./devops/ddl
COPY --chown=root:root devops/alembic.ini ./devops/alembic.ini

USER api
WORKDIR /srv/devops

CMD ["alembic", "upgrade", "head"]
