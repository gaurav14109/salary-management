"""Append-only. Who changed what, and when.

A compliance fact, deliberately separate from salary history, which is a business
fact that feeds every report. Collapsing the two means either polluting payroll
totals with correction records or losing the ability to prove who made a change.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ENTITY_TYPES = ("EMPLOYEE", "SALARY", "FX_RATE", "DEPARTMENT", "BAND")
ACTIONS = ("CREATE", "UPDATE", "DELETE")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('EMPLOYEE', 'SALARY', 'FX_RATE', 'DEPARTMENT', 'BAND')",
            name="ck_audit_entity_type",
        ),
        CheckConstraint("action IN ('CREATE', 'UPDATE', 'DELETE')", name="ck_audit_action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Polymorphic, so deliberately not a foreign key. One audit table per entity
    # would triple the schema to buy referential integrity on an append-only log that
    # no report joins. The mitigation is that only the audit service writes here.
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # Decimals are stored as strings so no precision is lost through JSON number
    # handling.
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    actor_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
