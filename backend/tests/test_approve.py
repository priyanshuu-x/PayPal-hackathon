import json
from pathlib import Path
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes_cases import _transition
from app.db.models import AuditEvent, Case, Draft
from app.security.audit import verify_chain

ORDER = "5O190127TN364715T"
REFUND = "/v2/payments/captures/CAP-PAID/refund"
NOTE = f"Your refund of 52.99 USD for order {ORDER} has been issued. Thank you."
SNAPSHOT = {
    "order_id": ORDER,
    "amount": "52.99",
    "currency": "USD",
    "created": "2026-11-03T10:15:00Z",
    "items": [{"name": "Lotus Linen Tote", "quantity": 2, "unit_price": "24.00"}],
    "payer_name": "Test Buyer",
    "shipping_status": "shipped",
    "tracking_number": "1Z999AA10123456784",
}


def seed(
    client: TestClient,
    *,
    status: str = "ready",
    version: int = 3,
    source: str = "refund",
    capture_id: str | None = "CAP-PAID",
    snapshot: dict[str, Any] | None = SNAPSHOT,
    draft: str | None = NOTE,
    case_id: str = "c1",
) -> None:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        db.add(
            Case(
                id=case_id,
                source=source,
                merchant_id="demo",
                status=status,
                version=version,
                verdict="MATCHES_RECORDS",
                capture_id=capture_id,
                paypal_order_id=ORDER,
                paypal_record_json=json.dumps(snapshot) if snapshot else None,
            )
        )
        if draft:
            db.add(Draft(case_id=case_id, text=draft, grounded=True, prompt_version="DRAFT_V1"))
        db.commit()


def refund_ok(router: respx.MockRouter, status: str = "COMPLETED") -> respx.Route:
    return router.post(REFUND).mock(
        return_value=httpx.Response(201, json={"id": "REFUND-1", "status": status})
    )


def approve(
    client: TestClient, headers: dict[str, str], version: int = 3, **extra: Any
) -> httpx.Response:
    return client.post(
        "/api/cases/c1/approve",
        json={"action": "approve", "version": version, **extra},
        headers=headers,
    )


def events(client: TestClient, case_id: str = "c1") -> list[AuditEvent]:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        return list(db.exec(select(AuditEvent).where(AuditEvent.case_id == case_id)).all())


def case_row(client: TestClient) -> Case:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        row = db.get(Case, "c1")
        assert row is not None
        return row


# --- the happy path ---------------------------------------------------------------------


