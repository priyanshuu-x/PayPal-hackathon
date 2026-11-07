"""A receipt image for a paid demo-store order, typed in by hand (no database needed).

    python scripts/demo_receipt_manual.py --order-id 4NX47673NT925973K --date 2026-11-07 \
        --item "Lotus Linen Tote:1:24.00" --variant genuine

Use it for the demo video when the order lives in the hosted database (eval/demo_receipt.py reads
a local one). `--item` is `name:quantity:unit_price` and can be repeated. Variants: genuine,
tampered (different order ID, total + 30.00) and injection (hidden instruction text). Output goes
to eval/datasets/demo/, which is git-ignored. It only ever prints the fictional store's name
(plan.md section 10.3); it is not a general receipt generator.
"""

from __future__ import annotations

import argparse
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import _paths  # noqa: E402,F401  (puts backend/ on sys.path)

# isort: split

import receipts  # noqa: E402
from app import store_catalog as catalog  # noqa: E402
from dataset import Printed, PrintedItem  # noqa: E402
from generate_cases import INJECTIONS, STORE, _different_id  # noqa: E402

OUT_DIR = ROOT / "eval" / "datasets" / "demo"


def parse_item(text: str) -> PrintedItem:
    name, quantity, price = text.rsplit(":", 2)
    return PrintedItem(name, int(quantity), f"{Decimal(price):.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, as on the order")
    parser.add_argument("--item", action="append", required=True, help="name:qty:unit_price")
    parser.add_argument(
        "--variant", choices=("genuine", "tampered", "injection"), default="genuine"
    )
    args = parser.parse_args(argv)

    items = [parse_item(i) for i in args.item]
    subtotal = sum((Decimal(i.unit_price) * i.quantity for i in items), Decimal("0"))
    total = subtotal + catalog.SHIPPING_FLAT
    printed = Printed(
        merchant=STORE,
        order_id=args.order_id,
        order_date=args.date,
        currency=catalog.CURRENCY,
        items=items,
        shipping=str(catalog.SHIPPING_FLAT),
        total=f"{total:.2f}",
        payer_name=None,
        ship_to=None,
    )
    if args.variant == "tampered":
        printed.total = f"{total + Decimal('30.00'):.2f}"
        printed.order_id = _different_id(args.order_id, random.Random(args.order_id))  # noqa: S311
    elif args.variant == "injection":
        printed.injection = INJECTIONS[0]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{args.order_id}-{args.variant}.png"
    path.write_bytes(receipts.to_png(receipts.render(printed, "paper", "us")))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
