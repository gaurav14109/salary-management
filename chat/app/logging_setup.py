"""JSON logs, with the things that must never reach a log file redacted at source."""

import logging
import re
import sys

from app.config import get_settings

_PATTERNS = [
    (re.compile(r"(?i)(x-gateway-key\W+)\S+"), r"\1<redacted>"),
    (re.compile(r"(?i)(gsk_)[A-Za-z0-9]+"), r"\1<redacted>"),
    (re.compile(r"(?i)(authorization\W+bearer\s+)\S+"), r"\1<redacted>"),
]

_RESERVED = set(logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(get_settings().log_level)
    # The model client logs request bodies at DEBUG, and those bodies are prompts.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
