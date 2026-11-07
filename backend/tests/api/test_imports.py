"""The upload endpoints.

Synchronous: the response is the finished result, not a job id to poll. A failed
import still returns 201, because the ImportJob was created - its `status` and
`errors` are what say the file was wrong. A 4xx would mean "we could not record your
attempt", which is a different and worse thing.
"""

import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.imports import router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware
from app.models import Employee, FxRate

GATEWAY_KEY = "test-gateway-key"
HEADER = (
    "employee_code,first_name,last_name,email,country_code,department,job_title,"
    "band,hire_date,amount,currency,effective_from"
)
ROW = (
    "EMP-8001,Anita,Rao,anita@acme.test,IN,Engineering,Engineer,L3,"
    "2024-04-01,1200000,INR,2026-04-01"
)


@pytest.fixture
def api(db, reference_data, actor_id) -> TestClient:
    db.add(
        FxRate(
            currency="INR",
            units_per_usd=Decimal("83.45000000"),
            as_of=date(2026, 1, 1),
            created_by=actor_id,
        )
    )
    db.flush()

    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def headers(roles: str = "hr_manager") -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-1",
        "X-User-Name": "priya",
        "X-User-Roles": roles,
    }


def upload(api, *rows, filename="people.csv", roles="hr_manager"):
    body = "\n".join([HEADER, *rows]).encode()
    return api.post(
        "/imports",
        files={"file": (filename, io.BytesIO(body), "text/csv")},
        headers=headers(roles),
    )


# --- authorization --------------------------------------------------------------


def test_an_hr_manager_can_import(api):
    """Bulk load is the HR manager's core job. Requiring an admin would make the
    main feature of the product unusable by its main user."""
    assert upload(api, ROW).status_code == 201


def test_an_unauthenticated_upload_is_refused(api):
    body = "\n".join([HEADER, ROW]).encode()
    response = api.post(
        "/imports",
        files={"file": ("people.csv", io.BytesIO(body), "text/csv")},
        headers={"X-Gateway-Key": GATEWAY_KEY},
    )

    assert response.status_code == 401


# --- the happy path --------------------------------------------------------------


def test_a_successful_import_reports_what_it_did(api):
    body = upload(api, ROW).json()

    assert body["status"] == "SUCCESS"
    assert body["employees_created"] == 1
    assert body["salaries_created"] == 1
    assert body["errors"] == []


def test_the_counts_distinguish_a_raise_from_an_unchanged_row(api):
    """ "1,000 rows succeeded" hides what people actually ask after an import: how
    many got a raise, and how many were already correct."""
    upload(api, ROW)

    body = upload(api, ROW).json()

    assert body["salaries_unchanged"] == 1
    assert body["salaries_created"] == 0


# --- failure is a 201 with a report ----------------------------------------------


def test_a_bad_row_returns_201_with_the_error_not_a_4xx(api):
    response = upload(api, ROW.replace(",INR,", ",XYZ,"))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["errors"][0]["row_number"] == 2
    assert body["errors"][0]["field"] == "currency"


def test_nothing_is_committed_when_a_row_fails(api, db):
    upload(api, ROW, ROW.replace("EMP-8001", "EMP-8002").replace(",INR,", ",XYZ,"))

    assert db.query(Employee).filter(Employee.employee_code.like("EMP-80%")).count() == 0


def test_the_error_report_is_addressed_the_way_excel_shows_it(api):
    """Row 2 is the first data line, because row 1 is the header. Off by one here
    sends someone to the wrong line of a ten-thousand-row file."""
    body = upload(api, ROW, ROW.replace("EMP-8001", "EMP-8002").replace(",1200000,", ",-5,")).json()

    assert {e["row_number"] for e in body["errors"]} == {3}


# --- what it refuses to read ------------------------------------------------------


def test_an_unsupported_file_type_is_422(api):
    assert upload(api, ROW, filename="people.pdf").status_code == 422


def test_an_oversized_upload_is_413(api):
    """Checked on bytes before parsing: discovering a file is too big by reading it
    is the mistake repeated."""
    huge = io.BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    response = api.post(
        "/imports", files={"file": ("big.csv", huge, "text/csv")}, headers=headers()
    )

    assert response.status_code == 413


def test_a_missing_column_is_one_error_about_the_file(api):
    """Not one per row: a missing `amount` column would otherwise produce ten
    thousand identical messages."""
    body = api.post(
        "/imports",
        files={
            "file": (
                "people.csv",
                io.BytesIO(
                    f"{HEADER.replace(',amount', '')}\n{ROW.replace(',1200000', '')}".encode()
                ),
                "text/csv",
            )
        },
        headers=headers(),
    ).json()

    assert len(body["errors"]) == 1
    assert body["errors"][0]["row_number"] == 0


# --- reading the history ----------------------------------------------------------


def test_a_job_can_be_read_back_after_the_upload(api):
    job_id = upload(api, ROW.replace(",INR,", ",XYZ,")).json()["id"]

    body = api.get(f"/imports/{job_id}", headers=headers()).json()

    assert body["status"] == "FAILED"
    assert body["errors"][0]["field"] == "currency"


def test_an_unknown_job_is_404(api):
    assert api.get("/imports/999999", headers=headers()).status_code == 404


def test_the_history_is_a_page_newest_first(api):
    upload(api, ROW)
    upload(api, ROW.replace("EMP-8001", "EMP-8002").replace("anita@", "b@"))

    body = api.get("/imports", headers=headers()).json()

    assert set(body) == {"items", "total", "page", "size"}
    assert body["total"] == 2
    assert body["items"][0]["id"] > body["items"][1]["id"]
