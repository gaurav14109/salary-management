"""The schema enforces the design, not the application.

Each test here proves that a rule in the ER diagram was actually implemented. A rule
the application merely remembers is a rule that breaks the first time someone adds a
second code path; a rule the database holds cannot be bypassed by any of them.
"""

import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

SALARY_INSERT = text(
    "INSERT INTO salary (employee_id, amount, currency, usd_amount, fx_units_per_usd, "
    "fx_rate_date, effective_from, effective_to, created_by) "
    "VALUES (:emp, :amount, :currency, :usd, :rate, :rate_date, :from, :to, :actor)"
)


def insert_salary(
    db,
    employee_id,
    actor_id,
    *,
    amount="1200000",
    effective_from="2025-04-01",
    effective_to=None,
    currency="INR",
    usd="14380.00",
    rate="83.45",
):
    db.execute(
        SALARY_INSERT,
        {
            "emp": employee_id,
            "amount": amount,
            "currency": currency,
            "usd": usd,
            "rate": rate,
            "rate_date": effective_from,
            "from": effective_from,
            "to": effective_to,
            "actor": actor_id,
        },
    )


# ── the invariant everything else depends on ────────────────────────────────────


def test_an_employee_cannot_have_two_open_salary_rows(db, employee_id, actor_id):
    """Every report joins on effective_to IS NULL. Two open rows would double-count
    one person in total payroll with nothing raising."""
    insert_salary(db, employee_id, actor_id, effective_from="2025-04-01")

    with pytest.raises(IntegrityError, match="ux_salary_open"):
        insert_salary(db, employee_id, actor_id, effective_from="2026-04-01")


def test_closing_the_first_row_allows_a_second(db, employee_id, actor_id):
    insert_salary(db, employee_id, actor_id, effective_from="2025-04-01", effective_to="2026-03-31")
    insert_salary(db, employee_id, actor_id, effective_from="2026-04-01")

    open_rows = db.execute(
        text("SELECT count(*) FROM salary WHERE employee_id = :e AND effective_to IS NULL"),
        {"e": employee_id},
    ).scalar_one()
    assert open_rows == 1


def test_a_salary_cannot_end_before_it_starts(db, employee_id, actor_id):
    with pytest.raises(IntegrityError, match="ck_salary_range"):
        insert_salary(
            db, employee_id, actor_id, effective_from="2026-04-01", effective_to="2026-03-01"
        )


def test_a_salary_amount_must_be_positive(db, employee_id, actor_id):
    with pytest.raises(IntegrityError, match="ck_salary_positive"):
        insert_salary(db, employee_id, actor_id, amount="0")


# ── identity and reference integrity ────────────────────────────────────────────


def test_employee_code_is_unique(db, reference_data, employee_id):
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO employee (employee_code, first_name, last_name, country_code, "
                "department_id, job_title, hire_date) "
                "VALUES ('EMP-1042', 'Other', 'Person', 'IN', :dept, 'Engineer', '2025-01-01')"
            ),
            {"dept": reference_data["department_id"]},
        )


def test_an_unknown_department_is_rejected(db, reference_data):
    """The import turns this into a row error. The database makes it impossible to skip."""
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO employee (employee_code, first_name, last_name, country_code, "
                "department_id, job_title, hire_date) "
                "VALUES ('EMP-9', 'A', 'B', 'IN', 99999, 'Engineer', '2025-01-01')"
            )
        )


def test_department_names_collide_regardless_of_case(db):
    """'Engineering' and 'engineering' would split one department's payroll across
    two dashboard rows."""
    db.execute(text("INSERT INTO department (name) VALUES ('Finance')"))

    with pytest.raises(IntegrityError, match="ux_department_name"):
        db.execute(text("INSERT INTO department (name) VALUES ('finance')"))


def test_two_bands_cannot_share_a_rank(db):
    db.execute(text("INSERT INTO band (name, rank) VALUES ('L7', 7)"))

    with pytest.raises(IntegrityError, match="ux_band_rank"):
        db.execute(text("INSERT INTO band (name, rank) VALUES ('Principal', 7)"))


