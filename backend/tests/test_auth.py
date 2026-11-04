import pytest
from fastapi.testclient import TestClient

from app.security.auth import (
    LoginThrottle,
    hash_password,
    issue_session,
    read_session,
    verify_password,
)
from tests.conftest import MERCHANT_EMAIL, MERCHANT_PASSWORD

SECRET = "k" * 40


def test_password_hash_roundtrip_and_salting() -> None:
    first, second = hash_password("pw"), hash_password("pw")
    assert first != second
    assert verify_password("pw", first)
    assert not verify_password("PW", first)


@pytest.mark.parametrize("stored", ["", "plain", "scrypt:1:2", "md5:1:1:1:a:b", "scrypt:x:8:1:a:b"])
def test_malformed_hash_fails_closed(stored: str) -> None:
    assert not verify_password("pw", stored)


def test_session_roundtrip() -> None:
    token, issued = issue_session(SECRET, "demo", now=1000)
    session = read_session(SECRET, token, now=1001)
    assert session == issued
    assert session is not None and session.merchant_id == "demo"


def test_tampered_session_is_rejected() -> None:
    token, _ = issue_session(SECRET, "demo", now=1000)
    body, sig = token.split(".")
    assert read_session(SECRET, f"{body}x.{sig}", now=1001) is None
    assert read_session(SECRET, f"{body}.{sig[:-2]}AA", now=1001) is None
    assert read_session("z" * 40, token, now=1001) is None
    assert read_session(SECRET, "garbage", now=1001) is None


def test_expired_session_is_rejected() -> None:
    token, issued = issue_session(SECRET, "demo", now=1000, ttl=60)
    assert read_session(SECRET, token, now=issued.expires_at - 1) is not None
    assert read_session(SECRET, token, now=issued.expires_at) is None


def test_short_secret_is_refused() -> None:
    with pytest.raises(ValueError):
        issue_session("short", "demo")
    token, _ = issue_session(SECRET, "demo")
    assert read_session("short", token) is None


def test_throttle_blocks_after_five_failures_and_recovers() -> None:
    throttle = LoginThrottle()
    for i in range(5):
        assert not throttle.blocked("k", now=100 + i)
        throttle.record_failure("k", now=100 + i)
    assert throttle.blocked("k", now=110)
    assert not throttle.blocked("other", now=110)
    assert not throttle.blocked("k", now=100 + 400)  # window passed
    throttle.record_failure("k", now=500)
    throttle.reset("k")
    assert not throttle.blocked("k", now=501)


def test_login_success_sets_cookie_and_me_works(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"email": MERCHANT_EMAIL.upper(), "password": MERCHANT_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["merchant_id"] == "demo"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json() == body


def test_me_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_bad_credentials_are_generic(client: TestClient) -> None:
    wrong_pw = client.post("/api/auth/login", json={"email": MERCHANT_EMAIL, "password": "nope"})
    wrong_email = client.post(
        "/api/auth/login", json={"email": "who@example.com", "password": MERCHANT_PASSWORD}
    )
    assert wrong_pw.status_code == wrong_email.status_code == 401
    assert wrong_pw.json() == wrong_email.json()


def test_login_is_throttled(client: TestClient) -> None:
    for _ in range(5):
        client.post("/api/auth/login", json={"email": MERCHANT_EMAIL, "password": "nope"})
    blocked = client.post(
        "/api/auth/login", json={"email": MERCHANT_EMAIL, "password": MERCHANT_PASSWORD}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_logout_needs_csrf_then_clears_session(
    client: TestClient, merchant: dict[str, str]
) -> None:
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/auth/logout", headers=merchant).status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_login_unavailable_without_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SESSION_SECRET", "")
    get_settings.cache_clear()
    response = client.post(
        "/api/auth/login", json={"email": MERCHANT_EMAIL, "password": MERCHANT_PASSWORD}
    )
    assert response.status_code == 503
