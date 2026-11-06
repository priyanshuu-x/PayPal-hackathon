import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from app import store_catalog as catalog
from app.config import SANDBOX_BASE_URL, Settings
from app.db.models import AuditEvent, Case, Evidence, StoreOrder, WebhookEvent
from app.db.session import create_db_and_tables, make_engine
from app.paypal.client import PayPalClient
from app.security.audit import append_event, verify_chain
from sqlmodel import Session, select

import demo_reset
import seed_sandbox as seed


async def _no_sleep(_: float) -> None:
    return None


def settings(tmp_path: Path) -> Settings:
    return Settings(
        paypal_client_id="test-id",
        paypal_client_secret="test-secret",  # noqa: S106
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=str(tmp_path / "uploads"),
    )


# --- plan_orders ----------------------------------------------------------------------


def test_plan_is_deterministic_and_within_store_limits() -> None:
    first = seed.plan_orders(24, seed=7)
    assert first == seed.plan_orders(24, seed=7)
    assert first != seed.plan_orders(24, seed=8)
    for plan in first:
        assert 1 <= len(plan.items) <= 3
        assert all(item["product_id"] in catalog.CATALOG for item in plan.items)
        assert all(1 <= item["quantity"] <= catalog.MAX_QUANTITY for item in plan.items)
        assert plan.shipping["country_code"] == "US"
    tracked = [p for p in first if p.tracking]
    assert 0 < len(tracked) < len(first)  # some untracked orders for "not received" cases
    numbers = [p.tracking["tracking_number"] for p in tracked if p.tracking]
    assert len(set(numbers)) == len(numbers)


def test_payload_uses_catalog_prices_and_flat_shipping() -> None:
    plan = seed.PlannedOrder(
        index=0,
        items=[{"product_id": "indigo-mug", "quantity": 2}],
        shipping={
            "name": "Asha Verma",
            "address_line_1": "12 Lotus Lane",
            "admin_area_2": "San Jose",
            "admin_area_1": "CA",
            "postal_code": "95131",
            "country_code": "US",
        },
        tracking=None,
    )
    payload, total, _ = seed.order_payload(plan)
    assert total == Decimal("16.25") * 2 + catalog.SHIPPING_FLAT
    unit = payload["purchase_units"][0]
    assert unit["amount"]["value"] == "37.49"
    assert unit["items"][0]["unit_amount"]["value"] == "16.25"
    assert unit["shipping"]["name"]["full_name"] == "Asha Verma"


# --- PayPal steps (mocked) -------------------------------------------------------------


def _order(status: str, *, capture: bool = False) -> dict[str, object]:
    unit: dict[str, object] = {"amount": {"currency_code": "USD", "value": "37.49"}}
    if capture:
        unit["payments"] = {
            "captures": [
                {
                    "id": "CAP1",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": "37.49"},
                }
            ]
        }
    return {
        "id": "ORDER1",
        "status": status,
        "purchase_units": [unit],
        "payer": {"name": {"given_name": "Sandbox", "surname": "Buyer"}},
    }


def _seeded(tracking: dict[str, str] | None) -> seed.SeededOrder:
    return seed.SeededOrder(
        index=0,
        order_id="ORDER1",
        status="created",
        amount="37.49",
        currency="USD",
        items=[],
        shipping={},
        planned_tracking=tracking,
    )


@pytest.fixture
def paypal_mock(tmp_path: Path):  # type: ignore[no-untyped-def]
    with respx.mock(base_url=SANDBOX_BASE_URL, assert_all_called=False) as mock:
        mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
        yield mock


