"""ORM models: the database as Python sees it.

One module per aggregate, re-exported here so a caller imports from `app.models`
rather than reaching into a file path. Importing this package imports every model,
which is what lets SQLAlchemy resolve the string names in relationships
(`Mapped[list["Salary"]]`) - they are looked up in the registry, so both sides must
have been imported before any mapper is configured.

The schema itself is owned by `devops/ddl` and the alembic chain. These classes
mirror it and never define it: two sources of truth for a column type is how a
migration and an application come to disagree in production. `tests/test_models.py`
compares the two and fails when they drift.

Two constraints are deliberately not restated here, because SQLAlchemy cannot
express them and would emit something weaker in their place:

  * `ux_salary_open` - one open salary row per employee, a partial unique index
    with a WHERE clause;
  * `ux_department_name` / `ux_band_name` - uniqueness on `lower(name)`, expression
    indexes.

Both live in `devops/ddl/` and are enforced by the database.
"""

from app.models.audit import AuditLog
from app.models.employee import Employee
from app.models.fx_rate import FxRate
from app.models.import_job import ImportJob
from app.models.reference import Band, Country, Currency, Department
from app.models.salary import Salary
from app.models.user import AppUser

__all__ = [
    "AppUser",
    "AuditLog",
    "Band",
    "Country",
    "Currency",
    "Department",
    "Employee",
    "FxRate",
    "ImportJob",
    "Salary",
]
