"""Wiring only.

Each concern lives in a module with one reason to change: who may call at all
(``gateway``), who the caller is and what their role permits (``access``), what a
failure means to a client (``errors``), what gets recorded (``observability``,
``logging_setup``). This file connects them and owns nothing itself.

Middleware order matters and is easy to get backwards, so it is not decided here:
``middleware.install_middleware`` owns it and explains why.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import register_error_handlers
from app.logging_setup import configure_logging
from app.middleware import install_middleware
from app.schemas import ErrorResponse, HealthResponse, ReadyResponse

log = logging.getLogger("salary.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log.info("startup")
    yield
    log.info("shutdown")


app = FastAPI(
    title="Employee Salary Management",
    description="Salary data for 10,000 employees across multiple countries.",
    version="0.1.0",
    lifespan=lifespan,
    # Declared once, for every route: the SPA handles this shape on any failure, so
    # it belongs in the published contract rather than in each endpoint.
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated."},
        403: {"model": ErrorResponse, "description": "Role not permitted."},
        422: {"model": ErrorResponse, "description": "Invalid request."},
        500: {"model": ErrorResponse, "description": "Unexpected failure."},
    },
)

register_error_handlers(app)
install_middleware(app)


@app.get("/health", tags=["ops"], response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness: is the process up and serving.

    Deliberately does not touch the database. A liveness probe that fails on a database
    blip restarts a perfectly healthy process, which turns a brief outage into a longer one.
    """
    return HealthResponse()


@app.get("/health/ready", tags=["ops"], response_model=ReadyResponse)
def ready(db: Session = Depends(get_db)) -> ReadyResponse:
    """Readiness: can this process actually serve a request.

    Checks for the schema, not merely a connection. An empty database accepts
    connections happily and fails every real query, so "ready" has to mean the
    migration has run.
    """
    db.execute(text("SELECT 1 FROM current_salary LIMIT 1"))
    return ReadyResponse()
