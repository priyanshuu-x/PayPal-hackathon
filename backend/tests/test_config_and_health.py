import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import SANDBOX_BASE_URL, Settings
from app.main import create_app


def test_default_base_url_is_sandbox() -> None:
    assert Settings(_env_file=None).paypal_base_url == SANDBOX_BASE_URL


def test_live_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, paypal_base_url="https://api-m.paypal.com")


def test_copilot_tool_cap_cannot_exceed_five() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, copilot_max_tool_calls=6)


def test_health_and_security_headers() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
