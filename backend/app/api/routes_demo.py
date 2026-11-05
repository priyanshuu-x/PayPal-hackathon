"""Demo helper, registered only when DEMO_MODE=true (never by default).

The sandbox cannot open a dispute through the API: a buyer has to open it in PayPal's UI. For
demos and recordings this creates a *demo* dispute case for a paid demo-store order, going
through the same analysis and the same approve handler as a real dispute. It needs a merchant
session and CSRF, is clearly marked (`DEMO-` dispute ID), and approving it sends nothing to
PayPal. It does not bypass webhook signature verification: real webhooks are still verified.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.api.deps import DEMO_MERCHANT_ID, db_dep, dispatcher_dep, merchant_csrf_dep
from app.api.errors import ApiError
from app.db.models import Case, StoreOrder
from app.security.audit import append_event
from app.security.auth import MerchantSession
from app.workflows.dispatch import Dispatcher
from app.workflows.pipeline import NOT_RECEIVED

router = APIRouter(prefix="/api/demo", tags=["demo"])

DEMO_PREFIX = "DEMO-"


class DemoDisputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=64)


@router.post("/disputes", status_code=201)
async def create_demo_dispute(
    body: DemoDisputeIn,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    dispatcher: Annotated[Dispatcher, Depends(dispatcher_dep)],
    background: BackgroundTasks,
) -> dict[str, str]:
    order = db.exec(
        select(StoreOrder).where(
            StoreOrder.paypal_order_id == body.order_id, StoreOrder.status == "captured"
        )
    ).first()
    if order is None or not order.capture_id:
        raise ApiError(404, "not_found", "No paid demo-store order with this ID.")
    open_demo = db.exec(
        select(Case).where(
            Case.paypal_order_id == order.paypal_order_id,
            Case.source == "dispute",
            Case.status.in_(("new", "processing", "ready", "approved")),  # type: ignore[attr-defined]
        )
    ).first()
    if open_demo is not None:
        raise ApiError(409, "conflict", "A dispute case for this order is already open.")

    case = Case(
        id=str(uuid.uuid4()),
        source="dispute",
        merchant_id=DEMO_MERCHANT_ID,
        dispute_id=f"{DEMO_PREFIX}{uuid.uuid4().hex[:8].upper()}",
        capture_id=order.capture_id,
        paypal_order_id=order.paypal_order_id,
        amount=order.amount,
        currency=order.currency,
        dispute_reason=NOT_RECEIVED,
        dispute_status="WAITING_FOR_SELLER_RESPONSE",
        dispute_due=(datetime.now(UTC) + timedelta(days=10)).date().isoformat(),
    )
    db.add(case)
    append_event(
        db,
        case_id=case.id,
        actor=f"merchant:{session.merchant_id}",
        action="case_created",
        details={"source": "dispute", "demo": True, "dispute_id": case.dispute_id},
    )
    db.commit()
    await dispatcher.enqueue(case.id, background)
    return {"case_id": case.id, "dispute_id": str(case.dispute_id)}
