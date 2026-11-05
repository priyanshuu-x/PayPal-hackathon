"""Server-side catalog for the fictional demo store. The browser never sets a price."""

from decimal import Decimal

from pydantic import BaseModel

CURRENCY = "USD"
SHIPPING_FLAT = Decimal("4.99")
MAX_LINES = 10
MAX_QUANTITY = 10


class Product(BaseModel):
    id: str
    name: str
    price: Decimal
    currency: str = CURRENCY
    image: str


_PRODUCTS = [
    Product(
        id="lotus-tote",
        name="Lotus Linen Tote",
        price=Decimal("24.00"),
        image="images/lotus-tote.png",
    ),
    Product(
        id="loom-throw",
        name="Hand-Loomed Throw",
        price=Decimal("68.50"),
        image="images/loom-throw.png",
    ),
    Product(
        id="indigo-mug",
        name="Indigo Stoneware Mug",
        price=Decimal("16.25"),
        image="images/indigo-mug.png",
    ),
    Product(
        id="cedar-candle",
        name="Cedar & Fig Candle",
        price=Decimal("21.00"),
        image="images/cedar-candle.png",
    ),
    Product(
        id="silk-scarf",
        name="Woven Silk Scarf",
        price=Decimal("39.99"),
        image="images/silk-scarf.png",
    ),
    Product(
        id="brass-tray",
        name="Brass Serving Tray",
        price=Decimal("52.00"),
        image="images/brass-tray.png",
    ),
]

CATALOG: dict[str, Product] = {p.id: p for p in _PRODUCTS}


def list_products() -> list[Product]:
    return list(_PRODUCTS)
