"""Admin reads. Admin never writes.

Asserted across every route at once rather than endpoint by endpoint, because the
failure this guards against is not an endpoint getting it wrong today - it is the
*next* endpoint being written with the habitual
`require_role(Role.ADMIN, Role.HR_MANAGER)` and nobody noticing that a read-only
role was just handed a write.

Hiding the button in the UI is a courtesy. This is the rule.
"""

from fastapi.routing import APIRoute

from app.access import Role
from app.main import app

# Methods that change something. HEAD and OPTIONS are neither reads of data nor
# writes, and FastAPI adds them on its own.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _role_gates(route: APIRoute):
    """Every require_role dependency guarding a route, by its published roles."""
    return [
        dependency.call.allowed_roles
        for dependency in route.dependant.dependencies
        if hasattr(dependency.call, "allowed_roles")
    ]


def _routes(routes=None):
    """Every endpoint in the app, included routers walked through.

    FastAPI wraps an included router in a holder object rather than flattening its
    routes onto the app, so a single pass over app.routes finds only the handlers
    declared on the app itself - which is to say, the health checks, and nothing
    this test is about.
    """
    found = []
    for route in app.routes if routes is None else routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            # FastAPI 0.162 holds an included router here rather than flattening
            # its routes onto the app.
            found.extend(_routes(route.original_router.routes))
        elif hasattr(route, "routes"):
            found.extend(_routes(route.routes))
    return found


def test_no_write_route_admits_an_admin():
    offenders = [
        f"{sorted(route.methods & WRITE_METHODS)[0]} {route.path}"
        for route in _routes()
        if route.methods & WRITE_METHODS
        for gate in _role_gates(route)
        if Role.ADMIN in gate
    ]

    assert offenders == [], (
        f"These write routes still admit an admin, who is a read-only role: {offenders}"
    )


def test_every_write_route_is_actually_guarded():
    """A route with no role gate at all would pass the test above by saying nothing.

    An unguarded write is worse than one guarded wrongly: this is the check that
    stops the rule being satisfied by an endpoint that forgot to ask.
    """
    unguarded = [
        f"{sorted(route.methods & WRITE_METHODS)[0]} {route.path}"
        for route in _routes()
        if route.methods & WRITE_METHODS and not _role_gates(route)
    ]

    assert unguarded == [], f"These write routes have no role requirement: {unguarded}"


def test_the_reads_are_still_open_to_an_admin():
    """Read-only is read, not shut out. If this fails, admin has been locked out of
    the thing the role exists to do."""
    readable = [
        route.path
        for route in _routes()
        if "GET" in route.methods
        for gate in _role_gates(route)
        if Role.ADMIN in gate
    ]

    assert "/employees" in readable
    assert "/dashboard" in readable
