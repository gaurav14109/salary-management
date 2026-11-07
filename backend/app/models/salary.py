"""A pay amount that was true over a date range.

Deliberately not a field on employee. One row per change, exactly one open at a time.
Without this, "what did we pay in Q2" has no answer.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:  # import for the annotation only - a runtime import would cycle
    from app.models.employee import Employee

CHANGE_REASONS = (
    "NEW_HIRE",
    "PROMOTION",
    "MERIT",
    "MARKET_ADJUSTMENT",
    "CORRECTION",
    "IMPORT",
)


class Salary(Base):
    __tablename__ = "salary"
    __table_args__ = (
        CheckConstraint("amount > 0 AND usd_amount > 0", name="ck_salary_positive"),
        CheckConstraint("fx_units_per_usd > 0", name="ck_salary_rate_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="ck_salary_range"
        ),
        CheckConstraint(
            "change_reason IS NULL OR change_reason IN "
            "('NEW_HIRE', 'PROMOTION', 'MERIT', 'MARKET_ADJUSTMENT', 'CORRECTION', 'IMPORT')",
            name="ck_salary_change_reason",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employee.id"), nullable=False)

    # Money is NUMERIC, never a float - Decimal in Python, NUMERIC in Postgres, with
    # nothing in between that could round it.
    #
    # Three decimal places, not two, because KWD, BHD, OMR and JOD have three. At two
    # a Kuwaiti salary silently loses its last digit, and that surfaces six months
    # later from Finance. The amount is in the currency the employee is PAID in,
    # which is not inferable from their country: an expat on a US contract is paid in
    # USD wherever they sit.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    currency: Mapped[str] = mapped_column(ForeignKey("currency.code"), nullable=False)

    # Frozen at write time, never recomputed. Converting at read time would mean
    # correcting one FX rate silently rewrites a payroll total Finance already
    # received. This duplication is the point, not an oversight.
    usd_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Units of local currency per 1 USD, so usd_amount = amount / fx_units_per_usd.
    # Named this way deliberately: a column called rate_to_usd reads as something to
    # multiply by, and multiplying 1,200,000 INR by 83.45 overstates that salary
    # roughly 7,000 times.
    fx_units_per_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fx_rate_date: Mapped[date] = mapped_column(Date, nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)  # inclusive
    effective_to: Mapped[date | None] = mapped_column(Date)  # inclusive; NULL = current

    change_reason: Mapped[str | None] = mapped_column(String(30))
    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_job.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee: Mapped["Employee"] = relationship(back_populates="salaries")

    # "Exactly one open row per employee" is a partial unique index -
    # ux_salary_open ON salary (employee_id) WHERE effective_to IS NULL - declared in
    # the DDL. It is a database guarantee rather than an application convention, and
    # it is not restated here because SQLAlchemy's UniqueConstraint cannot express
    # the WHERE clause and would emit a weaker, wrong constraint instead.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Salary employee_id={self.employee_id} from={self.effective_from}>"
