"""Who is asking, as the BFF describes them.

Identical in shape to the salary service's own `access`, and deliberately a separate
copy rather than a shared library: these are two deployables, and a package shared
between them would couple their release cycles to save forty lines.

Nothing here authenticates. The BFF verified the token; this reads what it decided.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request

from app.errors import Forbidden, NotAuthenticated

log = logging.getLogger("chat.access")

SUB_HEADER = "X-User-Sub"
EMAIL_HEADER = "X-User-Email"
NAME_HEADER = "X-User-Name"
ROLES_HEADER = "X-User-Roles"


class Role(StrEnum):
    ADMIN = "admin"
    HR_MANAGER = "hr_manager"


@dataclass(frozen=True)
class Principal:
    """Frozen: a request handler cannot grant itself a role."""

    sub: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]

    def has_any(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


def current_principal(request: Request) -> Principal:
    sub = request.headers.get(SUB_HEADER, "").strip()
    if not sub:
        raise NotAuthenticated()

    raw = request.headers.get(ROLES_HEADER, "")
    roles = frozenset(role.strip() for role in raw.split(",") if role.strip())
    return Principal(
        sub=sub,
        email=request.headers.get(EMAIL_HEADER) or None,
        display_name=request.headers.get(NAME_HEADER) or None,
        roles=roles,
    )


def require_role(*allowed: Role):
    """A dependency that admits only these roles."""

    def guard(request: Request) -> Principal:
        principal = current_principal(request)
        if not principal.has_any(*allowed):
            log.warning(
                "forbidden",
                extra={"sub": principal.sub, "needed": [str(r) for r in allowed]},
            )
            raise Forbidden()
        return principal

    return guard
