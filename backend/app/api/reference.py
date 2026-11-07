"""Reference data for forms and filters.

One endpoint, four lists. The edit form needs all of them before it can render a
valid choice, and four round trips is four chances for one to fail and leave a
dropdown mysteriously empty.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import Role, require_role
from app.db import get_db
from app.models import Band, Country, Currency, Department
from app.schemas.reference import BandRead, CountryRead, CurrencyRead, DepartmentRead, ReferenceData

router = APIRouter(tags=["reference"])


@router.get(
    "/reference",
    response_model=ReferenceData,
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def reference(db: Session = Depends(get_db)) -> ReferenceData:
    """Everything a form or a filter needs to offer a valid choice.

    Authenticated, though none of it is secret: department and band names are company
    structure, and an unauthenticated endpoint is one an attacker can enumerate the
    org chart from without ever logging in.

    Only active departments and bands. A retired one stays readable in historical
    reports - that is why it is a flag and not a delete - but offering it in a
    dropdown for a new record would re-populate something the company retired.
    """
    return ReferenceData(
        countries=[
            CountryRead.model_validate(row)
            for row in db.scalars(select(Country).order_by(Country.name))
        ],
        currencies=[
            CurrencyRead.model_validate(row)
            for row in db.scalars(select(Currency).order_by(Currency.code))
        ],
        departments=[
            DepartmentRead.model_validate(row)
            for row in db.scalars(
                select(Department).where(Department.is_active).order_by(Department.name)
            )
        ],
        # Ordered by rank, not name: sorted as text they read L1, L10, L11, L2, and
        # the UI should not have to know that to render a sensible dropdown.
        bands=[
            BandRead.model_validate(row)
            for row in db.scalars(select(Band).where(Band.is_active).order_by(Band.rank))
        ],
    )
