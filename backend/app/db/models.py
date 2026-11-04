"""SQLModel tables. More tables (Case, Evidence, ...) are added in A3."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class StoreOrder(SQLModel, table=True):
    """A demo-store order, owned by an anonymous buyer session.

    Amounts are decimal strings; the totals here are computed server-side and are what capture
    is checked against.
    """

    id: int | None = Field(default=None, primary_key=True)
    buyer_id: str = Field(index=True)
    paypal_order_id: str = Field(unique=True, index=True)
    status: str = "created"  # created | captured
    currency: str
    amount: str
    items_json: str
    capture_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
