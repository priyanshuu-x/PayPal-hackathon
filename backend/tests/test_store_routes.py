import json
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

SHIPPING = {
    "name": "Test Buyer",
    "address_line_1": "1 Main St",
    "admin_area_2": "San Jose",
    "admin_area_1": "CA",
    "postal_code": "95131",
    "country_code": "us",
}


def _order_body(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    default = [{"product_id": "lotus-tote", "quantity": 2}]
    return {"items": default if items is None else items, "shipping": SHIPPING}


def _paypal_order(order_id: str, value: str, status: str = "CREATED", capture: str | None = None):
    unit: dict[str, Any] = {"amount": {"currency_code": "USD", "value": value}}
    if capture:
        unit["payments"] = {
            "captures": [
                {
                    "id": "CAP-1",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "USD", "value": capture},
                }
            ]
        }
    return {"id": order_id, "status": status, "purchase_units": [unit]}


def _create(
    client: TestClient, paypal: respx.MockRouter, order_id: str = "ORD-1"
) -> httpx.Response:
    paypal.post("/v2/checkout/orders").respond(201, json=_paypal_order(order_id, "52.99"))
    return client.post("/api/store/orders", json=_order_body())


def test_products_come_from_the_server_catalog(client: TestClient) -> None:
    products = client.get("/api/store/products").json()
    assert {p["id"] for p in products} >= {"lotus-tote", "loom-throw"}
    assert all(p["currency"] == "USD" for p in products)


