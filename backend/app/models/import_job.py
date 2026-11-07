"""One upload attempt, its outcome, and its per-row errors."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

STATUSES = ("PENDING", "SUCCESS", "FAILED")


class ImportJob(Base):
    __tablename__ = "import_job"
    __table_args__ = (
        # A CHECK rather than a Postgres ENUM type: adding a value to an enum needs
        # ALTER TYPE and cannot run inside every migration path, while a CHECK is a
        # one-line change.
        CheckConstraint("status IN ('PENDING', 'SUCCESS', 'FAILED')", name="ck_import_job_status"),
        CheckConstraint(
            "total_rows >= 0 AND success_rows >= 0 AND error_rows >= 0 "
            "AND employees_created >= 0 AND employees_updated >= 0 "
            "AND salaries_created >= 0 AND salaries_unchanged >= 0",
            name="ck_import_job_counts",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    employees_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    employees_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    salaries_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Rows whose pay matched the current open salary row, so no row was written and
    # no audit entry made. This is what makes re-uploading an exported file safe.
    salaries_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # [{row_number, field, message}] - precise enough to fix in Excel and re-upload.
    # When error_rows > 0 nothing was committed at all.
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
