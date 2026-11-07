"""Middleware wiring, in the one order that works.

Starlette runs middleware in reverse registration order: the last registered is the
outermost and runs first. That is easy to get backwards, and getting it backwards
here is quiet rather than loud - the gateway guard would reject a request before
anything had assigned it an id, so the 401 and its log line would carry no
request_id, and exactly the failures most worth tracing would be the ones that
cannot be traced.

One function, so the order is stated once and cannot be reassembled wrongly at the
next call site.
"""

from collections.abc import Callable

from fastapi import FastAPI

from app.gateway import _configured_key, install_gateway_guard
from app.observability import install_request_logging


def install_middleware(
    app: FastAPI,
    *,
    key_provider: Callable[[], str] = _configured_key,
) -> None:
    """Register every middleware, outermost last.

    Resulting order on an inbound request:
        request logging  ->  gateway guard  ->  route
    so an id exists before anything can want to log one, and a rejected request is
    logged with the same id the caller is given.
    """
    install_gateway_guard(app, key_provider=key_provider)
    install_request_logging(app)
