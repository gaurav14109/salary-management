"""Exchange rate endpoints.

Reference data an admin maintains. HR managers may read them - that is how a USD
figure on screen is explained - but not write them: changing a rate moves every
reported figure without touching a single salary, which is a larger power than
editing one person's pay.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access import Role, require_role
from app.db import get_db
from app.deps import current_actor
from app.errors import Conflict, UnprocessableEntity
from app.models import AppUser
from app.schemas import FxRateCreate, FxRateRead, Page
from app.services import fx

router = APIRouter(prefix="/fx-rates", tags=["fx"])

MAX_PAGE_SIZE = 200


@router.get(
    "",
    response_model=Page[FxRateRead],
    dependencies=[Depends(require_role(Role.ADMIN, Role.HR_MANAGER))],
)
def list_fx_rates(
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> Page[FxRateRead]:
    """A page of rates, newest first.

    `size` is capped by the route signature, so an oversized request is a 422 from
    the framework rather than a query that returns ten thousand rows.
    """
    items, total = fx.list_rates(db, currency=currency, page=page, size=size)
    return Page[FxRateRead](
        items=[FxRateRead.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.post(
    "",
    response_model=FxRateRead,
    status_code=status.HTTP_201_CREATED,
    # The HR manager, not the admin. Admin is read-only: it sees the whole
    # organisation and changes none of it. A rate moves every reported figure in
    # its currency without touching a salary, so it belongs to the role that is
    # accountable for those figures.
    dependencies=[Depends(require_role(Role.HR_MANAGER))],
)
def create_fx_rate(
    body: FxRateCreate,
    db: Session = Depends(get_db),
    actor: AppUser = Depends(current_actor),
) -> FxRateRead:
    """Record a rate for one currency on one day.

    Two database constraints can refuse this, and each is translated into something
    the caller can act on rather than surfacing as a generic failure:

      * ux_fx_currency_date - a rate already exists for that currency and day. A 409
        says retrying will not help; the admin meant to correct the existing row.
      * fx_rate_currency_fkey - the currency is not in the reference table. A 422
        naming it reads as "no such currency", which is what happened.

    Caught here rather than pre-checked with a SELECT: a check-then-insert is a race,
    and the index is the authority either way.
    """
    try:
        with db.begin_nested():
            rate = fx.create_rate(
                db,
                currency=body.currency,
                units_per_usd=body.units_per_usd,
                as_of=body.as_of,
                created_by=actor.id,
            )
    except IntegrityError as exc:
        raise _translate(exc, body) from exc

    return FxRateRead.model_validate(rate)


def _translate(exc: IntegrityError, body: FxRateCreate) -> Exception:
    """Turn a constraint name into a sentence, or re-raise what we do not recognise.

    Matching on the constraint name rather than the driver's message text: the name
    is ours and is stable, the message belongs to Postgres and changes between
    versions. Anything unrecognised is left to the unhandled handler, which logs it
    in full and tells the caller nothing - the right outcome for a failure we did
    not anticipate.
    """
    detail = str(exc.orig)

    if "ux_fx_currency_date" in detail:
        return Conflict(f"A rate for {body.currency} on {body.as_of} already exists.")
    if "fx_rate_currency_fkey" in detail:
        return UnprocessableEntity(f"Unknown currency: {body.currency}.")
    return exc
