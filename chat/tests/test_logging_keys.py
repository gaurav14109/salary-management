"""No log call may use a name `logging` reserves.

`Logger.makeRecord` raises KeyError rather than let `extra=` shadow a LogRecord's
own attribute, and `Logger.info` checks the level *before* building the record - so
below INFO the call is a silent no-op. A log line can therefore kill the request it
is describing, in production only.

This exact bug shipped twice: `extra={"filename": ...}` in the salary service's
upload endpoint, and `extra={"args": ...}` here, which made every tool call fail
with "the salary service did not answer" while the salary service was never
contacted at all.

The salary service grew this guard after the first one. The chat service did not,
which is why the second one shipped. Scanned statically rather than caught at
runtime, because a runtime test needs to exercise that exact line with the logger
at that exact level.
"""

import ast
import logging
import pathlib

from app.logging_setup import _RESERVED

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_log_call_passes_a_reserved_logrecord_key_in_extra():
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


def test_the_evaluation_harness_is_scanned_too():
    """evals/ runs with logging configured and is not covered by the app scan."""
    offenders = []
    for path in sorted((ROOT / "evals").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and key.value in _RESERVED:
                            offenders.append(f"{path.name}:{key.lineno}")

    assert offenders == []


def test_the_reserved_list_knows_what_logging_actually_reserves():
    """A hand-written list rots the moment CPython adds an attribute, and the guard
    above would pass while the bug it exists to catch shipped."""
    actual = set(logging.LogRecord("n", logging.INFO, "p", 1, "m", None, None).__dict__)

    assert actual - _RESERVED == set()