def test_create_order_computes_the_price_server_side(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    response = _create(client, paypal_mock)
    assert response.status_code == 200
    assert response.json() == {"order_id": "ORD-1", "amount": "52.99", "currency": "USD"}

    sent = json.loads(paypal_mock.calls.last.request.content)
    unit = sent["purchase_units"][0]
    assert unit["amount"]["value"] == "52.99"  # 2 x 24.00 + 4.99 flat shipping
    assert unit["amount"]["breakdown"]["item_total"]["value"] == "48.00"
    assert unit["amount"]["breakdown"]["shipping"]["value"] == "4.99"
    assert unit["items"][0]["sku"] == "lotus-tote"
    assert unit["shipping"]["address"]["country_code"] == "US"
    assert paypal_mock.calls.last.request.headers["paypal-request-id"].startswith("create:")
    assert paypal_mock.calls.last.request.headers["prefer"] == "return=representation"


def test_browser_supplied_price_is_rejected(client: TestClient) -> None:
    body = _order_body([{"product_id": "lotus-tote", "quantity": 1, "price": "0.01"}])
    response = client.post("/api/store/orders", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "0.01" not in response.text


def test_unknown_product_and_bad_quantity_rejected(client: TestClient) -> None:
    for items in (
        [{"product_id": "nope", "quantity": 1}],
        [{"product_id": "lotus-tote", "quantity": 0}],
        [{"product_id": "lotus-tote", "quantity": 11}],
        [],
    ):
        response = client.post("/api/store/orders", json=_order_body(items))
        assert response.status_code == 422, items
    # same product on two lines is merged and still capped at the maximum
    response = client.post(
        "/api/store/orders",
        json=_order_body(
            [
                {"product_id": "lotus-tote", "quantity": 6},
                {"product_id": "lotus-tote", "quantity": 6},
            ]
        ),
    )
    assert response.status_code == 422


def test_paypal_total_that_differs_from_ours_is_refused(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    paypal_mock.post("/v2/checkout/orders").respond(201, json=_paypal_order("ORD-X", "1.00"))
    response = client.post("/api/store/orders", json=_order_body())
    assert response.status_code == 502
    assert client.get("/api/store/orders").json() == []


def test_paypal_error_is_generic_for_the_browser(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    paypal_mock.post("/v2/checkout/orders").respond(
        422, json={"name": "UNPROCESSABLE_ENTITY", "debug_id": "d1", "message": "internal detail"}
    )
    response = client.post("/api/store/orders", json=_order_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert "internal detail" not in response.text
    assert "d1" not in response.text


def test_capture_happy_path_and_idempotent_replay(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _create(client, paypal_mock)
    paypal_mock.get("/v2/checkout/orders/ORD-1").respond(
        200, json=_paypal_order("ORD-1", "52.99", status="APPROVED")
    )
    capture = paypal_mock.post("/v2/checkout/orders/ORD-1/capture").respond(
        201, json=_paypal_order("ORD-1", "52.99", status="COMPLETED", capture="52.99")
    )
    first = client.post("/api/store/orders/ORD-1/capture")
    assert first.status_code == 200
    assert first.json() == {
        "order_id": "ORD-1",
        "capture_id": "CAP-1",
        "status": "COMPLETED",
        "amount": "52.99",
        "currency": "USD",
    }
    assert capture.calls.last.request.headers["paypal-request-id"] == "capture:ORD-1"
    assert capture.calls.last.request.headers["prefer"] == "return=representation"

    again = client.post("/api/store/orders/ORD-1/capture")
    assert again.status_code == 200
    assert capture.call_count == 1  # no second capture call


def test_capture_requires_buyer_approval(client: TestClient, paypal_mock: respx.MockRouter) -> None:
    _create(client, paypal_mock)
    paypal_mock.get("/v2/checkout/orders/ORD-1").respond(
        200, json=_paypal_order("ORD-1", "52.99", status="CREATED")
    )
    response = client.post("/api/store/orders/ORD-1/capture")
    assert response.status_code == 409


def test_capture_refuses_a_changed_total(client: TestClient, paypal_mock: respx.MockRouter) -> None:
    _create(client, paypal_mock)
    paypal_mock.get("/v2/checkout/orders/ORD-1").respond(
        200, json=_paypal_order("ORD-1", "0.01", status="APPROVED")
    )
    assert client.post("/api/store/orders/ORD-1/capture").status_code == 409


def test_capture_refuses_a_captured_amount_mismatch(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _create(client, paypal_mock)
    paypal_mock.get("/v2/checkout/orders/ORD-1").respond(
        200, json=_paypal_order("ORD-1", "52.99", status="APPROVED")
    )
    paypal_mock.post("/v2/checkout/orders/ORD-1/capture").respond(
        201, json=_paypal_order("ORD-1", "52.99", status="COMPLETED", capture="1.00")
    )
    assert client.post("/api/store/orders/ORD-1/capture").status_code == 502


def test_capture_only_works_for_the_buyer_who_created_the_order(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _create(client, paypal_mock)
    with TestClient(client.app) as other:  # a different cookie jar, so another buyer
        response = other.post("/api/store/orders/ORD-1/capture")
        assert response.status_code == 404
        assert other.get("/api/store/orders").json() == []


def test_unknown_order_capture_is_404(client: TestClient) -> None:
    assert client.post("/api/store/orders/NOPE/capture").status_code == 404


def test_my_orders_lists_only_this_buyers_orders(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    _create(client, paypal_mock)
    orders = client.get("/api/store/orders").json()
    assert len(orders) == 1
    assert orders[0]["order_id"] == "ORD-1"
    assert orders[0]["status"] == "created"
    assert orders[0]["items"] == [
        {"name": "Lotus Linen Tote", "quantity": 2, "unit_price": "24.00"}
    ]


def test_buyer_cookie_is_httponly_and_lax(client: TestClient) -> None:
    response = client.get("/api/store/orders")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_sdk_config_returns_client_id_and_caches_the_token(
    client: TestClient, paypal_mock: respx.MockRouter
) -> None:
    token_route = paypal_mock.post("/v1/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "browser-token", "expires_in": 900})
    )
    first = client.get("/api/store/sdk-config").json()
    second = client.get("/api/store/sdk-config").json()
    assert first == {"client_id": "test-client-id", "client_token": "browser-token"}
    assert second == first
    assert token_route.call_count == 1  # second call served from the cache
    assert "response_type=client_token" in token_route.calls.last.request.content.decode()
    assert "test-client-secret" not in json.dumps(first)
