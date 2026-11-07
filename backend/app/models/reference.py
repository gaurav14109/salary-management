"""Reference data: small, static, and the reason the dashboard can be trusted.

These hold values an enum could have held. They are tables because the dashboard
GROUPs BY all four, and a group-by over free text produces numbers that are wrong
without anything failing - 'Engineering', 'engineering' and 'Eng ' become three
departments, splitting one department's payroll across three chart rows.
"""

from sqlalchemy import Boolean, CheckConstraint, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Country(Base):
    __tablename__ = "country"

    # ISO 3166-1 alpha-2. The country of the employing entity, not where the person
    # physically sits.
    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Currency(Base):
    __tablename__ = "currency"
    __table_args__ = (
        CheckConstraint("minor_units BETWEEN 0 AND 4", name="ck_currency_minor_units"),
    )

    # ISO 4217 alphabetic: USD, INR, EUR.
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # 2 for USD, 0 for JPY, 3 for KWD. Drives display, and catches cents entered for
    # a zero-decimal currency.
    minor_units: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class Department(Base):
    __tablename__ = "department"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # A retired department stays readable in historical reports rather than being
    # deleted.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Uniqueness is on lower(name), an expression index declared in the DDL. It is
    # not repeated here: SQLAlchemy would emit a plain UNIQUE(name), which is a
    # different and weaker constraint, and the model would then disagree with the
    # database about what is enforced.


class Band(Base):
    __tablename__ = "band"
    __table_args__ = (CheckConstraint("rank > 0", name="ck_band_rank_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)

    # Sort order, and the reason band is a table rather than an enum: bands are
    # ordered and text is not, so a text column sorts L1, L10, L11, L2 - and an
    # average-pay-by-band chart in that order is meaningless. 1 is most junior.
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
