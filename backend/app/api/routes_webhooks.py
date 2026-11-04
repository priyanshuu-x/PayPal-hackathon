"""PayPal webhook receiver: verify the signature, dedupe by event ID, record, acknowledge.

Dispute ingestion (turning CUSTOMER.DISPUTE.* into cases) is added in A9. Until a signature is
verified nothing is stored and nothing is acted on.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api.deps import db_dep, paypal_dep, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import WebhookEvent
from app.paypal.client import PayPalClient
from app.paypal.webhooks import HANDLED_EVENT_TYPES, verify_webhook_signature
from app.security.audit import append_event

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

MAX_BODY_BYTES = 1024 * 1024


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
    db: Annotated[Session, Depends(db_dep)],
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

    append_event(
        db,
        case_id=None,
        actor="paypal",
        action="webhook_received",
        details={"event_id": event_id, "event_type": event_type[:100]},
    )
    db.commit()
    return {"status": "accepted" if event_type in HANDLED_EVENT_TYPES else "ignored"}
