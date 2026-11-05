"""Flow B: turn a PayPal dispute into a case.

Only the dispute ID from the webhook is used; everything else is fetched from PayPal with our
own credentials, so nothing in a webhook body is trusted beyond its signature. Buyer-written
dispute messages are never read.
"""

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.api.deps import DEMO_MERCHANT_ID
from app.db.models import Case
from app.paypal.client import PayPalClient
from app.paypal.disputes import get_dispute, order_id_for_capture
from app.paypal.errors import PayPalError
from app.security.audit import append_event

logger = logging.getLogger(__name__)

DISPUTE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def valid_dispute_id(value: object) -> str | None:
    return value if isinstance(value, str) and DISPUTE_ID_RE.fullmatch(value) else None


async def ingest_dispute(
    db: Session, paypal: PayPalClient, dispute_id: str, *, event_type: str
) -> tuple[str, bool]:
    """Create the case for a dispute, or update the existing one. Returns (case_id, is_new).

    Raises PayPalError when the dispute cannot be fetched: the caller must not acknowledge the
    webhook, so PayPal sends it again. The caller commits."""
    dispute = await get_dispute(paypal, dispute_id)
    existing = db.exec(select(Case).where(Case.dispute_id == dispute.dispute_id)).first()
    if existing is not None:
        existing.dispute_status = dispute.status
        existing.dispute_due = dispute.seller_response_due_date
        existing.updated_at = datetime.now(UTC)
        db.add(existing)
        append_event(
            db,
            case_id=existing.id,
            actor="paypal",
            action="dispute_updated",
            details={"event_type": event_type, "status": dispute.status},
        )
        return existing.id, False

    capture_id = dispute.capture_id
    order_id: str | None = None
    if capture_id:
        try:
            order_id = await order_id_for_capture(paypal, capture_id)
        except PayPalError:
            logger.warning("dispute_order_lookup_failed", extra={"dispute_id": dispute.dispute_id})
    gross = dispute.disputed_transactions[0].gross_amount if dispute.disputed_transactions else None
    amount = dispute.dispute_amount or gross
    case = Case(
        id=str(uuid.uuid4()),
        source="dispute",
        merchant_id=DEMO_MERCHANT_ID,
        dispute_id=dispute.dispute_id,
        capture_id=capture_id,
        paypal_order_id=order_id,
        amount=str(amount.value) if amount else None,
        currency=amount.currency_code if amount else None,
        dispute_reason=dispute.reason,
        dispute_status=dispute.status,
        dispute_due=dispute.seller_response_due_date,
    )
    db.add(case)
    append_event(
        db,
        case_id=case.id,
        actor="paypal",
        action="case_created",
        details={"source": "dispute", "dispute_id": dispute.dispute_id, "reason": dispute.reason},
    )
    return case.id, True
