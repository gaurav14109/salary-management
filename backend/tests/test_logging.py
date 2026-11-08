"""Logging, and the things that must never reach a log file.

OWASP A09 is as much about logging the wrong thing as about logging too little. The
gateway key in a log line is a credential in a log line - readable by anyone with log
access, shipped to whatever aggregator the company uses, retained for years.
"""

import ast
import json
import logging
import pathlib

from app.logging_setup import _RESERVED, JsonFormatter, redact

ROOT = pathlib.Path(__file__).resolve().parent.parent


def record(message: str, **extra) -> logging.LogRecord:
    rec = logging.LogRecord("salary.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_the_gateway_key_is_never_written_to_a_log():
    """It is the only credential this service holds, and it is sent on every call."""
    line = redact('{"x-gateway-key": "local-dev-gateway-key"}')
    assert "local-dev-gateway-key" not in line
    assert "<redacted>" in line


def test_an_authorization_header_is_redacted_even_though_none_should_arrive():
    """Nothing should send one here any more. Defence in depth: if something starts
    to, the log must not be where the token ends up."""
    assert "secret-token" not in redact("authorization: Bearer secret-token")


def test_anything_shaped_like_a_jwt_is_redacted():
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJrYy0xIn0.c2ln"
    assert jwt not in redact(f"token={jwt}")
    assert "<jwt-redacted>" in redact(f"token={jwt}")


def test_a_database_url_password_is_redacted():
    """It appears in connection errors, which are exactly the ones that get logged."""
    line = redact("could not connect to postgresql+psycopg://salary:hunter2@db:5432/salary")
    assert "hunter2" not in line


def test_structured_fields_survive_as_json():
    payload = json.loads(JsonFormatter().format(record("forbidden", sub="kc-1", status=403)))
    assert payload["event"] == "forbidden"
    assert payload["sub"] == "kc-1"
    assert payload["status"] == 403
    assert payload["level"] == "INFO"


def test_redaction_applies_to_structured_fields_too():
    """Redacting only the message would miss extra={'key': ...}, which is where
    structured logging puts everything interesting."""
    payload = JsonFormatter().format(record("gateway_rejected", presented="Bearer abcdef123456"))
    assert "abcdef123456" not in payload


def test_an_exception_is_formatted_into_the_record_not_lost():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = record("unhandled")
        rec.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(rec))

    assert "ValueError" in payload["exception"]


# --- the whole class of bug, not just the one that escaped --------------------


def test_no_log_call_passes_a_reserved_logrecord_key_in_extra():
    """`extra={"filename": ...}` is a 500, and only in production.

    `Logger.makeRecord` raises KeyError rather than let `extra=` shadow one of a
    LogRecord's own attributes. `Logger.info` checks the level *before* building the
    record, so below INFO the call is a silent no-op - which is how an upload
    endpoint shipped returning 500 on every successful import while its tests stayed
    green.

    Scanned statically rather than caught at runtime, because catching it at runtime
    requires a test to both exercise that exact line and have the logger enabled for
    that exact level. This asserts the property everywhere at once, including on
    code paths no test reaches: an error branch that logs is exactly where this bug
    hides longest.
    """
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and key.value in _RESERVED:
                        offenders.append(f"{path.name}:{key.lineno} extra={{{key.value!r}: ...}}")

    assert offenders == [], (
        "These keys collide with LogRecord's own attributes and raise at INFO: "
        + ", ".join(offenders)
    )


def test_the_guard_above_knows_the_names_logging_actually_reserves():
    """A hand-written list of reserved names rots the moment CPython adds one.

    Checked against a real LogRecord rather than trusted: if `_RESERVED` drifted out
    of date, the test above would pass while the bug it exists to catch shipped.
    """
    actual = set(logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None).__dict__)

    missing = actual - _RESERVED - {"taskName"}  # taskName is 3.12+ and optional
    assert missing == set(), f"logging reserves names the guard does not know about: {missing}"
