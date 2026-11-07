"""Request-scoped dependencies.

The composition point between the HTTP layer and the services: routes declare what
they need, and nothing below this file knows a Request exists.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.access import Principal, current_principal
from app.db import get_db
from app.models import AppUser
from app.services.user import resolve_actor


def current_actor(
    db: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
) -> AppUser:
    """The local row every audit entry points at.

    A dependency rather than a call inside each handler, so a write cannot be made
    without an actor by forgetting a line - the row is resolved before the handler
    body runs.
    """
    return resolve_actor(db, principal)
