from collections.abc import Iterator
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from app.config import SANDBOX_BASE_URL, get_settings
from app.main import create_app


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("COOKIE_SECURE", "false")
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
