"""Wiring only.

Everything this service does lives in a module with one responsibility: authenticating
(``auth``), forwarding (``proxy``), deciding what is safe (``security``), talking to
the identity provider (``clients``), and turning failures into responses (``errors``).
This file connects them and owns nothing.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request

from app.auth.routes import router as auth_router
from app.clients.keycloak import KeycloakClient
from app.config import get_settings
from app.errors import register_error_handlers
from app.logging_setup import configure_logging
from app.proxy.routes import router as proxy_router
from app.security.headers import apply_security_headers
from app.security.token import TokenVerifier

log = logging.getLogger("bff.request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One HTTP client for the life of the process.

    A client per request means a TCP and TLS handshake on every call and a file
    descriptor churned each time: latency under load, exhaustion under more of it.
    """
    settings = get_settings()
    configure_logging()

    timeout = httpx.Timeout(
        connect=settings.upstream_connect_timeout_seconds,
        read=settings.upstream_read_timeout_seconds,
        write=settings.upstream_read_timeout_seconds,
        pool=settings.upstream_connect_timeout_seconds,
    )
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        # A proxy must not silently follow an upstream redirect: the client decides
        # what to do with a 302, and following it can leak the Authorization header
        # to wherever Location points.
        follow_redirects=False,
    ) as client:
        app.state.http = client
        app.state.keycloak = KeycloakClient(client)
        # The authentication boundary. Every token leaving this service has been
        # verified against the provider's published keys by this object.
        app.state.verifier = TokenVerifier(client)
        log.info("startup", extra={"issuer": settings.oidc_issuer})
        yield
        log.info("shutdown")


settings = get_settings()

app = FastAPI(
    title="Salary Management BFF",
    version="0.1.0",
    lifespan=lifespan,
    # A proxy's route list is a map of the services behind it.
    docs_url="/docs" if settings.expose_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.expose_docs else None,
)

app.middleware("http")(apply_security_headers)
register_error_handlers(app)


@app.middleware("http")
async def observe_request(request: Request, call_next):
    """One id and one log line per request.

    The id ties this service's log to the salary API's log and to the audit row the
    change produced, which is the difference between "it failed" and a timeline.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    log.info(
        "request",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return response


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness. Deliberately unauthenticated and deliberately shallow: a probe that
    fails because an upstream is down restarts a process that is working fine."""
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(proxy_router)
