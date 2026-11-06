import json
import random
from pathlib import Path

import pytest
from app.config import get_settings
from app.db.models import StoreOrder
from app.db.session import create_db_and_tables, make_engine
from PIL import Image
from sqlmodel import Session

import demo_receipt
from generate_cases import STORE

ITEMS = [{"name": "Indigo Stoneware Mug", "quantity": 1, "unit_price": "16.25"}]


def order(status: str = "captured") -> StoreOrder:
    return StoreOrder(
        buyer_id="browser",
        paypal_order_id="ORDER12345678ABCD",
        status=status,
        currency="USD",
        amount="21.24",
        items_json=json.dumps(ITEMS),
    )


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def add(url: str, row: StoreOrder) -> None:
    engine = make_engine(url)
    create_db_and_tables(engine)
    with Session(engine) as db:
        db.add(row)
        db.commit()
    engine.dispose()


def test_genuine_prints_the_order_as_recorded() -> None:
    printed = demo_receipt.printed_for(order(), "genuine", random.Random(1))  # noqa: S311
    assert (printed.order_id, printed.total, printed.merchant) == (
        "ORDER12345678ABCD",
        "21.24",
        STORE,
    )
    assert printed.injection is None


def test_tampered_changes_the_amount_and_the_order_id() -> None:
    printed = demo_receipt.printed_for(order(), "tampered", random.Random(1))  # noqa: S311
    assert printed.total == "51.24"
    assert sum(a != b for a, b in zip(printed.order_id, "ORDER12345678ABCD", strict=True)) >= 2


def test_injection_keeps_the_order_details_and_adds_the_text() -> None:
    printed = demo_receipt.printed_for(order(), "injection", random.Random(1))  # noqa: S311
    assert printed.total == "21.24"
    assert printed.injection


def test_writes_an_image_for_a_paid_order(database: str, tmp_path: Path) -> None:
    add(database, order())
    code = demo_receipt.main(
        ["--order-id", "ORDER12345678ABCD", "--variant", "injection", "--out-dir", str(tmp_path)]
    )
    assert code == 0
    with Image.open(tmp_path / "ORDER12345678ABCD-injection.png") as img:
        assert img.width > 300


@pytest.mark.parametrize("status", ["created", None])
def test_refuses_unpaid_or_unknown_orders(
    database: str, tmp_path: Path, status: str | None
) -> None:
    if status:
        add(database, order(status))
    assert demo_receipt.main(["--order-id", "ORDER12345678ABCD", "--out-dir", str(tmp_path)]) == 2
    assert list(tmp_path.glob("*.png")) == []
