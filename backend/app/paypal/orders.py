"""Orders v2: create, fetch by ID, capture. Look orders up by ID, never by transaction search."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from app.paypal.client import PayPalClient
from app.paypal.models import Order

CENT = Decimal("0.01")


def money(value: Decimal) -> str:
    """PayPal wants a decimal string with two places."""
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


class LineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku: str
    name: str
    quantity: int
    unit_price: Decimal


class ShippingAddress(BaseModel):
    full_name: str
    address_line_1: str
    address_line_2: str | None = None
    admin_area_2: str  # city
    admin_area_1: str  # state or province
    postal_code: str
    country_code: str


def build_order_payload(
    *,
    items: list[LineItem],
    shipping_cost: Decimal,
    currency: str,
    shipping: ShippingAddress,
    description: str,
) -> dict[str, Any]:
    """Order body with items[], amount.breakdown and shipping (needed to compare receipts later)."""
    item_total = sum((i.unit_price * i.quantity for i in items), Decimal("0"))
    total = item_total + shipping_cost
    address = {
        "address_line_1": shipping.address_line_1,
        "admin_area_2": shipping.admin_area_2,
        "admin_area_1": shipping.admin_area_1,
        "postal_code": shipping.postal_code,
        "country_code": shipping.country_code,
    }
    if shipping.address_line_2:
        address["address_line_2"] = shipping.address_line_2
    return {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "default",
                "description": description,
                "amount": {
                    "currency_code": currency,
                    "value": money(total),
                    "breakdown": {
                        "item_total": {"currency_code": currency, "value": money(item_total)},
                        "shipping": {"currency_code": currency, "value": money(shipping_cost)},
                    },
                },
                "items": [
                    {
                        "name": i.name,
                        "sku": i.sku,
                        "quantity": str(i.quantity),
                        "category": "PHYSICAL_GOODS",
                        "unit_amount": {"currency_code": currency, "value": money(i.unit_price)},
                    }
                    for i in items
                ],
                "shipping": {
                    "type": "SHIPPING",
                    "name": {"full_name": shipping.full_name},
                    "address": address,
                },
            }
        ],
    }


def _id(order_id: str) -> str:
    return quote(order_id, safe="")


# PayPal answers create and capture with only {id, status, links} unless asked for the full order.
FULL_RESPONSE = {"Prefer": "return=representation"}


async def create_order(client: PayPalClient, payload: dict[str, Any], request_id: str) -> Order:
    body = await client.request(
        "POST",
        "/v2/checkout/orders",
        json=payload,
        request_id=request_id,
        extra_headers=FULL_RESPONSE,
    )
    return Order.model_validate(body)


async def get_order(client: PayPalClient, order_id: str) -> Order:
    body = await client.request("GET", f"/v2/checkout/orders/{_id(order_id)}")
    return Order.model_validate(body)


async def capture_order(client: PayPalClient, order_id: str, request_id: str) -> Order:
    body = await client.request(
        "POST",
        f"/v2/checkout/orders/{_id(order_id)}/capture",
        json={},
        request_id=request_id,
        extra_headers=FULL_RESPONSE,
    )
    return Order.model_validate(body)
