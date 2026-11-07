"""initial schema

Applies every file in ddl/ in filename order: the reference tables, app_user,
import_job, the core employee and salary tables, audit_log, fx_rate, the indexes
and the current_salary view.

The DDL stays plain SQL so the schema a reviewer reads is the schema the database
gets. Alembic is the runner and the version ledger, not the author.

This chain starts at the schema as it stands today. Three changes were made before
Alembic existed - widening amount to NUMERIC(14,3) for Gulf currencies, dropping
last_seen_at, and renaming the FX columns - and they are recorded in git history
rather than invented as migrations here. Writing migrations for changes that never
ran anywhere would be a fiction, and a chain that lies about the past is worse than
one that starts later than you would like.

Revision ID: 0001
Revises:
"""

import pathlib

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# versions/ -> alembic/ -> devops/, where ddl/ sits beside alembic/.
DDL_DIR = pathlib.Path(__file__).resolve().parents[2] / "ddl"


def upgrade() -> None:
    for path in sorted(DDL_DIR.glob("*.sql")):
        op.execute(path.read_text())


def downgrade() -> None:
    """Drop everything this migration created, and nothing else.

    DROP SCHEMA public CASCADE would be simpler and takes alembic_version with it,
    leaving Alembic unable to record the rollback it just performed.

    Dropping by introspection rather than by a hand-written list, so adding a file
    to ddl/ cannot leave an orphan behind: a reverse-order list is a second place
    to remember, and the one that gets forgotten.
    """
    op.execute(
        """
        DO $$
        DECLARE obj record;
        BEGIN
            FOR obj IN
                SELECT table_name FROM information_schema.views
                WHERE table_schema = 'public'
            LOOP
                EXECUTE format('DROP VIEW IF EXISTS public.%I CASCADE', obj.table_name);
            END LOOP;

            FOR obj IN
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'alembic_version'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', obj.tablename);
            END LOOP;
        END $$;
        """
    )
