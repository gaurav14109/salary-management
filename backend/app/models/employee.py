"""The person. One of the two tables the product is about."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:  # import for the annotation only - a runtime import would cycle
    from app.models.salary import Salary

STATUSES = ("ACTIVE", "INACTIVE")


class Employee(Base):
    __tablename__ = "employee"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_employee_status"),
        # A leaver must carry the date they left. set_status closes their open salary
        # row with that date in the same transaction.
        CheckConstraint(
            "status = 'ACTIVE' OR termination_date IS NOT NULL",
            name="ck_employee_inactive_dated",
        ),
        CheckConstraint(
            "termination_date IS NULL OR termination_date >= hire_date",
            name="ck_employee_termination_after_hire",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The ID HR already uses, and the match key the import joins on.
    employee_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Nullable; Postgres allows many NULLs under a UNIQUE constraint.
    email: Mapped[str | None] = mapped_column(String(255), unique=True)

    country_code: Mapped[str] = mapped_column(ForeignKey("country.code"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("department.id"), nullable=False)
    job_title: Mapped[str] = mapped_column(String(150), nullable=False)

    # Nullable on purpose: contractors and executives commonly sit outside a banding
    # system, and junk in a required field is worse than an empty one.
    band_id: Mapped[int | None] = mapped_column(ForeignKey("band.id"))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date)

    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_job.id"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Ordered newest-first so history reads the way it is displayed. Not lazy="joined":
    # the list page fetches 50 employees and does not want 50 salary histories with
    # them.
    salaries: Mapped[list["Salary"]] = relationship(
        back_populates="employee",
        order_by="Salary.effective_from.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Employee id={self.id} code={self.employee_code!r}>"
