"""The trust boundary: is this caller the BFF?

Separate from ``access`` on purpose. This file answers a question about the *network
hop* - did the request come from the one process allowed to make it - while ``access``
answers a question about the *person*. They change for different reasons: this one
changes if the deployment topology changes, that one changes if the business decides
who may do what. One file that did both would change for either.

This is the only thing in the service that resembles authentication, and it does not
authenticate a user. Identity arrives as headers the BFF set after verifying a token,
so the guarantee that nothing else can set them rests on exactly two things:

  * the api container publishes no host port - reachable only over the compose
    network, from the BFF;
  * every request carries X-Gateway-Key, checked here before routing.

Remove either and X-User-Roles becomes a header anyone can type.
"""

import logging
import secrets
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.schemas import ErrorResponse

log = logging.getLogger("salary.gateway")

GATEWAY_KEY_HEADER = "X-Gateway-Key"

# The probes. Docker's healthcheck runs inside the container, is not the BFF, and has
# no key to send. Neither path reads data or names a user.
UNGUARDED_PATHS = frozenset({"/health", "/health/ready"})


def _configured_key() -> str:
    return get_settings().gateway_key


def install_gateway_guard(
    app: FastAPI,
    *,
    key_provider: Callable[[], str] = _configured_key,
) -> None:
    """Reject anything that did not come from the BFF, before it reaches a route.

    Middleware rather than a dependency: a dependency is opt-in per route, so an
    endpoint added in a hurry is unguarded until someone notices. This sits in the
    path of every request by construction (OWASP A01).

    ``key_provider`` is injected rather than read from settings inline, so a test can
    supply one without reaching into the environment, and a deployment could later
    read the key from a secret manager without this function changing.
    """

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path in UNGUARDED_PATHS:
            return await call_next(request)

        expected = key_provider()
        if not expected:
            # Fail closed. An unset key must never read as "no check configured" -
            # that is the single misconfiguration that silently opens everything.
            return _rejected("gateway_key_not_configured", request)

        presented = request.headers.get(GATEWAY_KEY_HEADER, "")
        # Constant time. A plain == leaks the key one byte at a time to anyone
        # patient enough to measure.
        if not secrets.compare_digest(presented, expected):
            # Told apart in the log, never in the response. "Nothing sent one" is a
            # misrouted caller or a missing config; "sent the wrong one" is a stale
            # deploy or someone probing. Different problems, different fixes - and
            # the operator is not the threat.
            return _rejected(
                "gateway_key_absent" if not presented else "gateway_key_mismatch", request
            )

        return await call_next(request)


def _rejected(reason: str, request: Request) -> JSONResponse:
    """One body for every rejection at the gate.

    The reason goes to the log, never to the caller: told apart, "no key" and "wrong
    key" are an oracle for probing the boundary. Built here rather than raised as a
    SalaryError because middleware runs outside the exception handlers - by design,
    since a request that fails here must not reach anything that could interpret it.
    """
    log.warning(
        "gateway_rejected",
        extra={
            "reason": reason,
            "path": request.url.path,
            "client": request.client.host if request.client else "-",
            "request_id": getattr(request.state, "request_id", None),
        },
    )
    # Same model as every other error, even though this runs outside the exception
    # handlers: a caller cannot tell where in the stack a failure happened, and
    # should not have to parse two shapes to find out.
    body = ErrorResponse(
        detail="Not authenticated.",
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(status_code=401, content=body.model_dump(exclude_none=True))
