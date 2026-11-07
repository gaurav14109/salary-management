import uuid

from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

app = FastAPI(
    title="Employee Salary Management",
    description="Salary data for 10,000 employees across multiple countries.",
    version="0.1.0",
)


@app.middleware("http")
async def assign_request_id(request: Request, call_next):
    """Tag every request so the access log, the error trace and the audit row share an id."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    """Liveness: is the process up and serving.

    Deliberately does not touch the database. A liveness probe that fails on a database
    blip restarts a perfectly healthy process, which turns a brief outage into a longer one.
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness: can this process actually serve a request.

    Checks for the schema, not merely a connection. An empty database accepts
    connections happily and fails every real query, so "ready" has to mean the
    migration has run.
    """
    db.execute(text("SELECT 1 FROM current_salary LIMIT 1"))
    return {"status": "ok", "database": "ok"}
