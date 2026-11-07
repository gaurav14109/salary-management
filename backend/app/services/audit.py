"""The audit trail writer.

One function, and one rule it exists to enforce: the audit row lives or dies with the
mutation it describes. It shares the caller's Session and never commits, so a change
that fails afterwards cannot leave behind a record of something that never happened,
and a record that fails to write takes the change down with it.

That is why this is not a listener, a decorator or a background task. Every one of
those would make the audit row a separate thing that usually happens - and "usually"
is not what a compliance trail means.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.models.audit import Action, EntityType

log = logging.getLogger("salary.audit")


def record(
    db: Session,
    *,
    entity_type: EntityType | str,
    entity_id: int,
    action: Action | str,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
    actor_id: int,
) -> AuditLog:
    """Write one audit entry into the caller's transaction.

    Flushed, not committed. Flushed because a failure must surface here, while there
    is still context about which change caused it, rather than at commit time where
    the stack trace says only that something went wrong. Not committed because the
    caller owns the transaction boundary - `get_db` commits once, at the end of a
    request, and a service that committed early would be deciding that a half-written
    change is final.
    """
    entry = AuditLog(
        entity_type=str(entity_type),
        entity_id=entity_id,
        action=str(action),
        old_value=_jsonable(old),
        new_value=_jsonable(new),
        actor_id=actor_id,
    )
    db.add(entry)
    db.flush()

    log.info(
        "audit_recorded",
        extra={
            "entity_type": str(entity_type),
            "entity_id": entity_id,
            "action": str(action),
            "actor_id": actor_id,
        },
    )
    return entry


def _jsonable(value: Any) -> Any:
    """Convert to something JSONB can hold without losing what it means.

    Decimals become strings, not numbers. A JSONB number is an IEEE 754 double, so a
    salary written as one comes back subtly different - and an audit trail that
    disagrees with the row it is supposed to prove is worse than no audit trail,
    because it will be believed.

    Dates become ISO strings for the same reason in a different direction: a date
    serialized by whatever default is nearest is a date nobody can parse back
    reliably.

    Recursive, because a value one level down is still a value that has to survive
    the round trip.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    # str before Sequence: a string is a sequence of strings, and recursing into one
    # would turn "ACTIVE" into a list of six characters.
    if isinstance(value, str | bytes):
        return value if isinstance(value, str) else value.decode()
    if isinstance(value, Sequence):
        return [_jsonable(item) for item in value]
    return value
