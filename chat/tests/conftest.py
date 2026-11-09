import logging

import pytest

from app.access import Principal


class FakeSalary:
    """Stands in for the salary API. Records calls, answers with canned data.

    A fake rather than a mocked HTTP layer: these tests are about which tool ran and
    what it asked for, and asserting on that through recorded requests would test
    httpx as much as the dispatcher.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: dict[str, dict] = {}
        self.caller: Principal | None = None

    def get(self, path: str, params: dict, *, caller: Principal) -> dict:
        self.calls.append((path, params))
        self.caller = caller
        return self.responses.get(path, {})


@pytest.fixture
def salary() -> FakeSalary:
    return FakeSalary()


@pytest.fixture(autouse=True)
def _build_log_records(caplog):
    """Make every test actually construct its log records.

    `Logger.info` checks the level before building the record, so with logging at
    its default WARNING a line like `extra={"args": ...}` is a silent no-op under
    test and a KeyError in production. That is precisely how this shipped: the
    suite was green while every tool call in the running service failed.

    Autouse, so a test added later inherits the guarantee without knowing about it.
    `caplog` captures the output, so the suite stays quiet.
    """
    caplog.set_level(logging.INFO)
