"""Access control: is this caller allowed to do this?

The service authenticates nobody. Keycloak issued the token, the BFF verified it -
signature against the published keys, issuer, audience, expiry - and forwarded the
decoded claims as headers. There is no token here to check, no JWKS cache, no clock
skew policy and no password.

Whether the caller is the BFF at all is a different question, answered one layer out
in ``gateway``. By the time anything here runs, that is settled.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Request

from app.errors import Forbidden, NotAuthenticated

log = logging.getLogger("salary.access")

SUB_HEADER = "X-User-Sub"
EMAIL_HEADER = "X-User-Email"
NAME_HEADER = "X-User-Name"
ROLES_HEADER = "X-User-Roles"


class Role(StrEnum):
    """The realm roles, spelled exactly as Keycloak spells them.

    Two, and no hierarchy. An ADMIN is not implicitly an HR_MANAGER: a user who needs
    both is granted both in the realm, which keeps "what can this person do" a
    question with one answer, readable in Keycloak rather than inferred from code.
    """

    ADMIN = "admin"
    HR_MANAGER = "hr_manager"


@dataclass(frozen=True)
class Principal:
    """Who the BFF says is calling. Frozen: a request handler cannot grant itself a role."""

    sub: str
    email: str | None
    display_name: str | None
    roles: frozenset[str]

    def has_any(self, *roles: Role) -> bool:
        return any(role.value in self.roles for role in roles)


def _parse_roles(raw: str | None) -> frozenset[str]:
    """Case-folded, blanks dropped. Keycloak emits lowercase; a proxy or a hand-run
    curl may not, and a role that fails to match because of its case fails open to
    a 403, which is at least the safe direction - but a confusing one."""
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def current_principal(request: Request) -> Principal:
    """Build the caller from the forwarded claims.

    Reached only after the gateway guard, so these headers are the BFF's.
    """
    sub = request.headers.get(SUB_HEADER)
    if not sub:
        # The gateway key proved the BFF called. It says nothing about on whose
        # behalf, so a request with no subject is still unauthenticated.
        log.warning(
            "missing_subject",
            extra={
                "path": request.url.path,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        raise NotAuthenticated()

    return Principal(
        sub=sub,
        email=request.headers.get(EMAIL_HEADER),
        display_name=request.headers.get(NAME_HEADER),
        roles=_parse_roles(request.headers.get(ROLES_HEADER)),
    )


def require_role(*allowed: Role):
    """Dependency factory: this route needs one of these roles.

    403, not 404. The caller is already known, so there is nothing to conceal by
    pretending the route does not exist - and a 404 here would send the next person
    debugging it hunting a routing bug that is not there.
    """
    if not allowed:  # pragma: no cover - a programming error, caught at import
        raise ValueError("require_role() needs at least one role")

    names = ", ".join(role.value for role in allowed)

    def dependency(
        request: Request, principal: Principal = Depends(current_principal)
    ) -> Principal:
        if not principal.has_any(*allowed):
            log.info(
                "forbidden",
                extra={
                    "sub": principal.sub,
                    "needs": names,
                    "has": sorted(principal.roles),
                    "request_id": getattr(request.state, "request_id", None),
                },
            )
            raise Forbidden(f"This action requires one of: {names}.")
        return principal

    return dependency