async def test_waits_while_the_buyer_has_not_approved(paypal_mock, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paypal_mock.get("/v2/checkout/orders/ORDER1").respond(200, json=_order("CREATED"))
    client = PayPalClient(settings(tmp_path), sleep=_no_sleep)
    order = _seeded(None)
    assert await seed.advance_one(client, order) is False
    assert order.status == "created"


async def test_captures_then_tracks_with_stable_request_ids(paypal_mock, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paypal_mock.get("/v2/checkout/orders/ORDER1").respond(200, json=_order("APPROVED"))
    capture = paypal_mock.post("/v2/checkout/orders/ORDER1/capture").respond(
        201, json=_order("COMPLETED", capture=True)
    )
    track = paypal_mock.post("/v2/checkout/orders/ORDER1/track").respond(201, json={})
    client = PayPalClient(settings(tmp_path), sleep=_no_sleep)
    order = _seeded({"carrier": "USPS", "tracking_number": "LL0700012345678"})

    assert await seed.advance_one(client, order) is True
    assert order.status == "tracked"
    assert order.capture_id == "CAP1"
    assert order.payer_name == "Sandbox Buyer"
    assert capture.calls.last.request.headers["PayPal-Request-Id"] == "capture:ORDER1"
    assert track.calls.last.request.headers["PayPal-Request-Id"] == "track:ORDER1"
    body = json.loads(track.calls.last.request.content)
    assert body == {
        "capture_id": "CAP1",
        "tracking_number": "LL0700012345678",
        "carrier": "USPS",
        "notify_payer": False,
    }


async def test_an_already_captured_order_is_not_captured_again(paypal_mock, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paypal_mock.get("/v2/checkout/orders/ORDER1").respond(
        200, json=_order("COMPLETED", capture=True)
    )
    capture = paypal_mock.post("/v2/checkout/orders/ORDER1/capture")
    client = PayPalClient(settings(tmp_path), sleep=_no_sleep)
    order = _seeded(None)
    assert await seed.advance_one(client, order) is True
    assert order.status == "captured"
    assert not capture.called


# --- output files and StoreOrder rows ---------------------------------------------------


def test_output_round_trips_and_store_order_is_upserted(tmp_path: Path) -> None:
    out = tmp_path / "orders.json"
    order = _seeded(None)
    seed.save_seeded(out, 7, {0: order})
    assert seed.load_seeded(out)[0] == order

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    create_db_and_tables(engine)
    with Session(engine) as db:
        seed.upsert_store_order(db, order)
        order.capture_id, order.status = "CAP1", "captured"
        seed.upsert_store_order(db, order)
        rows = db.exec(select(StoreOrder)).all()
        assert len(rows) == 1
        assert (rows[0].buyer_id, rows[0].status, rows[0].capture_id) == (
            seed.SEED_BUYER_ID,
            "captured",
            "CAP1",
        )


# --- demo_reset ------------------------------------------------------------------------


def test_reset_deletes_cases_and_files_and_restarts_a_valid_chain(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "k1").write_bytes(b"x")
    outside = tmp_path / "keep.txt"
    outside.write_text("not an upload")

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    create_db_and_tables(engine)
    with Session(engine) as db:
        db.add(Case(id="c1", source="refund", merchant_id="m"))
        db.commit()
        db.add(
            Evidence(
                case_id="c1", storage_key="k1", sha256="0" * 64, mime="image/png", size_bytes=1
            )
        )
        db.add(
            Evidence(
                case_id="c1",
                storage_key="../keep.txt",
                sha256="1" * 64,
                mime="image/png",
                size_bytes=1,
            )
        )
        db.add(WebhookEvent(event_id="WH-1", event_type="CUSTOMER.DISPUTE.CREATED"))
        db.add(
            StoreOrder(
                buyer_id=seed.SEED_BUYER_ID,
                paypal_order_id="S1",
                currency="USD",
                amount="1.00",
                items_json="[]",
            )
        )
        db.add(
            StoreOrder(
                buyer_id="browser",
                paypal_order_id="B1",
                currency="USD",
                amount="1.00",
                items_json="[]",
            )
        )
        append_event(db, case_id="c1", actor="buyer", action="refund_requested")
        db.commit()

        counts = demo_reset.reset(db, uploads, buyer_orders=True)

        assert (counts.cases, counts.evidence, counts.files, counts.buyer_orders) == (1, 2, 1, 1)
        assert not (uploads / "k1").exists()
        assert outside.exists()  # never deletes outside the upload folder
        assert db.exec(select(Case)).all() == []
        assert db.exec(select(WebhookEvent)).all() == []
        assert [o.paypal_order_id for o in db.exec(select(StoreOrder)).all()] == ["S1"]
        events = db.exec(select(AuditEvent)).all()
        assert [e.action for e in events] == ["demo_reset"]
        assert verify_chain(db)


def test_describe_database_hides_credentials() -> None:
    assert demo_reset.describe_database("postgresql://user:pw@db.example.test:5432/app") == (
        "postgresql://db.example.test/app"
    )
