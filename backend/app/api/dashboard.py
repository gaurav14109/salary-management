"""The dashboard endpoint.

One call, every figure, one filter applied to all of them. Six endpoints would mean
six chances for one to fail and leave the page showing a total that disagrees with
the chart beside it.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.access import Role, require_role
from app.db import get_db
from app.schemas.dashboard import DashboardRead
from app.services import analytics

router = APIRouter(tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=DashboardRead,
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def dashboard(
    country: str | None = Query(default=None, min_length=2, max_length=2),
    department: int | None = None,
    band: int | None = None,
    db: Session = Depends(get_db),
) -> DashboardRead:
    """Aggregate payroll, in USD.

    Authenticated like everything else, and for a stronger reason than most: a
    total-compensation figure for the whole company is as disclosive as any
    individual salary, and easier to carry away.

    Every figure sums `salary.usd_amount`, frozen at write time. Nothing here
    converts a currency, so correcting an FX rate cannot rewrite a quarter that has
    already been reported.
    """
    return DashboardRead.model_validate(
        analytics.summary(db, country=country, department=department, band=band)
    )
