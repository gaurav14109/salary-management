"""Who did it, for the audit trail.

Named app_user because "user" is reserved in Postgres and quoting it in every query
is a permanent papercut.

Identity lives in Keycloak. This application never sees a password, never stores one
and never issues a token, so there is no password hash here - and no role column
either: the token carries the roles, and a local copy of an authorization fact drifts
the moment someone edits the realm. It drifts open.

The row exists for one reason: audit_log.actor_id needs a stable local target. "Who
changed this salary" must still answer in three years, from the database alone,
without Keycloak being reachable or the realm still existing in its current form.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The Keycloak 'sub'. Keyed on this rather than email because people change
    # email, and an audit trail keyed on a mutable field is not an audit trail.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # Copied from the token for display, deliberately not authoritative. Nullable
    # because Keycloak does not guarantee the claim.
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # There is no last_seen_at. Tracking it would turn every GET into a write
    # transaction, all of them contending for the same row, in exchange for a column
    # no feature reads.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AppUser id={self.id} external_id={self.external_id!r}>"
