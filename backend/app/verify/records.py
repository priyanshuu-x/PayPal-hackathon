"""Build the verify-facing PayPalRecord from PayPal API models."""

from datetime import UTC, datetime

from app.paypal.models import Order, Tracker
from app.verify.models import PayPalRecord, RecordItem, TrackingStatus


def _parse_time(value: str | None) -> datetime:
    if not value:
        raise ValueError("order has no create_time")
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _latest_tracker(order: Order) -> Tracker | None:
    shipping = order.purchase_units[0].shipping
    trackers = shipping.trackers if shipping else []
    return trackers[-1] if trackers else None


def tracking_status(order: Order) -> TrackingStatus:
    """Map order trackers to our status. Orders v2 trackers carry SHIPPED or CANCELLED; a
    'delivered' status has to come from another source and is passed in by the caller."""
    tracker = _latest_tracker(order)
    if tracker is None:
        return "none"
    status = (tracker.status or "").upper()
    if status == "DELIVERED":
        return "delivered"
    if status == "CANCELLED":
        return "cancelled"
    return "shipped"


def record_from_order(order: Order, *, delivered: bool = False) -> PayPalRecord:
    unit = order.purchase_units[0]
    payer = order.payer
    name_parts = [payer.name.given_name, payer.name.surname] if payer and payer.name else []
    address = unit.shipping.address if unit.shipping else None
    address_parts = (
        [
            address.address_line_1,
            address.address_line_2,
            address.admin_area_2,
            address.admin_area_1,
            address.postal_code,
            address.country_code,
        ]
        if address
        else []
    )
    tracker = _latest_tracker(order)
    status = "delivered" if delivered else tracking_status(order)
    return PayPalRecord(
        order_id=order.id,
        capture_ids=tuple(c.id for c in order.captures),
        currency=unit.amount.currency_code,
        total=unit.amount.value,
        created_at=_parse_time(order.create_time),
        items=tuple(RecordItem(i.name, i.quantity, i.unit_amount.value) for i in unit.items),
        payer_name=" ".join(p for p in name_parts if p) or None,
        payer_email=payer.email_address if payer else None,
        shipping_address=", ".join(p for p in address_parts if p) or None,
        tracking_status=status,
        tracking_number=tracker.tracking_number if tracker else None,
    )
