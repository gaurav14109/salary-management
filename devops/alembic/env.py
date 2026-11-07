"""Alembic environment.

The database URL comes from the application's own Settings rather than alembic.ini,
so migrations and the app can never point at different databases - the usual way a
migration gets applied to the wrong place. That import is why ../backend goes on the
path below: the migrations live here, but the one definition of where the database
is lives with the service that talks to it.

There is no target_metadata and no autogeneration. Every migration is explicit SQL,
because the three things that carry this schema - a partial unique index, check
constraints and a view - are exactly what autogenerate expresses badly or drops.
"""

import pathlib
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from app.config import get_settings  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review or for a DBA to apply."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # Postgres can run DDL inside a transaction, so a migration that fails
        # halfway leaves nothing behind.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
