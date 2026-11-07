"""Turning a Keycloak subject into a local audit actor.

Just-in-time: there is no user administration screen and no sync job, because either
would be a second place where "who exists" is decided and a second thing to be out of
date. A person exists here the first time they do something worth recording.
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import Principal
from app.models import AppUser

log = logging.getLogger("salary.user")


def _find(db: Session, external_id: str) -> AppUser | None:
    return db.scalar(select(AppUser).where(AppUser.external_id == external_id))


def resolve_actor(db: Session, principal: Principal) -> AppUser:
    """Return the app_user row for this caller, creating it on first sight.

    Read first and write only when something differs, so the steady state of a
    request is one SELECT and no transaction upgrade. Writing unconditionally would
    make every GET a write, with every concurrent request for the same person
    contending for the same row.

    Roles are never written here. A stored copy of an authorization fact drifts the
    moment someone edits the realm, and it drifts open - the database would still say
    'admin' after Keycloak stopped saying it.
    """
    existing = _find(db, principal.sub)
    if existing is not None:
        _refresh_display_fields(existing, principal)
        return existing

    actor = AppUser(
        external_id=principal.sub,
        email=principal.email,
        display_name=principal.display_name,
    )
    try:
        # A savepoint, so losing the race rolls back this INSERT alone. Without it
        # the failure would poison the request's whole transaction, and a first-ever
        # request that happened to arrive twice would fail the user's actual work.
        with db.begin_nested():
            db.add(actor)
            db.flush()
    except IntegrityError:
        # Two first requests from the same new person arrived together. The unique
        # index on external_id decided it; this one lost and re-reads the winner's
        # row. Not an error - the outcome is exactly what was wanted.
        log.info("actor_create_raced", extra={"sub": principal.sub})
        winner = _find(db, principal.sub)
        if winner is None:  # pragma: no cover - would mean the index is gone
            raise
        _refresh_display_fields(winner, principal)
        return winner

    log.info("actor_created", extra={"sub": principal.sub, "actor_id": actor.id})
    return actor


def _refresh_display_fields(actor: AppUser, principal: Principal) -> None:
    """Keep the display copy current, and only when it has actually changed.

    These fields are a convenience for reading an audit row without calling Keycloak.
    external_id is never touched: it is the identity, and rewriting it would detach
    every audit row that points here.
    """
    if actor.email != principal.email:
        actor.email = principal.email
    if actor.display_name != principal.display_name:
        actor.display_name = principal.display_name
