"""Structured logging, with the things that must never be logged redacted at source.

OWASP A09 is as much about logging the wrong thing as about logging too little. An
access token in a log file is a credential in a log file - readable by anyone with log
access, shipped to whatever aggregator the company uses, and retained for years.

Redaction happens in the formatter rather than at call sites, because a rule that
depends on every future developer remembering it is not a rule.
"""

import json
import logging
import re
import sys
from typing import Any

from app.config import get_settings

# Bearer tokens, cookie values, client secrets, and anything shaped like a JWT.
_PATTERNS = [
    (re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?)(bearer\s+)?[\w\-.~+/]+=*"), r"\1<redacted>"),
    (re.compile(r"(?i)(cookie\"?\s*[:=]\s*\"?)[^\"\s,}]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(client_secret\"?\s*[:=]\s*\"?)[^\"\s,}&]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(refresh_token\"?\s*[:=]\s*\"?)[^\"\s,}&]+"), r"\1<redacted>"),
    (re.compile(r"\beyJ[\w\-]+\.[\w\-]+\.[\w\-]*"), "<jwt-redacted>"),
]

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
        return redact(json.dumps(payload, default=str))


class TextFormatter(logging.Formatter):
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

    # uvicorn's access log duplicates the request log below, without the request id,
    # the session subject or the duration - so it is noise rather than signal.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
