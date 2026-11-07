"""seed reference data

Applies every file in seed/ in filename order: currencies, countries, departments,
bands, the system actor, and one indicative FX rate per currency.

Separate from 0001 because the two answer different questions. 0001 is "what shape
is the database"; this is "what must already be in it for the application to
function". Keeping them apart means a schema change and a reference-data change are
reviewable on their own, and a seed that turns out wrong can be corrected in a new
revision without touching the schema chain.

Why reference data is seeded by a migration at all: employee.department_id and
salary.currency are NOT NULL foreign keys, the import refuses an unknown value
rather than inventing one, and there is no screen for creating departments. An empty
reference table means nothing can be created by any route, with no way out. The
rows have to exist before the application is usable, which makes them part of
getting the database to a working state - which is what a migration is for.

Every statement is ON CONFLICT DO NOTHING, so this is safe against a database where
some of these rows were added by hand before the migration ran.

Revision ID: 0002
Revises: 0001
"""

import pathlib

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# versions/ -> alembic/ -> devops/, where seed/ sits beside alembic/.
SEED_DIR = pathlib.Path(__file__).resolve().parents[2] / "seed"


def upgrade() -> None:
    for path in sorted(SEED_DIR.glob("*.sql")):
        op.execute(path.read_text())


def downgrade() -> None:
    """Remove the seeded rows, and only those not in use.

    A blind DELETE would fail against any employee or salary referencing them, which
    is the correct outcome - but it would fail halfway, leaving the reference tables
    partly emptied and the migration neither applied nor rolled back. Deleting only
    unreferenced rows makes the downgrade total: it either removes a row or leaves
    it, and never breaks a foreign key.
    """
    # Seeded rates first: they reference the currencies and the system actor below,
    # so any other order leaves rows that nothing can then delete. Only rates this
    # migration created - an admin's real rates are not this revision's to remove.
    op.execute(
        "DELETE FROM fx_rate WHERE created_by IN "
        "(SELECT id FROM app_user WHERE external_id = 'system:seed')"
    )
    op.execute(
        "DELETE FROM app_user WHERE external_id = 'system:seed' "
        "AND id NOT IN (SELECT created_by FROM fx_rate) "
        "AND id NOT IN (SELECT created_by FROM salary) "
        "AND id NOT IN (SELECT actor_id FROM audit_log)"
    )
    op.execute(
        "DELETE FROM band WHERE id NOT IN (SELECT band_id FROM employee WHERE band_id IS NOT NULL)"
    )
    op.execute("DELETE FROM department WHERE id NOT IN (SELECT department_id FROM employee)")
    op.execute("DELETE FROM country WHERE code NOT IN (SELECT country_code FROM employee)")
    op.execute(
        "DELETE FROM currency WHERE code NOT IN "
        "(SELECT currency FROM salary UNION SELECT currency FROM fx_rate)"
    )
