"""Shipment tracking on an order. Seeding uses `add_tracking`; checks read `Order` trackers."""

from typing import Any
from urllib.parse import quote

from app.paypal.client import PayPalClient


async def add_tracking(
    client: PayPalClient,
    order_id: str,
    *,
    capture_id: str,
    tracking_number: str,
    carrier: str,
    request_id: str,
    notify_payer: bool = False,
) -> dict[str, Any]:
    """POST /v2/checkout/orders/{id}/track. Used when seeding sandbox orders."""
    return await client.request(
        "POST",
        f"/v2/checkout/orders/{quote(order_id, safe='')}/track",
        json={
            "capture_id": capture_id,
            "tracking_number": tracking_number,
            "carrier": carrier,
            "notify_payer": notify_payer,
        },
        request_id=request_id,
    )
