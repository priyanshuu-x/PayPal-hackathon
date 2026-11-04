import json

import httpx
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import AuditEvent, WebhookEvent
from tests.conftest import WEBHOOK_ID

HEADERS = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1",
    "PAYPAL-TRANSMISSION-ID": "tx-1",
    "PAYPAL-TRANSMISSION-SIG": "sig",
    "PAYPAL-TRANSMISSION-TIME": "2026-11-04T10:00:00Z",
}
VERIFY = "/v1/notifications/verify-webhook-signature"

DISPUTE = {
    "dispute_id": "PP-D-1",
    "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
    "status": "WAITING_FOR_SELLER_RESPONSE",
    "dispute_amount": {"currency_code": "USD", "value": "52.99"},
    "seller_response_due_date": "2026-11-20T00:00:00.000Z",
    "disputed_transactions": [{"seller_transaction_id": "CAP-1"}],
}


def _mock_dispute(paypal: respx.MockRouter) -> None:
    paypal.get("/v1/customer/disputes/PP-D-1").respond(200, json=DISPUTE)
    paypal.get("/v2/payments/captures/CAP-1").respond(
        200, json={"supplementary_data": {"related_ids": {"order_id": "ORD-1"}}}
    )


def _event(event_id: str = "WH-EVT-1", event_type: str = "CUSTOMER.DISPUTE.CREATED") -> dict:
    return {"id": event_id, "event_type": event_type, "resource": {"dispute_id": "PP-D-1"}}


def _post(client: TestClient, event: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return client.post(
        "/api/webhooks/paypal",
        content=json.dumps(event),
        headers={"Content-Type": "application/json", **(HEADERS if headers is None else headers)},
    )


def _verified(paypal: respx.MockRouter, status: str = "SUCCESS") -> respx.Route:
    return paypal.post(VERIFY).respond(200, json={"verification_status": status})


def _stored(client: TestClient) -> tuple[int, int]:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        return (
            len(db.exec(select(WebhookEvent)).all()),
            len(db.exec(select(AuditEvent).where(AuditEvent.action == "webhook_received")).all()),
        )


def test_verified_event_is_accepted_and_audited(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    route = _verified(paypal_mock)
    _mock_dispute(paypal_mock)
    event = _event()
    response = _post(client, event)
    assert response.status_code == 200 and response.json() == {"status": "accepted"}
    assert _stored(client) == (1, 1)

    sent = json.loads(route.calls.last.request.content)
    assert sent["webhook_id"] == WEBHOOK_ID
    assert sent["webhook_event"] == event
    assert sent["transmission_id"] == "tx-1" and sent["auth_algo"] == "SHA256withRSA"
    assert sent["cert_url"] == HEADERS["PAYPAL-CERT-URL"]


def test_unverified_signature_is_rejected_and_nothing_is_stored(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _verified(paypal_mock, "FAILURE")
    response = _post(client, _event())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert _stored(client) == (0, 0)


def test_missing_signature_headers_are_rejected_without_calling_paypal(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    route = _verified(paypal_mock)
    for missing in HEADERS:
        partial = {k: v for k, v in HEADERS.items() if k != missing}
        assert _post(client, _event(), headers=partial).status_code == 400, missing
    assert _post(client, _event(), headers={}).status_code == 400
    assert route.call_count == 0
    assert _stored(client) == (0, 0)


def test_paypal_verify_failure_means_unverified(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    paypal_mock.post(VERIFY).respond(500, json={"name": "INTERNAL_SERVICE_ERROR"})
    assert _post(client, _event()).status_code == 400
    assert _stored(client) == (0, 0)


def test_unset_webhook_id_rejects_everything(
    client: TestClient, paypal_mock: respx.MockRouter, monkeypatch
) -> None:
    from app.config import get_settings

    route = _verified(paypal_mock)
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "")
    get_settings.cache_clear()
    assert _post(client, _event()).status_code == 400
    assert route.call_count == 0


def test_duplicate_event_id_is_ignored(client: TestClient, paypal_mock: respx.MockRouter) -> None:
    _verified(paypal_mock)
    _mock_dispute(paypal_mock)
    assert _post(client, _event()).json() == {"status": "accepted"}
    assert _post(client, _event()).json() == {"status": "duplicate"}
    assert _stored(client) == (1, 1)
    assert _post(client, _event("WH-EVT-2")).json() == {"status": "accepted"}
    assert _stored(client) == (2, 2)


def test_other_event_types_are_recorded_but_ignored(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _verified(paypal_mock)
    response = _post(client, _event("WH-EVT-9", "PAYMENT.CAPTURE.COMPLETED"))
    assert response.json() == {"status": "ignored"}
    assert _stored(client) == (1, 1)


def test_malformed_bodies_are_rejected(client: TestClient, paypal_mock: respx.MockRouter) -> None:
    route = _verified(paypal_mock)
    for body in (b"not json", b"[1,2]", b'"text"', b"null"):
        response = client.post(
            "/api/webhooks/paypal",
            content=body,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 400, body
    assert _post(client, {"event_type": "X"}).status_code == 400  # verified but no event ID
    assert route.call_count == 1
    assert _stored(client) == (0, 0)


def test_oversized_body_is_rejected(client: TestClient, paypal_mock: respx.MockRouter) -> None:
    route = _verified(paypal_mock)
    big = {"id": "WH-BIG", "event_type": "X", "pad": "a" * (1024 * 1024 + 1)}
    assert _post(client, big).status_code == 413  # refused by the size limit, before it is read
    assert route.call_count == 0
