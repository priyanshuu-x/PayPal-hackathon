import io
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app.config import SANDBOX_BASE_URL, get_settings
from app.db.models import StoreOrder
from app.main import create_app
from app.security.auth import hash_password

MERCHANT_EMAIL = "merchant@example.com"
MERCHANT_PASSWORD = "correct horse battery"
SESSION_SECRET = "s" * 48
WEBHOOK_ID = "WH-TEST-123"


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", WEBHOOK_ID)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("DEMO_MERCHANT_EMAIL", MERCHANT_EMAIL)
    monkeypatch.setenv("DEMO_MERCHANT_PASSWORD_HASH", hash_password(MERCHANT_PASSWORD))
    get_settings.cache_clear()


@pytest.fixture
def paypal_mock(settings_env: None) -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=SANDBOX_BASE_URL, assert_all_called=False) as router:
        router.post("/v1/oauth2/token").respond(
            200, json={"access_token": "tok-1", "expires_in": 32400}
        )
        yield router


@pytest.fixture
def client(paypal_mock: respx.MockRouter) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture
def merchant(client: TestClient) -> dict[str, str]:
    """Sign in as the merchant; returns the CSRF header to send on POSTs."""
    response = client.post(
        "/api/auth/login", json={"email": MERCHANT_EMAIL, "password": MERCHANT_PASSWORD}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


@pytest.fixture
def captured_order(client: TestClient) -> str:
    """A paid demo-store order owned by this client's buyer session."""
    client.get("/api/store/orders")  # issues the buyer cookie
    buyer_id = client.cookies.get("proofcheck_buyer")
    assert buyer_id
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        db.add(
            StoreOrder(
                buyer_id=buyer_id,
                paypal_order_id="ORD-PAID",
                status="captured",
                currency="USD",
                amount="52.99",
                items_json="[]",
                capture_id="CAP-PAID",
            )
        )
        db.commit()
    return "ORD-PAID"


def png_bytes(size: tuple[int, int] = (20, 20), exif: bool = False) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    out = io.BytesIO()
    if exif:
        exif_data = Image.Exif()
        exif_data[0x010F] = "SecretCameraMaker"
        image.save(out, format="JPEG", exif=exif_data)
    else:
        image.save(out, format="PNG")
    return out.getvalue()
