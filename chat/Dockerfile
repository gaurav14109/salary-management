# The assistant service.
#
# Build context is the repository root, like the API's, so paths are repo-relative.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /build

COPY chat/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app && touch app/__init__.py \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --uid 10003 --create-home --shell /usr/sbin/nologin chat

WORKDIR /srv/chat
COPY --from=builder /opt/venv /opt/venv
# Owned by root, run as chat: the process can read its own source and cannot
# rewrite it.
COPY --chown=root:root chat/app ./app
COPY --chown=root:root chat/evals ./evals

USER chat
EXPOSE 8002

# Liveness only. The assistant is up even when Groq is not - a question will say so
# rather than the container being restarted for an upstream's outage.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
