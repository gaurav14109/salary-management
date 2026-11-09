"""Forwarding mechanics, with no knowledge of which upstream or why.

The proxy's job is to be boring. Everything interesting here is something it refuses
to do.
"""

import logging

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.errors import PayloadTooLarge, UpstreamTimeout, UpstreamUnavailable
from app.security.paths import safe_upstream_path
from app.security.session import Session

log = logging.getLogger("bff.proxy")

# Allowlist, not denylist. A denylist forwards every header someone thinks of next -
# X-Forwarded-For, X-User-Id, X-Real-IP - and an upstream that trusts any of them
# trusts the client (OWASP A03). Everything the upstream needs, it gets explicitly.
#
# This matters more than it used to: the upstream decides what a caller may do from
# X-User-Roles. If a browser could set that header, it would be setting its own
# permissions. The allowlist is what makes that impossible - the identity headers
# below are built here, from a verified token, and any inbound copy is dropped
# before this dictionary is built.
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
    session: Session,
    roles: list[str],
    request_id: str,
) -> Response:
    """Forward the request with the caller's identity, and without the caller's token.

    `roles` comes from the token verified moments ago rather than from `session`,
    so a role revoked in Keycloak stops working on the next request instead of at
    the next login.
    """
    safe_path = safe_upstream_path(path)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in FORWARDABLE_REQUEST_HEADERS
    }
    # Added, never forwarded. Anything the client sent under these names was
    # discarded by the allowlist above before this point.
    #
    # No Authorization header. The upstream has no business verifying a token - this
    # process already did, once, for every service behind it. Sending one anyway
    # would leave a credential sitting in a service that is not responsible for
    # checking it, which is an invitation for someone later to trust it.
    settings = get_settings()
    headers["X-Gateway-Key"] = settings.gateway_key
    headers["X-User-Sub"] = session.sub
    headers["X-User-Name"] = session.username
    headers["X-User-Roles"] = ",".join(roles)
    headers["X-Request-ID"] = request_id
    if session.email:
        headers["X-User-Email"] = session.email

    body = await read_body(request)

    if _wants_stream(request, safe_path):
        return await _forward_streaming(
            http=http,
            request=request,
            url=f"{upstream_base.rstrip('/')}/{safe_path}",
            body=body,
            headers=headers,
            upstream_base=upstream_base,
            safe_path=safe_path,
        )

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


# Paths whose responses must reach the browser as they are produced rather than
# after they are finished. Matched on the request rather than on the upstream's
# content-type, because the decision has to be made before the request is sent.
#
# Two kinds, for two different reasons. The assistant streams prose, and buffering
# it would undo the streaming entirely - the user would wait the whole generation
# and then see the answer at once. The export streams rows from a server-side
# cursor, and buffering it here would hold the whole file in this process, which is
# the memory the cursor exists to avoid.
_STREAMING_SUFFIXES = ("ask/stream", "employees/export")


def _wants_stream(request: Request, safe_path: str) -> bool:
    return safe_path.endswith(_STREAMING_SUFFIXES)


async def _forward_streaming(
    *,
    http: httpx.AsyncClient,
    request: Request,
    url: str,
    body: bytes,
    headers: dict[str, str],
    upstream_base: str,
    safe_path: str,
) -> StreamingResponse:
    """Relay a response without ever holding all of it.

    The upstream request stays open for the life of the response, so the connection
    is closed by the generator rather than by this function - which is why the
    request is built and sent by hand instead of with `http.request`.
    """
    upstream_request = http.build_request(
        request.method,
        url,
        params=request.query_params,
        content=body,
        headers=headers,
    )

    try:
        upstream = await http.send(upstream_request, stream=True)
    except httpx.TimeoutException as exc:
        log.warning("upstream_timeout", extra={"upstream": upstream_base, "path": safe_path})
        raise UpstreamTimeout() from exc
    except httpx.HTTPError as exc:
        log.warning(
            "upstream_unreachable",
            extra={"upstream": upstream_base, "path": safe_path, "reason": type(exc).__name__},
        )
        raise UpstreamUnavailable() from exc

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            # Always, including when the browser goes away mid-answer: a connection
            # left open here is one held against the upstream too.
            await upstream.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        # The same header rules as the buffered path. A streamed response is not a
        # hole in them - an upstream still has no business setting cookies.
        headers={
            k: v for k, v in upstream.headers.items() if k.lower() not in BLOCKED_RESPONSE_HEADERS
        },
    )
