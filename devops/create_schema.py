"""Bring the local database to the current schema.

A thin wrapper over Alembic, not a second way to build the schema. Two paths that
both create tables drift the moment one of them is updated and the other is not, so
this one drops and delegates.

    python scripts/create_schema.py              # apply pending migrations
    python scripts/create_schema.py --reset      # drop everything, then migrate

--reset destroys all data and exists for local development and the test suite. On a
database with rows in it, the answer is `alembic upgrade head` and nothing else.
"""

import argparse
import pathlib
import subprocess
import sys

from sqlalchemy import create_engine, text

# The one definition of the database URL lives with the service, not here.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.config import get_settings  # noqa: E402

# alembic.ini sits beside this file; alembic is run from here.
ROOT = pathlib.Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="drop the public schema first. Destroys all data; local use only.",
    )
    args = parser.parse_args()

    if args.reset:
        engine = create_engine(get_settings().database_url, future=True)
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        print("dropped public schema")

    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
