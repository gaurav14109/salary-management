"""Forwarding mechanics, with no knowledge of which upstream or why.

The proxy's job is to be boring. Everything interesting here is something it refuses
to do.
"""

import logging

import httpx
from fastapi import Request, Response

from app.config import get_settings
from app.errors import PayloadTooLarge, UpstreamTimeout, UpstreamUnavailable
from app.security.paths import safe_upstream_path

log = logging.getLogger("bff.proxy")

# Allowlist, not denylist. A denylist forwards every header someone thinks of next -
# X-Forwarded-For, X-User-Id, X-Real-IP - and an upstream that trusts any of them
# trusts the client (OWASP A03). Everything the upstream needs, it gets explicitly.
FORWARDABLE_REQUEST_HEADERS = frozenset(
    {"accept", "accept-language", "content-type", "if-none-match", "if-modified-since", "range"}
)

# Hop-by-hop headers belong to a single connection; forwarding them corrupts the next.
# Set-Cookie is dropped because an upstream has no business setting cookies on a
# browser it cannot see.
BLOCKED_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-encoding",
        "content-length",
        "set-cookie",
    }
)


async def read_body(request: Request) -> bytes:
    """Read the body, refusing one large enough to be a denial of service.

    Checking Content-Length is not enough on its own - it can lie, or be absent on a
    chunked request - so the accumulated size is checked as well.
    """
    limit = get_settings().max_body_bytes

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge()

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


async def forward(
    *,
    http: httpx.AsyncClient,
    request: Request,
    upstream_base: str,
    path: str,
    access_token: str,
    request_id: str,
) -> Response:
    safe_path = safe_upstream_path(path)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in FORWARDABLE_REQUEST_HEADERS
    }
    # Added, never forwarded. A client-supplied Authorization or X-Request-ID is
    # discarded by the allowlist above before it gets here.
    headers["Authorization"] = f"Bearer {access_token}"
    headers["X-Request-ID"] = request_id

    body = await read_body(request)

    try:
        upstream = await http.request(
            request.method,
            f"{upstream_base.rstrip('/')}/{safe_path}",
            params=request.query_params,
            content=body,
            headers=headers,
        )
    except httpx.TimeoutException as exc:
        log.warning("upstream_timeout", extra={"upstream": upstream_base, "path": safe_path})
        raise UpstreamTimeout() from exc
    except httpx.HTTPError as exc:
        # The upstream hostname and the library's message stay in the log. The client
        # gets a sentence it can act on (A05: no internal detail in responses).
        log.warning(
            "upstream_unreachable",
            extra={"upstream": upstream_base, "path": safe_path, "reason": type(exc).__name__},
        )
        raise UpstreamUnavailable() from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers={
            k: v for k, v in upstream.headers.items() if k.lower() not in BLOCKED_RESPONSE_HEADERS
        },
    )
