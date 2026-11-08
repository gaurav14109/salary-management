"""Preview before import: see the file, fix it, then commit it.

The import is all-or-nothing (ADR-0004), which is right and which makes the first
attempt on a real file usually a failure - one bad currency code on line 400 and
nothing lands. Today the only way to find that out is to upload, read the error
report, go back to Excel, fix, and upload again.

Preview makes that visible before anything is written: the rows arrive with their
errors attached, so the decision to import is made with the file on screen rather
than after the fact. Correcting the data stays in the spreadsheet that is the system
of record - a cell edited here would leave the file HR keeps and the rows ACME
imported quietly diverged.

The property every test here protects is that **preview writes nothing**. It parses
and validates against the real database - unknown department, missing FX rate, a
duplicate of a code already taken - and that is exactly the set of checks a browser
cannot make for itself. A preview that reported less than the import would be worse
than none, because it would promise a clean import and then fail.
"""

import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.imports import router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware
from app.models import FxRate

GATEWAY_KEY = "test-gateway-key"
HEADER = (
    "employee_code,first_name,last_name,email,country_code,department,job_title,"
    "band,hire_date,amount,currency,effective_from"
)
ROW = (
    "EMP-7001,Anita,Rao,anita@acme.test,IN,Engineering,Engineer,L3,"
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


def preview_file(api, *rows, filename="people.csv", roles="hr_manager"):
    body = "\n".join([HEADER, *rows]).encode()
    return api.post(
        "/imports/preview",
        files={"file": (filename, io.BytesIO(body), "text/csv")},
        headers=headers(roles),
    )


def employee_count(db) -> int:
    return db.execute(text("SELECT count(*) FROM employee")).scalar_one()


# --- the guarantee ---------------------------------------------------------------


def test_previewing_a_valid_file_writes_nothing(api, db):
    """The whole feature rests on this. A preview that imported would make the
    confirm button a lie."""
    before = employee_count(db)

    assert preview_file(api, ROW).status_code == 200

    assert employee_count(db) == before
    assert db.execute(text("SELECT count(*) FROM import_job")).scalar_one() == 0


def test_preview_leaves_no_salary_rows_either(api, db):
    preview_file(api, ROW)

    assert db.execute(text("SELECT count(*) FROM salary")).scalar_one() == 0


# --- what comes back -------------------------------------------------------------


def test_preview_returns_the_rows_as_typed(api):
    """Strings, not parsed values. The grid is editing a spreadsheet, and a date the
    user typed wrong has to come back exactly as they typed it or they cannot see
    what is wrong with it."""
    body = preview_file(api, ROW).json()

    assert body["rows"][0]["values"]["employee_code"] == "EMP-7001"
    assert body["rows"][0]["values"]["hire_date"] == "2024-04-01"


def test_preview_numbers_rows_the_way_the_spreadsheet_does(api):
    """Row 2 is the first row of data, because row 1 is the header. An off-by-one
    here sends the user to the wrong line."""
    body = preview_file(api, ROW).json()

    assert body["rows"][0]["row_number"] == 2


def test_preview_reports_the_columns_it_found(api):
    """The grid builds its headers from this, so an extra column in the file shows
    up rather than being silently dropped."""
    body = preview_file(api, ROW).json()

    assert body["columns"][:3] == ["employee_code", "first_name", "last_name"]


def test_preview_attaches_errors_to_the_row_they_belong_to(api):
    body = preview_file(api, ROW.replace(",INR,", ",XYZ,")).json()

    assert body["error_count"] == 1
    error = body["errors"][0]
    assert error["row_number"] == 2
    assert error["field"] == "currency"
    assert "XYZ" in error["message"]


def test_preview_finds_every_error_not_only_the_first(api):
    """Same promise the import makes. Fixing errors one upload at a time is the
    thing this screen exists to stop."""
    bad_currency = ROW.replace(",INR,", ",XYZ,")
    bad_country = ROW.replace("EMP-7001", "EMP-7002").replace(",IN,", ",ZZ,")

    body = preview_file(api, bad_currency, bad_country).json()

    assert body["error_count"] == 2
    assert {e["field"] for e in body["errors"]} == {"currency", "country_code"}


def test_preview_says_the_file_is_clean_when_it_is(api):
    body = preview_file(api, ROW).json()

    assert body["error_count"] == 0
    assert body["row_count"] == 1


# --- the checks only the server can make -----------------------------------------


def test_preview_catches_an_unknown_department(api):
    """A browser cannot know ACME's departments. This is why preview is a round trip
    rather than a parse in the page."""
    body = preview_file(api, ROW.replace(",Engineering,", ",Teleportation,")).json()

    assert body["errors"][0]["field"] == "department"


def test_preview_catches_a_salary_dated_before_any_fx_rate(api):
    """The rates are seeded at 2020-01-01, so a salary effective before that has
    none and cannot be converted.

    This is the check the preview most needed and the one `validate` did not have:
    the import caught it while applying a row, so a file like this validated clean
    and failed on upload. A preview that reported less than the import would promise
    a clean run and then fail - which is worse than no preview at all.
    """
    early = ROW.replace("2024-04-01", "2019-04-01").replace(",2026-04-01", ",2019-06-01")

    body = preview_file(api, early).json()

    assert body["error_count"] == 1
    assert body["errors"][0]["field"] == "currency"
    assert body["errors"][0]["message"] == "No FX rate for INR as of 2019-06-01."


def test_preview_agrees_with_what_the_import_would_do(api, db):
    """The two must not drift. Preview calls the same `validate` the import calls,
    and this is the test that notices if someone gives one of them a check the other
    does not have."""
    from app.services import importer as importer_service

    early = ROW.replace("2024-04-01", "2019-04-01").replace(",2026-04-01", ",2019-06-01")
    previewed = preview_file(api, early).json()

    job = importer_service.run_import(
        db,
        io.BytesIO("\n".join([HEADER, early]).encode()),
        "x.csv",
        actor_id=db.execute(text("SELECT id FROM app_user LIMIT 1")).scalar_one(),
    )

    assert job.status == "FAILED"
    assert [(e["row_number"], e["field"], e["message"]) for e in job.errors] == [
        (e["row_number"], e["field"], e["message"]) for e in previewed["errors"]
    ]


def test_preview_catches_a_duplicate_within_the_file(api):
    body = preview_file(api, ROW, ROW).json()

    assert any("appears twice" in e["message"] for e in body["errors"])


# --- limits and access -----------------------------------------------------------


def test_preview_caps_the_rows_it_returns(api, monkeypatch):
    """Past the cap the rows are truncated and the response says so, rather than the
    page quietly showing part of a file as if it were all of it.

    The real cap is the import's own 20,000, so any importable file can be seen in
    full. Lowered here rather than generating 20,001 rows to prove a branch.
    """
    monkeypatch.setattr("app.api.imports.PREVIEW_MAX_ROWS", 3)
    rows = [
        ROW.replace("EMP-7001", f"EMP-{7000 + i:05d}").replace("anita@", f"a{i}@") for i in range(5)
    ]

    body = preview_file(api, *rows).json()

    assert body["row_count"] == 5
    assert len(body["rows"]) == 3
    assert body["truncated"] is True


def test_the_preview_cap_matches_what_the_import_accepts(api):
    """A file the import would take must be previewable in full, or the screen that
    exists to prevent surprises becomes one."""
    from app.api.imports import PREVIEW_MAX_ROWS
    from app.services.importer import MAX_ROWS

    assert PREVIEW_MAX_ROWS == MAX_ROWS


def test_a_small_file_is_not_marked_truncated(api):
    assert preview_file(api, ROW).json()["truncated"] is False


def test_preview_refuses_an_unauthenticated_caller(api):
    response = api.post(
        "/imports/preview",
        files={"file": ("x.csv", io.BytesIO(b"a"), "text/csv")},
    )

    assert response.status_code == 401


def test_preview_refuses_a_caller_without_a_role(api):
    response = api.post(
        "/imports/preview",
        files={"file": ("x.csv", io.BytesIO(b"a"), "text/csv")},
        headers=headers(roles=""),
    )

    assert response.status_code == 403
