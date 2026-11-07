"""One id and one log line per request.

Split from both ``errors`` and ``gateway``: those decide what a failure means, this
only records what happened. It is also the outermost middleware, so the request id
exists before anything else can want to log it.
"""

import logging
import time
import uuid

from fastapi import FastAPI, Request

log = logging.getLogger("salary.request")

REQUEST_ID_HEADER = "X-Request-ID"


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(request: Request, call_next):
        """The id ties this service's log to the BFF's and to the audit row the
        change produced, which is the difference between "it failed" and a timeline.

        An inbound id is kept rather than replaced: the BFF already minted one, and
        a second id for the same request means the two logs cannot be joined.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()

        response = await call_next(request)

        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response
