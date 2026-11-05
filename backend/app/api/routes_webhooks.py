"""PayPal webhook receiver: verify the signature, dedupe by event ID, record, acknowledge.

CUSTOMER.DISPUTE.* events create or update a dispute case (Flow B). Until a signature is
verified nothing is stored and nothing is acted on. If the dispute cannot be fetched from PayPal
the event is NOT recorded and a 502 is returned, so PayPal delivers it again.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api.deps import db_dep, dispatcher_dep, paypal_dep, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import WebhookEvent
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.webhooks import HANDLED_EVENT_TYPES, verify_webhook_signature
from app.security.audit import append_event
from app.workflows.dispatch import Dispatcher
from app.workflows.disputes import ingest_dispute, valid_dispute_id

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

MAX_BODY_BYTES = 1024 * 1024


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
    db: Annotated[Session, Depends(db_dep)],
    dispatcher: Annotated[Dispatcher, Depends(dispatcher_dep)],
    background: BackgroundTasks,
) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ApiError(400, "validation_error", "Webhook rejected.")
    try:
        event: Any = json.loads(raw)
    except ValueError:
        raise ApiError(400, "validation_error", "Webhook rejected.") from None
    if not isinstance(event, dict):
        raise ApiError(400, "validation_error", "Webhook rejected.")

    verified = await verify_webhook_signature(
        paypal,
        webhook_id=settings.paypal_webhook_id,
        headers=request.headers,
        event=event,
    )
    if not verified:
        raise ApiError(400, "validation_error", "Webhook verification failed.")

    event_id = str(event.get("id") or "")
    event_type = str(event.get("event_type") or "")
    if not event_id or len(event_id) > 100:
        raise ApiError(400, "validation_error", "Webhook rejected.")

    try:
        db.add(WebhookEvent(event_id=event_id, event_type=event_type[:100]))
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate"}

    new_case_id: str | None = None
    if event_type in HANDLED_EVENT_TYPES:
        resource = event.get("resource")
        dispute_id = valid_dispute_id(
            resource.get("dispute_id") if isinstance(resource, dict) else None
        )
        if dispute_id is None:
            db.rollback()
            raise ApiError(400, "validation_error", "Webhook rejected.")
        try:
            case_id, is_new = await ingest_dispute(db, paypal, dispute_id, event_type=event_type)
        except PayPalError as exc:
            db.rollback()  # not recorded, so PayPal's retry is processed
            raise ApiError(
                502, "upstream_error", "The dispute could not be processed yet."
            ) from exc
        new_case_id = case_id if is_new else None

    append_event(
        db,
        case_id=None,
        actor="paypal",
        action="webhook_received",
        details={"event_id": event_id, "event_type": event_type[:100]},
    )
    db.commit()
    if new_case_id:
        await dispatcher.enqueue(new_case_id, background)
    return {"status": "accepted" if event_type in HANDLED_EVENT_TYPES else "ignored"}