def test_approve_refunds_through_paypal_once_with_an_idempotency_key(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    response = approve(client, merchant)

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "approved" and detail["refund_id"] == "REFUND-1"
    assert detail["version"] == 5 and detail["audit_chain_valid"] is True

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["paypal-request-id"] == "c1:approve"
    assert json.loads(request.content) == {"note_to_payer": NOTE}  # a full refund of the capture

    approved = [e for e in events(client) if e.action == "approved"]
    assert len(approved) == 1 and approved[0].actor == "merchant:demo"
    details = json.loads(approved[0].details_json)
    assert details["refund_id"] == "REFUND-1" and details["edited_by_human"] is False
    assert len(details["note_sha256"]) == 64 and NOTE not in approved[0].details_json
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        assert verify_chain(db)


def test_a_pending_refund_still_counts_as_approved(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    refund_ok(paypal_mock, "PENDING")
    assert approve(client, merchant).json()["status"] == "approved"


def test_approve_without_a_draft_sends_no_note(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client, draft=None)
    route = refund_ok(paypal_mock)
    assert approve(client, merchant).status_code == 200
    assert json.loads(route.calls.last.request.content) == {}


# --- decline ---------------------------------------------------------------------------


def test_decline_closes_the_case_without_calling_paypal(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    response = client.post(
        "/api/cases/c1/approve", json={"action": "decline", "version": 3}, headers=merchant
    )
    assert response.status_code == 200 and response.json()["status"] == "declined"
    assert route.call_count == 0
    assert [e.action for e in events(client)] == ["declined"]
    assert approve(client, merchant, version=4).status_code == 409  # decided cases are final


# --- who may call it ------------------------------------------------------------------


def test_no_session_means_401_and_nothing_happens(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    response = client.post("/api/cases/c1/approve", json={"action": "approve", "version": 3})
    assert response.status_code == 401 and route.call_count == 0
    assert case_row(client).status == "ready"


def test_a_missing_or_wrong_csrf_token_means_403(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    body = {"action": "approve", "version": 3}
    assert client.post("/api/cases/c1/approve", json=body).status_code == 403
    wrong = client.post("/api/cases/c1/approve", json=body, headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    assert route.call_count == 0 and case_row(client).status == "ready"


# --- state, version and double clicks -------------------------------------------------


def test_a_stale_version_is_rejected_before_any_paypal_call(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    response = approve(client, merchant, version=2)
    assert response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    assert route.call_count == 0 and events(client) == []


def test_only_ready_cases_can_be_decided(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    route = refund_ok(paypal_mock)
    for i, status in enumerate(["new", "processing", "error", "approved", "declined"]):
        seed(client, status=status, case_id=f"s{i}")
        response = client.post(
            f"/api/cases/s{i}/approve", json={"action": "approve", "version": 3}, headers=merchant
        )
        assert response.status_code == 409, status
    assert route.call_count == 0


def test_approving_twice_refunds_once(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    assert approve(client, merchant).status_code == 200
    assert approve(client, merchant, version=5).status_code == 409
    assert approve(client, merchant, version=3).status_code == 409
    assert route.call_count == 1
    assert len([e for e in events(client) if e.action == "approved"]) == 1


def test_the_claim_is_atomic(client: TestClient) -> None:
    seed(client)
    with Session(client.app.state.engine) as one, Session(client.app.state.engine) as two:  # type: ignore[attr-defined]
        assert _transition(one, "c1", 3, "processing") is True
        one.commit()
        assert _transition(two, "c1", 3, "processing") is False  # the second click loses


def test_disputes_and_cases_without_a_payment_are_not_refundable(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    route = refund_ok(paypal_mock)
    seed(client, source="dispute")
    assert approve(client, merchant).status_code == 409
    seed(client, capture_id=None, case_id="c2")
    response = client.post(
        "/api/cases/c2/approve", json={"action": "approve", "version": 3}, headers=merchant
    )
    assert response.status_code == 409 and route.call_count == 0


def test_unknown_case_and_bad_bodies(client: TestClient, merchant: dict[str, str]) -> None:
    assert (
        client.post(
            "/api/cases/nope/approve", json={"action": "approve", "version": 1}, headers=merchant
        ).status_code
        == 404
    )
    seed(client)
    for body in (
        {"action": "refund-everyone", "version": 3},
        {"action": "approve", "version": 0},
        {"action": "approve"},
        {"action": "approve", "version": 3, "amount": "9999"},  # no way to choose an amount
        {"action": "approve", "version": 3, "draft_text": "x" * 1001},
    ):
        assert client.post("/api/cases/c1/approve", json=body, headers=merchant).status_code == 422


# --- PayPal failures: safe to retry --------------------------------------------------


def test_a_paypal_failure_restores_the_case_and_a_retry_reuses_the_same_key(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = paypal_mock.post(REFUND).mock(
        side_effect=[httpx.Response(500, json={"name": "INTERNAL_SERVICE_ERROR"})] * 4
        + [httpx.Response(201, json={"id": "REFUND-1", "status": "COMPLETED"})]
    )
    first = approve(client, merchant)
    assert first.status_code == 502 and first.json()["error"]["code"] == "upstream_error"
    assert "INTERNAL_SERVICE_ERROR" not in first.text
    row = case_row(client)
    assert row.status == "ready" and row.version == 5 and row.refund_id is None
    assert [e.action for e in events(client)] == ["approval_failed"]

    second = approve(client, merchant, version=5)
    assert second.status_code == 200 and second.json()["refund_id"] == "REFUND-1"
    assert {c.request.headers["paypal-request-id"] for c in route.calls} == {"c1:approve"}
    assert [e.action for e in events(client)] == ["approval_failed", "approved"]


def test_a_declined_refund_leaves_the_case_ready(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    paypal_mock.post(REFUND).mock(
        return_value=httpx.Response(422, json={"name": "REFUND_NOT_ALLOWED", "debug_id": "d9"})
    )
    response = approve(client, merchant)
    assert response.status_code == 502 and "REFUND_NOT_ALLOWED" not in response.text
    assert case_row(client).status == "ready"


def test_a_cancelled_or_failed_refund_is_not_an_approval(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    refund_ok(paypal_mock, "CANCELLED")
    assert approve(client, merchant).status_code == 502
    row = case_row(client)
    assert row.status == "ready" and row.refund_id is None


# --- the note ---------------------------------------------------------------------------


def test_an_edited_grounded_note_is_sent_and_kept_as_human_edited(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    edited = f"Thanks for your patience. We refunded 52.99 USD for order {ORDER}."
    response = approve(client, merchant, draft_text=f"  {edited}  ")
    assert response.status_code == 200
    assert json.loads(route.calls.last.request.content) == {"note_to_payer": edited}
    assert response.json()["draft"]["edited_by_human"] is True
    details = json.loads(next(e for e in events(client) if e.action == "approved").details_json)
    assert details["edited_by_human"] is True


def test_an_unchanged_draft_is_not_marked_as_edited(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    refund_ok(paypal_mock)
    response = approve(client, merchant, draft_text=NOTE)
    assert response.json()["draft"]["edited_by_human"] is False


def test_an_ungrounded_edit_is_refused_and_nothing_is_sent(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client)
    route = refund_ok(paypal_mock)
    for bad in (
        "We refunded 99.00 USD today.",
        "Your parcel 1Z000BB99999999999 was lost.",
        "Ordered on 2026-12-25.",
        "Sorry about the fraud.",
        "x " * 200,
    ):
        response = approve(client, merchant, draft_text=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "validation_error"
    assert route.call_count == 0
    row = case_row(client)
    assert row.status == "ready" and row.version == 3 and events(client) == []


def test_an_edit_without_a_record_to_check_against_is_refused(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client, snapshot=None, draft=None)
    route = refund_ok(paypal_mock)
    assert approve(client, merchant, draft_text="Thanks.").status_code == 422
    assert route.call_count == 0
    assert approve(client, merchant).status_code == 200  # no note at all is fine


def test_a_long_stored_draft_cannot_be_sent(
    client: TestClient, merchant: dict[str, str], paypal_mock: respx.MockRouter
) -> None:
    seed(client, draft="word " * 100)
    route = refund_ok(paypal_mock)
    assert approve(client, merchant).status_code == 422 and route.call_count == 0


# --- the structural guarantee -----------------------------------------------------------


def test_only_the_approve_handler_can_reach_the_refund_function() -> None:
    """CLAUDE.md rule 4: money moves only through POST /cases/{id}/approve."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    users = {
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if "refund_capture" in path.read_text(encoding="utf-8")
    }
    assert users == {"paypal/payments.py", "api/routes_cases.py"}
    routes = (app_dir / "api" / "routes_cases.py").read_text(encoding="utf-8")
    assert routes.count("await refund_capture(") == 1
    start = routes.index("async def approve_case(")
    assert routes.index("await refund_capture(") > start
    assert "@router" not in routes[start : routes.index("await refund_capture(")]


def test_no_other_money_endpoints_exist(client: TestClient) -> None:
    posts = {
        route.path
        for route in client.app.routes  # type: ignore[attr-defined]
        if "POST" in getattr(route, "methods", set())
    }
    assert "/api/cases/{case_id}/approve" in posts
    assert not any("refund" in p and p != "/api/refunds" for p in posts)
