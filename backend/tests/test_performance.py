"""The two queries a 10,000-employee database is allowed to be slow on, measured.

The brief names 10,000 employees, which makes every "this will scale" claim either
measured or decorative. These two endpoints are the ones that cannot be made fast
later by adding a cache: the list is the first screen an HR Manager sees, and the
dashboard aggregates every open salary row in the company.

Marked `perf` and excluded from the default run. They need ~25,000 salary rows built
first, which is seconds rather than milliseconds, and a timing assertion in the
normal suite is a test that fails on a busy laptop and teaches nobody anything.

Run them deliberately:

    pytest -m perf -v

What is asserted is a budget, not a benchmark. The number is generous enough that
only a missing index or an N+1 crosses it - a 10x regression, not a 10% one - because
a tight bound on wall-clock time measured on unknown hardware is a flake generator.
"""

import time
from statistics import median

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dashboard import router as dashboard_router
from app.api.employees import router as employees_router
from app.db import get_db
from app.errors import register_error_handlers
from app.middleware import install_middleware
from scripts.seed import populate

# Every test in this file is a perf test. Marking the module rather than each
# function means a test added here cannot forget the marker and leak into the
# default suite, where it would be a flake.
pytestmark = pytest.mark.perf

GATEWAY_KEY = "test-gateway-key"

EMPLOYEES = 10_000

# Budgets, in seconds. Both are the figures the plan committed to.
LIST_BUDGET = 0.300
DASHBOARD_BUDGET = 0.500


def headers() -> dict[str, str]:
    return {
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Sub": "kc-sub-perf",
        "X-User-Name": "perf",
        "X-User-Roles": "hr_manager",
    }


@pytest.fixture
def api(db) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)
    install_middleware(app, key_provider=lambda: GATEWAY_KEY)
    app.include_router(employees_router)
    app.include_router(dashboard_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture
def populated(db, actor_id):
    """Ten thousand employees, inside the test's transaction and rolled back after.

    The same function the seed script runs, so what is measured here is what a
    reviewer gets when they populate their own database - not a second, tidier
    dataset that happens to query well.
    """
    return populate(db, actor_id=actor_id, employees=EMPLOYEES)


def timed(call, *, runs: int = 5) -> float:
    """Median of several runs, after a warm-up.

    The first call pays for connection setup, query planning and Pydantic model
    construction, none of which repeat. Median rather than mean so one scheduling
    hiccup on a laptop does not decide the result.
    """
    call()  # warm-up, discarded
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        response = call()
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
    return median(samples)


def test_list_endpoint_under_300ms_with_10000_employees(api, populated):
    """One page of 50, with the current salary joined on.

    The join is the risk: the open salary row is found through a partial index, and
    without it this becomes a sequential scan of every salary row ever written, per
    page, forever.
    """
    elapsed = timed(lambda: api.get("/employees", params={"size": 50}, headers=headers()))

    assert elapsed < LIST_BUDGET, f"list took {elapsed * 1000:.0f}ms"


def test_list_endpoint_is_not_slower_on_the_last_page(api, populated):
    """OFFSET pagination degrades with depth - the database walks the rows it skips.

    At 10,000 employees the last page must still be servable. If this ever crosses
    the budget, the fix is keyset pagination, and this test is the trigger for it.
    """
    last_page = EMPLOYEES // 50

    elapsed = timed(
        lambda: api.get("/employees", params={"size": 50, "page": last_page}, headers=headers())
    )

    assert elapsed < LIST_BUDGET, f"last page took {elapsed * 1000:.0f}ms"


def test_dashboard_endpoint_under_500ms_with_10000_employees(api, populated):
    """Six aggregates over every open salary row, including a median.

    `percentile_cont` sorts, so this is the most expensive thing the application
    asks the database to do. It reads the `current_salary` view, which is where the
    "open row, active employee" filter lives.
    """
    elapsed = timed(lambda: api.get("/dashboard", headers=headers()))

    assert elapsed < DASHBOARD_BUDGET, f"dashboard took {elapsed * 1000:.0f}ms"


def test_dashboard_with_a_country_filter_is_not_slower_than_without(api, populated):
    """A filter must narrow the work, not add to it.

    If filtering costs more than not filtering, the predicate is being applied after
    the aggregation rather than before it - the shape of mistake that only shows up
    at volume.
    """
    unfiltered = timed(lambda: api.get("/dashboard", headers=headers()))
    filtered = timed(lambda: api.get("/dashboard", params={"country": "US"}, headers=headers()))

    assert filtered < DASHBOARD_BUDGET, f"filtered dashboard took {filtered * 1000:.0f}ms"
    # Generous multiplier: the point is to catch a filter that makes things worse by
    # an order of magnitude, not to assert that filtering always wins.
    assert filtered < unfiltered * 2


def test_the_fixture_really_built_ten_thousand_employees(populated):
    """A performance test against an empty table passes instantly and proves nothing.

    This is the guard on the other five: if the seed quietly produced 100 rows, every
    budget above would be met for the wrong reason.
    """
    assert populated.employees == EMPLOYEES
    # 1-4 salary rows each, so the total must be well clear of one-each.
    assert populated.salaries > EMPLOYEES * 1.5
    # Leavers keep their history and have no open row - the same shape the
    # termination path produces, so the dashboard has something to exclude.
    assert populated.inactive > 0
    assert populated.open_salaries == populated.employees - populated.inactive
