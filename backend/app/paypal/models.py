"""Pydantic models for the PayPal fields we read. Unknown fields are ignored."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Money(_Model):
    currency_code: str
    value: Decimal


class OrderItem(_Model):
    name: str
    quantity: int
    unit_amount: Money
    sku: str | None = None


class Capture(_Model):
    id: str
    status: str
    amount: Money
    create_time: str | None = None


class Payments(_Model):
    captures: list[Capture] = []


class Tracker(_Model):
    id: str | None = None
    status: str | None = None
    tracking_number: str | None = None
    carrier: str | None = None


class ShippingName(_Model):
    full_name: str | None = None


class Address(_Model):
    address_line_1: str | None = None
    address_line_2: str | None = None
    admin_area_2: str | None = None  # city
    admin_area_1: str | None = None  # state or province
    postal_code: str | None = None
    country_code: str | None = None


class Shipping(_Model):
    name: ShippingName | None = None
    address: Address | None = None
    trackers: list[Tracker] = []


class PurchaseUnit(_Model):
    reference_id: str | None = None
    amount: Money
    items: list[OrderItem] = []
    shipping: Shipping | None = None
    payments: Payments | None = None


class PayerName(_Model):
    given_name: str | None = None
    surname: str | None = None


class Payer(_Model):
    name: PayerName | None = None
    email_address: str | None = None


class Order(_Model):
    id: str
    status: str
    create_time: str | None = None
    purchase_units: list[PurchaseUnit]
    payer: Payer | None = None

    @property
    def total(self) -> Money:
        return self.purchase_units[0].amount

    @property
    def captures(self) -> list[Capture]:
        payments = self.purchase_units[0].payments
        return payments.captures if payments else []


class DisputedTransaction(_Model):
    seller_transaction_id: str | None = None  # the capture ID
    create_time: str | None = None
    gross_amount: Money | None = None


class Dispute(_Model):
    """Structured dispute fields only. Buyer-written `messages` are deliberately not modelled:
    they are hostile text and nothing here should ever read them (CLAUDE.md rule 5)."""

    dispute_id: str
    reason: str | None = None  # PayPal enum, for example MERCHANDISE_OR_SERVICE_NOT_RECEIVED
    status: str | None = None
    dispute_life_cycle_stage: str | None = None
    dispute_amount: Money | None = None
    seller_response_due_date: str | None = None
    disputed_transactions: list[DisputedTransaction] = []

    @property
    def capture_id(self) -> str | None:
        first = self.disputed_transactions[0] if self.disputed_transactions else None
        return first.seller_transaction_id if first else None


class Refund(_Model):
    id: str
    status: str
    amount: Money | None = None
