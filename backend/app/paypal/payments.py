"""Payments v2 refunds.

MONEY MOVES HERE. The only caller allowed is the `POST /cases/{id}/approve` handler (CLAUDE.md
rule 4). `request_id` is mandatory so a replayed approval returns the original refund.
"""

from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.paypal.client import PayPalClient
from app.paypal.models import Refund
from app.paypal.orders import money


async def refund_capture(
    client: PayPalClient,
    capture_id: str,
    *,
    request_id: str,
    amount: Decimal | None = None,
    currency: str | None = None,
    note: str | None = None,
) -> Refund:
    """Full refund when `amount` is None, otherwise a partial refund (needs `currency`)."""
    if not request_id:
        raise ValueError("request_id is required for refunds")
    body: dict[str, Any] = {}
    if amount is not None:
        if not currency:
            raise ValueError("currency is required for a partial refund")
        body["amount"] = {"currency_code": currency, "value": money(amount)}
    if note:
        body["note_to_payer"] = note[:255]
    response = await client.request(
        "POST",
        f"/v2/payments/captures/{quote(capture_id, safe='')}/refund",
        json=body,
        request_id=request_id,
    )
    return Refund.model_validate(response)
