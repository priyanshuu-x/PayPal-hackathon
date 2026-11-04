"""Demo-store checkout for PayPal JS SDK v6. Buyer-initiated create and capture.

Prices are computed here from the server-side catalog. The browser sends product IDs and
quantities only, and unknown fields are rejected, so a tampered price cannot reach PayPal.
"""

import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select

from app import store_catalog as catalog
from app.api.deps import buyer_dep, db_dep, paypal_dep, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import StoreOrder
from app.paypal import orders as paypal_orders
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import LineItem, ShippingAddress, money

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/store", tags=["store"])

SDK_TOKEN_REUSE_SECONDS = 600  # PayPal client tokens last about 15 minutes


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ItemIn(_Strict):
    product_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=catalog.MAX_QUANTITY)


class ShippingIn(_Strict):
    name: str = Field(min_length=1, max_length=100)
    address_line_1: str = Field(min_length=1, max_length=100)
    address_line_2: str | None = Field(default=None, max_length=100)
    admin_area_2: str = Field(min_length=1, max_length=60)
    admin_area_1: str = Field(min_length=1, max_length=60)
    postal_code: str = Field(min_length=1, max_length=20)
    country_code: str = Field(pattern=r"^[A-Za-z]{2}$")

    @field_validator("country_code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class CreateOrderIn(_Strict):
    items: list[ItemIn] = Field(min_length=1, max_length=catalog.MAX_LINES)
    shipping: ShippingIn


def _upstream(exc: PayPalError) -> ApiError:
    logger.error("paypal_failure", extra={"debug_id": exc.debug_id, "paypal_error": exc.name})
    return ApiError(502, "upstream_error", "The payment provider could not complete this request.")


@router.get("/products")
async def list_products() -> list[catalog.Product]:
    return catalog.list_products()


@router.get("/sdk-config")
async def sdk_config(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
) -> dict[str, str]:
    """Public values for `paypal.createInstance`: the sandbox client ID and a client token."""
    cached: tuple[str, float] | None = getattr(request.app.state, "sdk_token", None)
    if cached is None or time.monotonic() >= cached[1]:
        try:
            token = await paypal.client_token(settings.sdk_domain_list or None)
        except PayPalError as exc:
            raise _upstream(exc) from exc
        cached = (token, time.monotonic() + SDK_TOKEN_REUSE_SECONDS)
        request.app.state.sdk_token = cached
    return {"client_id": settings.paypal_client_id, "client_token": cached[0]}


@router.post("/orders")
async def create_order(
    body: CreateOrderIn,
    buyer_id: Annotated[str, Depends(buyer_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
    db: Annotated[Session, Depends(db_dep)],
) -> dict[str, str]:
    quantities: dict[str, int] = {}
    for item in body.items:
        if item.product_id not in catalog.CATALOG:
            raise ApiError(422, "validation_error", "Invalid request: items.product_id")
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    if any(q > catalog.MAX_QUANTITY for q in quantities.values()):
        raise ApiError(422, "validation_error", "Invalid request: items.quantity")

    lines = [
        LineItem(
            sku=pid,
            name=catalog.CATALOG[pid].name,
            quantity=qty,
            unit_price=catalog.CATALOG[pid].price,
        )
        for pid, qty in quantities.items()
    ]
    total = sum((line.unit_price * line.quantity for line in lines), Decimal("0"))
    total += catalog.SHIPPING_FLAT

    payload = paypal_orders.build_order_payload(
        items=lines,
        shipping_cost=catalog.SHIPPING_FLAT,
        currency=catalog.CURRENCY,
        shipping=ShippingAddress(
            full_name=body.shipping.name,
            **body.shipping.model_dump(exclude={"name"}),
        ),
        description="Lotus & Loom Demo Store order",
    )
    try:
        order = await paypal_orders.create_order(
            paypal, payload, request_id=f"create:{uuid.uuid4()}"
        )
    except PayPalError as exc:
        raise _upstream(exc) from exc

    if order.total.value != total or order.total.currency_code != catalog.CURRENCY:
        logger.error("paypal_total_differs", extra={"order_id": order.id})
        raise ApiError(
            502, "upstream_error", "The payment provider could not complete this request."
        )

    db.add(
        StoreOrder(
            buyer_id=buyer_id,
            paypal_order_id=order.id,
            currency=catalog.CURRENCY,
            amount=money(total),
            items_json=json.dumps(
                [
                    {"name": ln.name, "quantity": ln.quantity, "unit_price": money(ln.unit_price)}
                    for ln in lines
                ]
            ),
        )
    )
    db.commit()
    return {"order_id": order.id, "amount": money(total), "currency": catalog.CURRENCY}


@router.post("/orders/{order_id}/capture")
async def capture_order(
    order_id: str,
    buyer_id: Annotated[str, Depends(buyer_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
    db: Annotated[Session, Depends(db_dep)],
) -> dict[str, str]:
    row = db.exec(
        select(StoreOrder).where(
            StoreOrder.paypal_order_id == order_id, StoreOrder.buyer_id == buyer_id
        )
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "Order not found.")

    expected = Decimal(row.amount)
    if row.status == "captured" and row.capture_id:
        return _captured(row)

    try:
        current = await paypal_orders.get_order(paypal, order_id)
        if current.total.value != expected or current.total.currency_code != row.currency:
            raise ApiError(409, "conflict", "Order total does not match.")
        if current.status != "APPROVED":
            raise ApiError(409, "conflict", "Order has not been approved by the buyer.")
        done = await paypal_orders.capture_order(paypal, order_id, request_id=f"capture:{order_id}")
    except PayPalError as exc:
        raise _upstream(exc) from exc

    captures = done.captures
    if (
        not captures
        or captures[0].amount.value != expected
        or captures[0].amount.currency_code != row.currency
    ):
        logger.error("capture_mismatch", extra={"order_id": order_id})
        raise ApiError(
            502, "upstream_error", "The payment provider could not complete this request."
        )

    row.status = "captured"
    row.capture_id = captures[0].id
    db.add(row)
    db.commit()
    return _captured(row)


def _captured(row: StoreOrder) -> dict[str, str]:
    return {
        "order_id": row.paypal_order_id,
        "capture_id": row.capture_id or "",
        "status": "COMPLETED",
        "amount": row.amount,
        "currency": row.currency,
    }


@router.get("/orders")
async def list_my_orders(
    buyer_id: Annotated[str, Depends(buyer_dep)],
    db: Annotated[Session, Depends(db_dep)],
) -> list[dict[str, Any]]:
    rows = db.exec(
        select(StoreOrder)
        .where(StoreOrder.buyer_id == buyer_id)
        .order_by(StoreOrder.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [
        {
            "order_id": r.paypal_order_id,
            "capture_id": r.capture_id,
            "status": r.status,
            "amount": r.amount,
            "currency": r.currency,
            "created_at": r.created_at.isoformat().replace("+00:00", "Z"),
            "items": json.loads(r.items_json),
        }
        for r in rows
    ]
