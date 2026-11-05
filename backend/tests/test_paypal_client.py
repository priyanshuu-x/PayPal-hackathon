import json
from decimal import Decimal

import httpx
import pytest
import respx

from app.config import SANDBOX_BASE_URL, Settings
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.payments import refund_capture


async def _no_sleep(_: float) -> None:
    return None


def _client() -> PayPalClient:
    settings = Settings(_env_file=None, paypal_client_id="id", paypal_client_secret="secret")
    return PayPalClient(settings, sleep=_no_sleep)


@pytest.fixture
def mock() -> respx.MockRouter:
    with respx.mock(base_url=SANDBOX_BASE_URL) as router:
        yield router


async def test_token_is_fetched_once_and_cached(mock: respx.MockRouter) -> None:
    token = mock.post("/v1/oauth2/token").respond(
        200, json={"access_token": "tok", "expires_in": 3600}
    )
    mock.get("/v2/checkout/orders/A").respond(200, json={"id": "A"})
    client = _client()
    await client.request("GET", "/v2/checkout/orders/A")
    await client.request("GET", "/v2/checkout/orders/A")
    assert token.call_count == 1
    sent = mock.calls.last.request
    assert sent.headers["authorization"] == "Bearer tok"
    await client.aclose()


async def test_expired_token_is_refreshed(mock: respx.MockRouter) -> None:
    token = mock.post("/v1/oauth2/token").respond(
        200, json={"access_token": "tok", "expires_in": 3600}
    )
    mock.get("/x").respond(200, json={})
    client = _client()
    await client.request("GET", "/x")
    assert client._token is not None
    client._token.expires_at = 0  # simulate expiry
    await client.request("GET", "/x")
    assert token.call_count == 2
    await client.aclose()


async def test_401_refreshes_token_and_retries_once(mock: respx.MockRouter) -> None:
    token = mock.post("/v1/oauth2/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "old", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "new", "expires_in": 3600}),
        ]
    )
    route = mock.get("/x").mock(
        side_effect=[
            httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    client = _client()
    assert await client.request("GET", "/x") == {"ok": True}
    assert token.call_count == 2
    assert route.calls.last.request.headers["authorization"] == "Bearer new"
    await client.aclose()


async def test_422_is_not_retried_and_hides_body_from_message(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.post("/v2/checkout/orders").respond(
        422,
        json={"name": "UNPROCESSABLE_ENTITY", "debug_id": "dbg1", "message": "secret=hunter2xx"},
    )
    client = _client()
    with pytest.raises(PayPalError) as err:
        await client.request("POST", "/v2/checkout/orders", json={})
    assert route.call_count == 1
    assert err.value.status_code == 422
    assert err.value.debug_id == "dbg1"
    assert err.value.is_client_error
    assert "hunter2xx" not in str(err.value)
    assert "hunter2xx" not in json.dumps(err.value.details)
    await client.aclose()


async def test_429_is_retried_then_succeeds(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.get("/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    client = _client()
    assert await client.request("GET", "/x") == {"ok": 1}
    assert route.call_count == 3
    await client.aclose()


async def test_gives_up_after_three_retries(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.get("/x").respond(500, json={"name": "INTERNAL_SERVICE_ERROR"})
    client = _client()
    with pytest.raises(PayPalError) as err:
        await client.request("GET", "/x")
    assert route.call_count == 4  # first try plus 3 retries
    assert err.value.status_code == 500
    await client.aclose()


async def test_transport_error_is_retried_then_raised(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.get("/x").mock(side_effect=httpx.ConnectError("boom"))
    client = _client()
    with pytest.raises(PayPalError):
        await client.request("GET", "/x")
    assert route.call_count == 4
    await client.aclose()


async def test_refund_retry_reuses_the_same_request_id(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.post("/v2/payments/captures/CAP1/refund").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(201, json={"id": "R1", "status": "COMPLETED"}),
        ]
    )
    client = _client()
    refund = await refund_capture(client, "CAP1", request_id="case-1:approve")
    assert refund.id == "R1"
    ids = {c.request.headers["paypal-request-id"] for c in route.calls}
    assert ids == {"case-1:approve"}
    await client.aclose()


async def test_partial_refund_body_and_validation(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(200, json={"access_token": "t", "expires_in": 3600})
    route = mock.post("/v2/payments/captures/CAP1/refund").respond(
        201, json={"id": "R2", "status": "COMPLETED"}
    )
    client = _client()
    await refund_capture(
        client, "CAP1", request_id="k", amount=Decimal("5"), currency="USD", note="Thanks"
    )
    assert json.loads(route.calls.last.request.content) == {
        "amount": {"currency_code": "USD", "value": "5.00"},
        "note_to_payer": "Thanks",
    }
    with pytest.raises(ValueError):
        await refund_capture(client, "CAP1", request_id="")
    with pytest.raises(ValueError):
        await refund_capture(client, "CAP1", request_id="k", amount=Decimal("1"))
    await client.aclose()


async def test_client_token_request_shape(mock: respx.MockRouter) -> None:
    route = mock.post("/v1/oauth2/token").respond(
        200, json={"access_token": "browser-safe", "expires_in": 900}
    )
    client = _client()
    assert await client.client_token(["example.com"]) == "browser-safe"
    body = route.calls.last.request.content.decode()
    assert "response_type=client_token" in body
    assert "domains%5B0%5D=example.com" in body
    await client.aclose()


async def test_token_fetch_retries_transport_errors(mock: respx.MockRouter) -> None:
    token = mock.post("/v1/oauth2/token").mock(
        side_effect=[
            httpx.ConnectTimeout("slow"),
            httpx.Response(200, json={"access_token": "t", "expires_in": 3600}),
        ]
    )
    mock.get("/x").respond(200, json={})
    client = _client()
    await client.request("GET", "/x")
    assert token.call_count == 2
    await client.aclose()


async def test_token_endpoint_failure_raises(mock: respx.MockRouter) -> None:
    mock.post("/v1/oauth2/token").respond(401, json={"error": "invalid_client"})
    client = _client()
    with pytest.raises(PayPalError):
        await client.request("GET", "/x")
    await client.aclose()