def test_one_fx_rate_per_currency_per_day(db, reference_data, actor_id):
    db.execute(
        text(
            "INSERT INTO fx_rate (currency, units_per_usd, as_of, created_by) "
            "VALUES ('INR', 83.45, '2026-04-01', :a)"
        ),
        {"a": actor_id},
    )

    with pytest.raises(IntegrityError, match="ux_fx_currency_date"):
        db.execute(
            text(
                "INSERT INTO fx_rate (currency, units_per_usd, as_of, created_by) "
                "VALUES ('INR', 83.50, '2026-04-01', :a)"
            ),
            {"a": actor_id},
        )


def test_a_leaver_must_carry_the_date_they_left(db, reference_data):
    with pytest.raises(IntegrityError, match="ck_employee_inactive_dated"):
        db.execute(
            text(
                "INSERT INTO employee (employee_code, first_name, last_name, country_code, "
                "department_id, job_title, hire_date, status) "
                "VALUES ('EMP-7', 'A', 'B', 'IN', :dept, 'Engineer', '2025-01-01', 'INACTIVE')"
            ),
            {"dept": reference_data["department_id"]},
        )


def test_an_employee_without_a_band_is_valid(db, reference_data):
    """Contractors and executives sit outside a banding system."""
    db.execute(
        text(
            "INSERT INTO employee (employee_code, first_name, last_name, country_code, "
            "department_id, job_title, hire_date) "
            "VALUES ('EMP-8', 'A', 'B', 'IN', :dept, 'Contractor', '2025-01-01')"
        ),
        {"dept": reference_data["department_id"]},
    )


# ── the current_salary view ─────────────────────────────────────────────────────


def test_current_salary_shows_the_open_row(db, employee_id, actor_id):
    insert_salary(db, employee_id, actor_id)

    row = db.execute(
        text("SELECT employee_code, usd_amount FROM current_salary WHERE employee_id = :e"),
        {"e": employee_id},
    ).one()
    assert row.employee_code == "EMP-1042"


def test_current_salary_excludes_closed_rows(db, employee_id, actor_id):
    insert_salary(db, employee_id, actor_id, effective_from="2024-04-01", effective_to="2025-03-31")

    count = db.execute(
        text("SELECT count(*) FROM current_salary WHERE employee_id = :e"), {"e": employee_id}
    ).scalar_one()
    assert count == 0


def test_current_salary_excludes_a_terminated_employee(db, employee_id, actor_id):
    """Total payroll would otherwise be overstated by every leaver, permanently."""
    insert_salary(db, employee_id, actor_id)
    db.execute(
        text(
            "UPDATE employee SET status = 'INACTIVE', termination_date = '2026-09-30' WHERE id = :e"
        ),
        {"e": employee_id},
    )

    count = db.execute(
        text("SELECT count(*) FROM current_salary WHERE employee_id = :e"), {"e": employee_id}
    ).scalar_one()
    assert count == 0


def test_a_three_decimal_currency_keeps_its_last_digit(db, employee_id, actor_id):
    """KWD, BHD, OMR and JOD have three minor units. At NUMERIC(14,2) the third digit
    would be silently rounded away, and nobody would notice until Finance did."""
    db.execute(
        text("INSERT INTO currency (code, name, minor_units) VALUES ('KWD', 'Kuwaiti Dinar', 3)")
    )
    insert_salary(
        db,
        employee_id,
        actor_id,
        amount="12345.678",
        currency="KWD",
        usd="40234.56",
        rate="0.30684",
    )

    stored = db.execute(
        text("SELECT amount FROM salary WHERE employee_id = :e"), {"e": employee_id}
    ).scalar_one()
    assert str(stored) == "12345.678"


def test_app_user_records_no_per_request_state(db):
    """A last_seen_at column would make every GET a write transaction, all of them
    contending for the same row, to serve a column no feature reads."""
    columns = {
        r[0]
        for r in db.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'app_user'")
        )
    }
    assert columns == {"id", "external_id", "email", "display_name", "created_at"}


def test_the_database_is_at_the_latest_migration(db):
    """A schema built by anything other than the migration chain is a schema no
    other environment can reproduce."""
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    versions_dir = pathlib.Path(__file__).resolve().parents[2] / "devops" / "alembic" / "versions"
    latest = sorted(p.name.split("_")[0] for p in versions_dir.glob("[0-9]*.py"))[-1]

    assert version == latest
