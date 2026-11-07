"""Structured logging, with the things that must never be logged redacted at source.

OWASP A09 cuts both ways: too little logging leaves an incident unreconstructable,
and the wrong logging turns the log itself into the breach. A credential in a log
line is readable by anyone with log access, shipped to whatever aggregator the
company uses, and retained for years after the incident that would have justified it.

Redaction lives in the formatter, not at the call sites. A rule that depends on every
future developer remembering it at every `log.info` is not a rule, it is a hope.
"""

import json
import logging
import re
import sys
from typing import Any

from app.config import get_settings

# What this service can plausibly be holding when something goes wrong:
#   - the gateway key, which it is sent on literally every request;
#   - a database URL with a password in it, which appears in connection errors -
#     exactly the errors that get logged;
#   - a bearer token or JWT, which should no longer reach this service at all. Kept
#     because defence in depth costs one regex, and the day something starts
#     forwarding one again is not the day to discover the log was recording it.
_PATTERNS = [
    (re.compile(r"(?i)(x-gateway-key\"?\s*[:=]\s*\"?)[^\"\s,}]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(gateway_key\"?\s*[:=]\s*\"?)[^\"\s,}]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(bearer\s+)?[\w\-.~+/]+=*"), r"\1<redacted>"),
    (re.compile(r"(?i)(bearer\s+)[\w\-.~+/]+=*"), r"\1<redacted>"),
    (re.compile(r"(?i)(password\"?\s*[:=]\s*\"?)[^\"\s,}&]+"), r"\1<redacted>"),
    # postgresql://user:password@host - the password, and nothing either side of it.
    (re.compile(r"(://[^:/\s]+:)[^@/\s]+(@)"), r"\1<redacted>\2"),
    (re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]*"), "<jwt-redacted>"),
]

# LogRecord's own attributes. Everything else on a record was put there by a caller
# via extra= and is the structured payload we actually want.
_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
}


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Structured rather than prose because the questions asked of these logs are
    structured: every 403 for one subject, every request over a second, everything
    carrying one request id across two services.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Redacting the serialized line, not the message alone: extra= is where
        # structured logging puts everything interesting, and therefore everything
        # dangerous.
        return redact(json.dumps(payload, default=str))


class TextFormatter(logging.Formatter):
    """Readable lines for a terminal. Same redaction - a developer's log is still a log."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if settings.log_json
        else TextFormatter("%(asctime)s %(levelname)-7s %(name)s %(message)s")
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # uvicorn's access log repeats the request line below without the request id,
    # the subject or the duration - the same event, minus everything that makes it
    # useful, at twice the volume.
    logging.getLogger("uvicorn.access").disabled = True
    # SQLAlchemy at INFO echoes every statement, parameters included. That is salary
    # data in a log file.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
