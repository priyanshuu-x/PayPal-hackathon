"""A receipt image for one paid demo-store order, for recording the demo video.

    python eval/demo_receipt.py --order-id <PayPal order ID> --variant genuine
    python eval/demo_receipt.py --order-id <PayPal order ID> --variant tampered
    python eval/demo_receipt.py --order-id <PayPal order ID> --variant injection

The refund form only accepts orders paid from the same browser, so the eval's receipts (built
from seeded orders) cannot be used on camera. This renders one from a captured order in the
`StoreOrder` table. It is not a general receipt generator: it takes only an order ID that exists
in our own database, and always prints the fictional store's name (plan.md §10.3).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

import _paths

# isort: split

from app import store_catalog as catalog
from app.config import get_settings
from app.db.models import StoreOrder
from app.db.session import create_db_and_tables, make_engine
from sqlmodel import Session, select

import receipts
from dataset import Printed, PrintedItem
from generate_cases import INJECTIONS, STORE, _different_id

OUT_DIR = _paths.DATASETS / "demo"
VARIANTS = ("genuine", "tampered", "injection")
TAMPER_AMOUNT = Decimal("30.00")


def printed_for(order: StoreOrder, variant: str, rng: random.Random) -> Printed:
    items = [
        PrintedItem(i["name"], int(i["quantity"]), i["unit_price"])
        for i in json.loads(order.items_json)
    ]
    printed = Printed(
        merchant=STORE,
        order_id=order.paypal_order_id,
        order_date=order.created_at.date().isoformat(),
        currency=order.currency,
        items=items,
        shipping=str(catalog.SHIPPING_FLAT),
        total=order.amount,
        payer_name=None,
        ship_to=None,
    )
    if variant == "tampered":
        printed.total = f"{Decimal(order.amount) + TAMPER_AMOUNT:.2f}"
        printed.order_id = _different_id(order.paypal_order_id, rng)
    elif variant == "injection":
        printed.injection = INJECTIONS[0]
    return printed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--variant", choices=VARIANTS, default="genuine")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    engine = make_engine(get_settings().database_url)
    create_db_and_tables(engine)
    try:
        with Session(engine) as db:
            order = db.exec(
                select(StoreOrder).where(StoreOrder.paypal_order_id == args.order_id)
            ).first()
    finally:
        engine.dispose()
    if order is None or order.status != "captured":
        print(f"No paid order {args.order_id} in this database. Use a paid demo-store order.")
        return 2

    rng = random.Random(args.order_id)  # noqa: S311  (test data, not security)
    image = receipts.render(printed_for(order, args.variant, rng), "paper", "us")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"{args.order_id}-{args.variant}.png"
    path.write_bytes(receipts.to_png(image))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
