"""Webhook signature verification through PayPal's verify-webhook-signature API (rule 7)."""

import logging
from collections.abc import Mapping
from typing import Any

from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError

logger = logging.getLogger(__name__)

HANDLED_EVENT_TYPES = frozenset(
    {"CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"}
)

_HEADER_TO_FIELD = {
    "paypal-auth-algo": "auth_algo",
    "paypal-cert-url": "cert_url",
    "paypal-transmission-id": "transmission_id",
    "paypal-transmission-sig": "transmission_sig",
    "paypal-transmission-time": "transmission_time",
}


async def verify_webhook_signature(
    client: PayPalClient,
    *,
    webhook_id: str,
    headers: Mapping[str, str],
    event: dict[str, Any],
) -> bool:
    """True only when PayPal answers SUCCESS. Missing headers, an unset webhook ID and any
    PayPal failure all count as unverified."""
    if not webhook_id:
        return False
    body: dict[str, Any] = {"webhook_id": webhook_id, "webhook_event": event}
    for header, field in _HEADER_TO_FIELD.items():
        value = headers.get(header)
        if not value:
            return False
        body[field] = value
    try:
        response = await client.request(
            "POST", "/v1/notifications/verify-webhook-signature", json=body
        )
    except PayPalError as exc:
        logger.warning("webhook_verify_failed", extra={"paypal_debug_id": exc.debug_id})
        return False
    return response.get("verification_status") == "SUCCESS"
