import pathlib
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Migrations live in devops/, which owns the schema for the whole stack.
DEVOPS = ROOT.parent / "devops"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
def engine():
    """A real Postgres, built from ddl/ once per session.

    Deliberately not SQLite: the schema relies on a partial unique index, JSONB and
    expression indexes, so a test against another engine would prove nothing about
    the database that actually runs.
    """
    url = get_settings().database_url
    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment failure, not a test failure
        pytest.exit(
            f"Could not reach Postgres at {url}.\n"
            f"Start it with:  cd devops && docker compose up -d --wait db\n\n{exc}",
            returncode=1,
        )

    subprocess.run(
        [sys.executable, str(DEVOPS / "create_schema.py"), "--reset"],
        check=True,
        capture_output=True,
        cwd=DEVOPS,
    )
    return eng


@pytest.fixture
def db(engine) -> Session:
    """One transaction per test, always rolled back.

    Tests share a schema but never share data: each sees an empty database and
    leaves nothing behind, so they pass in any order.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def actor_id(db) -> int:
    """A user row, since almost every table records who wrote it."""
    return db.execute(
        text(
            "INSERT INTO app_user (external_id, email, display_name) "
            "VALUES ('kc-test-sub', 'hr@acme.test', 'Test HR Manager') RETURNING id"
        )
    ).scalar_one()


@pytest.fixture
def reference_data(db) -> dict[str, object]:
    """The reference rows an employee cannot exist without."""
    db.execute(text("INSERT INTO country (code, name) VALUES ('IN', 'India'), ('DE', 'Germany')"))
    db.execute(
        text(
            "INSERT INTO currency (code, name, minor_units) "
            "VALUES ('USD', 'US Dollar', 2), ('INR', 'Indian Rupee', 2), ('EUR', 'Euro', 2)"
        )
    )
    dept_id = db.execute(
        text("INSERT INTO department (name) VALUES ('Engineering') RETURNING id")
    ).scalar_one()
    band_id = db.execute(
        text("INSERT INTO band (name, rank) VALUES ('L3', 3) RETURNING id")
    ).scalar_one()
    return {"department_id": dept_id, "band_id": band_id}


@pytest.fixture
def employee_id(db, reference_data) -> int:
    return db.execute(
        text(
            "INSERT INTO employee (employee_code, first_name, last_name, country_code, "
            "department_id, job_title, band_id, hire_date) "
            "VALUES ('EMP-1042', 'Priya', 'Sharma', 'IN', :dept, 'Engineer', :band, '2024-04-01') "
            "RETURNING id"
        ),
        {"dept": reference_data["department_id"], "band": reference_data["band_id"]},
    ).scalar_one()
