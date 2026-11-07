"""Admin-maintained exchange rates. One rate per currency per day."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FxRate(Base):
    __tablename__ = "fx_rate"
    __table_args__ = (CheckConstraint("units_per_usd > 0", name="ck_fx_rate_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(ForeignKey("currency.code"), nullable=False)

    # 83.45 means 83.45 INR per 1 USD. Lookup takes the most recent rate on or before
    # the salary date; there is no fallback to 1.0 under any condition.
    units_per_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)

    as_of: Mapped[date] = mapped_column(Date, nullable=False)

    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # UNIQUE (currency, as_of) is declared in the DDL as ux_fx_currency_date. Without
    # it an admin entering a correction creates a second rate for the same day and
    # the lookup silently picks one of them.
