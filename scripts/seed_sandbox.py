"""Seed the PayPal sandbox with demo-store orders: items, shipping, capture and tracking.

    make seed                                   # 24 orders (python scripts/seed_sandbox.py)
    python scripts/seed_sandbox.py --count 30
    python scripts/seed_sandbox.py --resume     # carry on after an interruption

Sandbox only (the backend's settings refuse any other PayPal base URL). Follows the flow in
paypaldev/getting-started-with-paypal-sandbox: create an order, approve it as the sandbox
buyer, capture it as the merchant. PayPal has no documented way to capture a buyer-funded
order without that approval, so the script:

1. creates the orders through `app.paypal.orders` (same payload as the store checkout),
2. prints one approval link per order: open each, log in as the sandbox **personal** (buyer)
   account and approve,
3. polls until each order is approved, then captures it and adds tracking.

Orders are written to `eval/datasets/sandbox_orders.json` (the eval generator's input) and to
the `StoreOrder` table, owned by a fixed seed buyer, so demo disputes can use them. Re-running
with the same `--seed` reuses PayPal request IDs, so nothing is created or captured twice.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import _backend  # noqa: F401  (puts backend/ on sys.path)

# isort: split

from app import store_catalog as catalog
from app.config import Settings, get_settings
from app.db.models import StoreOrder
from app.db.session import create_db_and_tables, make_engine
from app.paypal import orders as paypal_orders
from app.paypal import tracking as paypal_tracking
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import LineItem, ShippingAddress, money
from sqlmodel import Session, select

DEFAULT_OUT = _backend.REPO_ROOT / "eval" / "datasets" / "sandbox_orders.json"
SEED_BUYER_ID = "seed-buyer"  # StoreOrder.buyer_id for seeded orders (no browser holds it)
APPROVE_URL = "https://www.sandbox.paypal.com/checkoutnow?token={order_id}"
TRACKED_SHARE = 0.75  # the rest stay untracked, for "not received" cases
CARRIERS = ["USPS", "UPS", "FEDEX", "DHL"]

# Fictional people and places only (CLAUDE.md: no real personal data).
NAMES = [
    "Asha Verma",
    "Leo Marsh",
    "Mina Okafor",
    "Tomas Reyes",
    "Priya Nair",
    "Hana Sato",
    "Owen Clarke",
    "Zara Haddad",
    "Ravi Menon",
    "Elena Popova",
    "Kofi Mensah",
    "Ines Duarte",
]
STREETS = ["Lotus Lane", "Loom Street", "Indigo Avenue", "Cedar Court", "Willow Way", "Harbor Road"]
CITIES = [
    ("San Jose", "CA", "95131"),
    ("Austin", "TX", "73301"),
    ("Portland", "OR", "97201"),
    ("Denver", "CO", "80202"),
    ("Columbus", "OH", "43004"),
    ("Raleigh", "NC", "27601"),
]


@dataclass
class PlannedOrder:
    index: int
    items: list[dict[str, Any]]  # {product_id, quantity}
    shipping: dict[str, str]
    tracking: dict[str, str] | None  # {carrier, tracking_number} or None


@dataclass
class SeededOrder:
    index: int
    order_id: str
    status: str  # created | captured | tracked
    amount: str
    currency: str
    items: list[dict[str, Any]]  # {name, quantity, unit_price}
    shipping: dict[str, str]
    planned_tracking: dict[str, str] | None
    capture_id: str | None = None
    tracking: dict[str, str] | None = None
    payer_name: str | None = None
    create_time: str | None = None
    capture_time: str | None = None
    notes: list[str] = field(default_factory=list)


def plan_orders(count: int, seed: int) -> list[PlannedOrder]:
    """Deterministic order mix: 1 to 3 products, quantities 1 to 3, fictional addresses."""
    rng = random.Random(seed)  # noqa: S311  (test data, not security)
    product_ids = sorted(catalog.CATALOG)
    plans = []
    for index in range(count):
        chosen = rng.sample(product_ids, k=rng.randint(1, 3))
        items = [{"product_id": pid, "quantity": rng.randint(1, 3)} for pid in chosen]
        city, state, postal = rng.choice(CITIES)
        shipping = {
            "name": rng.choice(NAMES),
            "address_line_1": f"{rng.randint(10, 999)} {rng.choice(STREETS)}",
            "admin_area_2": city,
            "admin_area_1": state,
            "postal_code": postal,
            "country_code": "US",
        }
        tracking = None
        if rng.random() < TRACKED_SHARE:
            tracking = {
                "carrier": rng.choice(CARRIERS),
                "tracking_number": f"LL{seed:02d}{index:03d}{rng.randint(10**7, 10**8 - 1)}",
            }
        plans.append(PlannedOrder(index, items, shipping, tracking))
    return plans


def order_payload(plan: PlannedOrder) -> tuple[dict[str, Any], Decimal, list[LineItem]]:
    """The same payload the store checkout sends, with server-side catalog prices."""
    lines = [
        LineItem(
            sku=item["product_id"],
            name=catalog.CATALOG[item["product_id"]].name,
            quantity=item["quantity"],
            unit_price=catalog.CATALOG[item["product_id"]].price,
        )
        for item in plan.items
    ]
    total = sum((ln.unit_price * ln.quantity for ln in lines), Decimal("0")) + catalog.SHIPPING_FLAT
    payload = paypal_orders.build_order_payload(
        items=lines,
        shipping_cost=catalog.SHIPPING_FLAT,
        currency=catalog.CURRENCY,
        shipping=ShippingAddress(
            full_name=plan.shipping["name"],
            **{k: v for k, v in plan.shipping.items() if k != "name"},
        ),
        description="Lotus & Loom Demo Store order",
    )
    return payload, total, lines


# --- Output ---------------------------------------------------------------------------


def load_seeded(path: Path) -> dict[int, SeededOrder]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["index"]: SeededOrder(**row) for row in data["orders"]}


def save_seeded(path: Path, seed: int, seeded: dict[int, SeededOrder]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "description": (
            "Lotus & Loom Demo Store orders seeded in the PayPal sandbox by "
            "scripts/seed_sandbox.py. Fictional names and addresses."
        ),
        "seed": seed,
        "orders": [asdict(seeded[i]) for i in sorted(seeded)],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def upsert_store_order(db: Session, order: SeededOrder) -> None:
    row = db.exec(select(StoreOrder).where(StoreOrder.paypal_order_id == order.order_id)).first()
    if row is None:
        row = StoreOrder(
            buyer_id=SEED_BUYER_ID,
            paypal_order_id=order.order_id,
            currency=order.currency,
            amount=order.amount,
            items_json=json.dumps(order.items),
        )
    if order.capture_id:
        row.status = "captured"
        row.capture_id = order.capture_id
    db.add(row)
    db.commit()


# --- PayPal steps ---------------------------------------------------------------------


async def create_one(paypal: PayPalClient, plan: PlannedOrder, seed: int) -> SeededOrder:
    payload, total, lines = order_payload(plan)
    order = await paypal_orders.create_order(
        paypal, payload, request_id=f"seed:{seed}:{plan.index}:create"
    )
    if order.total.value != total:
        raise RuntimeError(f"PayPal total {order.total.value} differs from {total}")
    return SeededOrder(
        index=plan.index,
        order_id=order.id,
        status="created",
        amount=money(total),
        currency=catalog.CURRENCY,
        items=[
            {"name": ln.name, "quantity": ln.quantity, "unit_price": money(ln.unit_price)}
            for ln in lines
        ],
        shipping=plan.shipping,
        planned_tracking=plan.tracking,
        create_time=order.create_time,
    )


async def advance_one(paypal: PayPalClient, order: SeededOrder) -> bool:
    """Capture an approved order and add its tracking. True when the order needs nothing more."""
    if order.status == "created":
        current = await paypal_orders.get_order(paypal, order.order_id)
        if current.status == "COMPLETED" and current.captures:
            done = current
        elif current.status == "APPROVED":
            done = await paypal_orders.capture_order(
                paypal, order.order_id, request_id=f"capture:{order.order_id}"
            )
        else:
            return False  # still waiting for the buyer
        captures = done.captures
        if not captures or captures[0].status != "COMPLETED":
            order.notes.append(
                f"capture not completed: {captures[0].status if captures else 'none'}"
            )
            return True
        if captures[0].amount.value != Decimal(order.amount):
            order.notes.append("captured amount differs from the order total")
        order.capture_id = captures[0].id
        order.capture_time = captures[0].create_time
        if done.payer and done.payer.name:
            parts = [done.payer.name.given_name, done.payer.name.surname]
            order.payer_name = " ".join(p for p in parts if p) or None
        order.status = "captured"

    if order.status == "captured" and order.planned_tracking and order.capture_id:
        await paypal_tracking.add_tracking(
            paypal,
            order.order_id,
            capture_id=order.capture_id,
            tracking_number=order.planned_tracking["tracking_number"],
            carrier=order.planned_tracking["carrier"],
            request_id=f"track:{order.order_id}",
        )
        order.tracking = dict(order.planned_tracking)
        order.status = "tracked"
    return True


async def seed(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.paypal_client_id or not settings.paypal_client_secret:
        print("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in .env (sandbox app).")
        return 2

    out: Path = args.out
    seeded = load_seeded(out)
    plans = plan_orders(args.count, args.seed)
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)
    paypal = PayPalClient(settings)
    try:
        with Session(engine) as db:
            for plan in plans:
                if plan.index in seeded:
                    continue
                if args.resume:
                    break
                seeded[plan.index] = await create_one(paypal, plan, args.seed)
                upsert_store_order(db, seeded[plan.index])
                save_seeded(out, args.seed, seeded)
                print(f"created {plan.index + 1}/{args.count}: {seeded[plan.index].order_id}")

            waiting = [o for o in seeded.values() if o.status == "created"]
            if waiting:
                print(
                    "\nApprove each order as the sandbox PERSONAL (buyer) account. "
                    "Open each link, log in, and approve:"
                )
                for o in waiting:
                    print(
                        f"  {o.index + 1:>3}  {o.amount} {o.currency}  "
                        f"{APPROVE_URL.format(order_id=o.order_id)}"
                    )
                print("\nWaiting for approvals (Ctrl+C to stop; --resume carries on later)...")

            deadline = time.monotonic() + args.timeout_minutes * 60
            pending = [o for o in seeded.values() if o.status != "tracked" and not o.notes]
            while pending and time.monotonic() < deadline:
                still = []
                for order in pending:
                    try:
                        finished = await advance_one(paypal, order)
                    except PayPalError as exc:
                        order.notes.append(f"PayPal error: {exc.name or 'unknown'}")
                        finished = True
                    if finished:
                        upsert_store_order(db, order)
                        save_seeded(out, args.seed, seeded)
                        print(
                            f"  {order.index + 1:>3}  {order.order_id}  {order.status}"
                            + (f"  ({'; '.join(order.notes)})" if order.notes else "")
                        )
                    else:
                        still.append(order)
                pending = still
                if pending:
                    await asyncio.sleep(args.poll_seconds)
    finally:
        await paypal.aclose()

    done = [o for o in seeded.values() if o.status in ("captured", "tracked")]
    print(
        f"\n{len(done)}/{len(seeded)} orders captured; "
        f"{sum(o.status == 'tracked' for o in seeded.values())} with tracking. Wrote {out}"
    )
    if len(done) < len(seeded):
        print("Some orders are not captured yet. Approve them, then run with --resume.")
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--count", type=int, default=24, help="orders to create (default 24)")
    parser.add_argument("--seed", type=int, default=7, help="random seed for the order mix")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSON file")
    parser.add_argument("--resume", action="store_true", help="only finish orders already created")
    parser.add_argument("--timeout-minutes", type=float, default=30)
    parser.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(seed(args, get_settings()))


if __name__ == "__main__":
    sys.exit(main())
