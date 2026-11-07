# Backend-for-frontend image.
#
# Two stages. The builder has a compiler and a package index; the runtime has
# neither. Anything an attacker could use to fetch or build code is left behind
# in a layer that never ships.

# ── build ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Dependencies resolve from pyproject alone, so copy it first: application code
# changes on every commit, dependencies rarely. Splitting them keeps the slow
# layer cached.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir .

# ── runtime ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE: the filesystem is read-only in deployment, and .pyc
# files written at import time would fail there rather than at build time.
# PYTHONUNBUFFERED: logs are the only view into this process; buffering hides
# them until the buffer fills, which for a crashing process is never.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# A fixed uid so a mounted volume's ownership is predictable across hosts.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin bff

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/bff
COPY --chown=root:root app ./app

# Code is owned by root and the process runs as bff: the service can read its
# own source but cannot rewrite it. A remote-code-execution bug then has nowhere
# to persist itself.
USER bff

EXPOSE 8001

# Liveness only, matching GET /health: shallow by design, so a probe cannot
# restart a healthy process because Keycloak is down. urllib rather than curl —
# no extra package, and nothing in the image that can fetch a remote payload.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"]

# No --reload, no --workers. One process per container: the rate limiter and the
# JWKS cache are per-process, so replicas are the scaling unit and the
# orchestrator owns the count.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
