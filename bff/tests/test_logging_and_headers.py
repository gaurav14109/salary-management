"""Logging hygiene (OWASP A09) and response headers (A05).

A09 is as much about logging the wrong thing as about logging too little: a token in
a log file is a credential in a log file, readable by everyone with log access and
retained for years.
"""

import json
import logging

import httpx
import respx

from app.logging_setup import JsonFormatter, redact

API = "http://localhost:8000"


def test_a_bearer_token_is_redacted():
    line = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig"

    assert "eyJhbGciOiJSUzI1NiJ9" not in redact(line)
    assert "redacted" in redact(line)


def test_a_bare_jwt_anywhere_in_a_line_is_redacted():
    """Tokens reach logs by accident - inside an exception message, a repr, a URL -
    so the pattern matches the shape, not the context."""
    line = "upstream said eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc was rejected"

    assert "eyJ" not in redact(line)


def test_cookies_refresh_tokens_and_client_secrets_are_redacted():
    assert "gAAAA" not in redact("cookie: salary_session=gAAAAABq7e...")
    assert "s3cr3t" not in redact("client_secret=s3cr3t&grant_type=x")
    assert "r3fr3sh" not in redact('{"refresh_token": "r3fr3sh"}')


def test_redaction_happens_in_the_formatter_not_at_the_call_site():
    """A rule that depends on every future developer remembering it is not a rule."""
    record = logging.LogRecord(
        "bff.test",
        logging.INFO,
        __file__,
        1,
        "forwarding with Authorization: Bearer eyJabc.eyJdef.sig",
        None,
        None,
    )

    emitted = JsonFormatter().format(record)

    assert "eyJabc" not in emitted
    assert json.loads(emitted)["level"] == "INFO"


def test_a_log_line_is_structured_json():
    record = logging.LogRecord("bff.test", logging.INFO, __file__, 1, "request", None, None)
    record.request_id = "abc-123"
    record.status = 200

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "request"
    assert payload["request_id"] == "abc-123"
    assert payload["status"] == 200
    assert payload["logger"] == "bff.test"


def test_every_response_carries_a_request_id(client):
    """One id ties this log to the API's log and to the audit row the change made."""
    response = client.get("/health")

    assert response.headers["X-Request-ID"]


def test_a_supplied_request_id_is_preserved(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-me"})

    assert response.headers["X-Request-ID"] == "trace-me"


def test_security_headers_are_present(client):
    headers = client.get("/health").headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"


@respx.mock
def test_authenticated_responses_are_not_cacheable(authenticated):
    """Salary data must not sit in a shared or browser cache."""
    respx.get(f"{API}/employees").mock(return_value=httpx.Response(200, json={"items": []}))

    response = authenticated.get("/api/employees")

    assert "no-store" in response.headers["Cache-Control"]


def test_an_unhandled_error_returns_a_generic_body_with_a_request_id(client, monkeypatch):
    """Full detail to the log, a sentence to the client, and the id that joins them."""
    from app.auth import routes

    def explode(*_args, **_kwargs):
        raise RuntimeError("internal detail that must not escape")

    monkeypatch.setattr(routes, "safe_return_path", explode)

    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 500
    assert response.json()["detail"] == "Something went wrong"
    assert response.json()["request_id"]
    assert "internal detail" not in response.text
    assert "Traceback" not in response.text
