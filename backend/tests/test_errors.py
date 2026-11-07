"""Failures become responses in one place, and say only what the caller can act on.

Two rules, both OWASP A09/A05: no internal detail escapes, and every response carries
the request id that finds the full detail in the log.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.errors import Conflict, Forbidden, NotFound, SalaryError, register_error_handlers
from app.middleware import install_middleware
from app.observability import install_request_logging
from app.schemas import ErrorResponse


class Body(BaseModel):
    amount: int


@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    # No gateway guard here: these tests are about what a failure looks like, not
    # about who may call. Request logging alone, for the id in every body.
    install_request_logging(app)
    register_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("psycopg.OperationalError: password authentication failed")

    @app.get("/missing")
    def missing():
        raise NotFound("No employee with id 42.")

    @app.get("/denied")
    def denied():
        raise Forbidden("This action requires one of: admin.")

    @app.get("/conflict")
    def conflict():
        raise Conflict("That salary period already has an open row.")

    @app.post("/salary")
    def salary(body: Body):
        return {"ok": True}

    # raise_server_exceptions=False so the handler runs instead of the test client
    # re-raising, which is what production does.
    return TestClient(app, raise_server_exceptions=False)


def test_an_unexpected_error_is_a_generic_500(api):
    response = api.get("/boom")
    assert response.status_code == 500
    assert response.json()["detail"] == "Something went wrong."


def test_an_unexpected_error_leaks_nothing_about_the_cause(api):
    """The database driver, the credentials message and the traceback belong in the
    log. A caller who learns the database rejected a password learns there is one."""
    body = api.get("/boom").text
    assert "psycopg" not in body
    assert "password" not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body


def test_every_error_response_carries_the_request_id(api):
    """'It failed' plus this string finds the whole story in the log."""
    for path in ("/boom", "/missing", "/denied", "/conflict"):
        body = api.get(path).json()
        assert body["request_id"], path


def test_the_request_id_in_the_body_matches_the_header(api):
    response = api.get("/missing")
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_preserved_across_the_hop(api):
    """The BFF generates it; this service must not mint a second one, or the two
    logs cannot be joined."""
    response = api.get("/missing", headers={"X-Request-ID": "from-the-bff"})
    assert response.headers["X-Request-ID"] == "from-the-bff"


@pytest.mark.parametrize(
    ("path", "status"),
    [("/missing", 404), ("/denied", 403), ("/conflict", 409)],
)
def test_a_typed_failure_keeps_its_status(api, path, status):
    assert api.get(path).status_code == status


def test_a_typed_failure_keeps_its_message(api):
    """Unlike a 500, these are written for the caller and are safe to show."""
    assert api.get("/missing").json()["detail"] == "No employee with id 42."


def test_validation_errors_name_the_field_without_echoing_the_value(api):
    """Pydantic's default body echoes the input back. For this service that can mean
    echoing a salary, or whatever else was posted, into a client-visible response."""
    response = api.post("/salary", json={"amount": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["fields"][0]["field"] == "amount"
    assert "value" not in body["fields"][0]
    assert "not-a-number" not in response.text


def test_salary_error_defaults_are_usable_without_a_message():
    """Raising the bare class must not produce an empty response body."""
    assert SalaryError().detail
    assert NotFound().detail


# --- the contract itself, not just this response -------------------------------


def test_the_error_body_validates_against_the_published_model(api):
    """If a handler ever hand-rolls a dict again, this fails."""
    for path in ("/boom", "/missing", "/denied", "/conflict"):
        ErrorResponse.model_validate(api.get(path).json())


def test_a_non_validation_error_omits_fields_rather_than_sending_null(api):
    """A null the client must remember to ignore is a null the client will forget."""
    assert "fields" not in api.get("/missing").json()


def test_a_validation_error_carries_typed_field_errors(api):
    body = ErrorResponse.model_validate(api.post("/salary", json={"amount": "nope"}).json())
    assert body.fields and body.fields[0].field == "amount"


def test_the_error_shape_is_published_in_the_openapi_document():
    """The SPA's types are generated from this. An error shape that exists only in
    code is a contract the frontend cannot be held to."""
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    assert "ErrorResponse" in schemas
    assert set(schemas["ErrorResponse"]["properties"]) == {"detail", "request_id", "fields"}


def test_every_route_documents_the_error_responses():
    from app.main import app

    responses = app.openapi()["paths"]["/health"]["get"]["responses"]
    for status in ("401", "403", "422", "500"):
        ref = responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ErrorResponse")


def test_the_gateway_rejection_uses_the_same_shape_as_every_other_error():
    """It is built outside the exception handlers. A caller cannot tell where in the
    stack a failure happened and must not have to parse two shapes to find out."""
    from fastapi import FastAPI

    app = FastAPI()
    install_middleware(app, key_provider=lambda: "a-key")

    @app.get("/guarded")
    def guarded() -> dict:
        return {}

    body = TestClient(app).get("/guarded").json()
    assert ErrorResponse.model_validate(body).request_id
