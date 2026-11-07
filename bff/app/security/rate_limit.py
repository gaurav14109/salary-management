"""A small sliding-window limiter for the login endpoints.

Honest about what it is: in-memory and per-instance, so two instances allow twice the
rate and a restart forgets everything. A real deployment limits at the edge, where it
can see every instance.

It is here anyway because the alternative is nothing, and an unlimited login endpoint
is an invitation (OWASP A07). Keycloak also has brute-force detection enabled; this
protects the hop in front of it.
"""

import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60
_buckets: dict[str, deque[float]] = defaultdict(deque)
# Stops a flood of distinct source addresses turning the limiter itself into the
# memory-exhaustion bug it was added to prevent.
_MAX_TRACKED_KEYS = 10_000


def allow(key: str, limit: int) -> bool:
    now = time.monotonic()
    bucket = _buckets[key]

    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()

    if not bucket and len(_buckets) > _MAX_TRACKED_KEYS:
        _prune(now)

    if len(bucket) >= limit:
        return False

    bucket.append(now)
    return True


def _prune(now: float) -> None:
    for key in [k for k, v in _buckets.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
        del _buckets[key]


def reset() -> None:
    """For tests. Production never needs this; the window expires on its own."""
    _buckets.clear()
