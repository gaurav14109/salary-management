"""The ORM mirrors the schema; it does not define it.

`devops/ddl` and the alembic chain own the database. These classes describe it to
Python, which means they can disagree with it - and the disagreement surfaces as a
confusing runtime error rather than a failed migration. This file turns that into a
failed test instead, for every table rather than for whichever one someone
remembered.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.models import AppUser, Employee, FxRate, Salary

MODELS = [cls for cls in Base.registry._class_registry.values() if hasattr(cls, "__tablename__")]

# created_at / updated_at carry a server_default, so the model never supplies one and
# SQLAlchemy marks them nullable regardless of the column.
SERVER_FILLED = {"created_at", "updated_at"}


def db_columns(db, table: str) -> dict[str, bool]:
    rows = db.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = :t"
        ),
        {"t": table},
    ).all()
    return {name: nullable == "YES" for name, nullable in rows}


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_every_model_names_every_column_its_table_has(db, model):
    """A column added by a migration and missed here is invisible to the application
    until something reads it and finds nothing."""
    actual = db_columns(db, model.__tablename__)
    assert actual, f"{model.__tablename__} is not in the database at all"

    mapped = {column.name for column in inspect(model).columns}
    assert mapped == set(actual)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_every_model_agrees_about_what_may_be_null(db, model):
    """The direction that matters: a column the model thinks is optional and the
    database requires fails at INSERT, in production, under load."""
    actual = db_columns(db, model.__tablename__)

    for column in inspect(model).columns:
        if column.name in SERVER_FILLED:
            continue
        assert column.nullable == actual[column.name], f"{model.__tablename__}.{column.name}"


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_no_model_declares_a_table_the_database_does_not_have(db, model):
    found = db.execute(text("SELECT to_regclass(:t)"), {"t": model.__tablename__}).scalar_one()
    assert found is not None


# --- the guarantees that must be the database's, not the application's ----------
#
# Each of these is relied on by code elsewhere. A UNIQUE declared only in Python is
# a comment.


def test_exactly_one_open_salary_row_per_employee(db, employee_id, actor_id):
    """The partial unique index is what makes "exactly one current salary" a database
    guarantee rather than an application convention."""
    from datetime import date
    from decimal import Decimal

    def open_row(start: str) -> Salary:
        return Salary(
            employee_id=employee_id,
            amount=Decimal("1200000.000"),
            currency="INR",
            usd_amount=Decimal("14379.87"),
            fx_units_per_usd=Decimal("83.45000000"),
            fx_rate_date=date(2026, 1, 1),
            effective_from=date.fromisoformat(start),
            effective_to=None,
            created_by=actor_id,
        )

    db.add(open_row("2026-01-01"))
    db.flush()
    db.add(open_row("2026-06-01"))

    with pytest.raises(IntegrityError) as caught:
        db.flush()
    assert "ux_salary_open" in str(caught.value)


def test_a_duplicate_fx_rate_for_one_currency_and_date_is_rejected(db, actor_id):
    """Without this an admin entering a correction creates a second rate for the same
    day and the lookup silently picks one of them."""
    from datetime import date
    from decimal import Decimal

    for rate in ("83.45000000", "83.50000000"):
        db.add(
            FxRate(
                currency="INR",
                units_per_usd=Decimal(rate),
                as_of=date(2026, 1, 1),
                created_by=actor_id,
            )
        )

    with pytest.raises(IntegrityError) as caught:
        db.flush()
    assert "ux_fx_currency_date" in str(caught.value)


def test_a_duplicate_employee_code_is_rejected(db, reference_data):
    from datetime import date

    def employee(code: str) -> Employee:
        return Employee(
            employee_code=code,
            first_name="Priya",
            last_name="Sharma",
            country_code="IN",
            department_id=reference_data["department_id"],
            job_title="Engineer",
            hire_date=date(2024, 4, 1),
        )

    db.add(employee("EMP-DUP"))
    db.flush()
    db.add(employee("EMP-DUP"))

    with pytest.raises(IntegrityError) as caught:
        db.flush()
    assert "employee_code" in str(caught.value)


def test_external_id_is_unique_in_the_database_not_merely_in_the_model(db):
    """resolve_actor's race recovery depends on this index existing."""
    found = db.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'app_user' "
            "AND indexdef ILIKE '%UNIQUE%external_id%'"
        )
    ).first()
    assert found is not None


def test_app_user_has_no_role_column(db):
    """A local copy of an authorization fact drifts the moment someone edits the
    realm, and it drifts open. The absence is the design."""
    assert "role" not in {column.name for column in inspect(AppUser).columns}
    assert "role" not in db_columns(db, "app_user")
