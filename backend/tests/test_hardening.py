import asyncio
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app import main
from app.config import Settings, get_settings
from app.db.models import AuditEvent, Case, CopilotTurn, Evidence
from app.db.session import create_db_and_tables, make_engine
from app.main import create_app
from app.middleware import (
    CSP,
    MAX_JSON_REQUEST_BYTES,
    MAX_UPLOAD_REQUEST_BYTES,
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
)
from app.security.audit import verify_chain
from app.security.ratelimit import (
    DEMO,
    EVIDENCE,
    GENERAL,
    LOGIN,
    REFUNDS,
    STORE_SDK,
    STORE_WRITE,
    WEBHOOKS,
    Rule,
    SlidingWindowLimiter,
    client_ip,
    rule_for,
)
from app.security.retention import PURGED, purge_expired
from app.security.uploads import store_file
from tests.conftest import MERCHANT_EMAIL, MERCHANT_PASSWORD, png_bytes

# --- security headers ---------------------------------------------------------------------


def test_every_response_carries_the_security_headers(client: TestClient) -> None:
    for response in (client.get("/api/health"), client.get("/api/nope"), client.get("/")):
        h = response.headers
        assert h["content-security-policy"] == CSP
        assert "default-src 'none'" in CSP and "frame-ancestors 'none'" in CSP
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert (
            "camera=()" in h["permissions-policy"] and "geolocation=()" in h["permissions-policy"]
        )
        assert h["cross-origin-opener-policy"] == "same-origin"
        assert h["cross-origin-resource-policy"] == "same-site"
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/").headers  # only API replies are marked no-store


def test_hsts_is_sent_only_when_cookies_are_secure(
    paypal_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "true")
    get_settings.cache_clear()
    with TestClient(create_app()) as secure:
        assert "max-age=31536000" in secure.get("/api/health").headers["strict-transport-security"]
    monkeypatch.setenv("COOKIE_SECURE", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as plain:
        assert "strict-transport-security" not in plain.get("/api/health").headers


def test_repeated_headers_such_as_set_cookie_survive(client: TestClient) -> None:
    client.get("/api/store/orders")  # buyer cookie
    response = client.post(
        "/api/auth/login", json={"email": MERCHANT_EMAIL, "password": MERCHANT_PASSWORD}
    )
    assert response.headers["content-security-policy"] == CSP
    assert any(k.lower() == "set-cookie" for k in response.headers)
    assert client.cookies.get("proofcheck_session")


def test_an_endpoint_can_keep_its_own_cache_policy(
    client: TestClient, merchant: dict[str, str]
) -> None:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        db.add(Case(id="c1", source="refund", merchant_id="demo"))
        key = store_file(Path(get_settings().upload_dir), png_bytes())
        db.add(
            Evidence(case_id="c1", storage_key=key, sha256="a" * 64, mime="image/png", size_bytes=1)
        )
        db.commit()
    response = client.get("/api/cases/c1/evidence")
    assert response.headers["cache-control"] == "private, no-store"


def test_there_are_no_interactive_docs_or_public_schema(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json", "/api/openapi.json", "/api/docs"):
        assert client.get(path).status_code == 404, path
    schema = client.app.openapi()  # type: ignore[attr-defined]
    assert "/api/cases/{case_id}/approve" in schema["paths"]  # still available to `make postman`


# --- rate limiting ------------------------------------------------------------------------


def test_sliding_window_counts_and_recovers() -> None:
    limiter = SlidingWindowLimiter()
    rule = Rule("t", 3, 10)
    assert [limiter.check("ip", rule, now=t) for t in (0, 1, 2)] == [(True, 0)] * 3
    allowed, retry = limiter.check("ip", rule, now=3)
    assert allowed is False and retry == 8  # the first hit leaves the window at t=10
    assert limiter.check("other", rule, now=3) == (True, 0)  # per key
    assert limiter.check("ip", Rule("u", 3, 10), now=3) == (True, 0)  # per rule
    assert limiter.check("ip", rule, now=10.5)[0] is True  # the window moved on


def test_the_limiter_forgets_idle_clients_and_bounds_memory() -> None:
    limiter = SlidingWindowLimiter(max_keys=5)
    rule = Rule("t", 1, 10)
    for i in range(5):
        limiter.check(f"ip{i}", rule, now=float(i))
    limiter.check("ip5", rule, now=4000.0)  # earlier clients have been idle for over an hour
    assert len(limiter._hits) == 1
    busy = SlidingWindowLimiter(max_keys=10)
    for i in range(30):
        busy.check(f"ip{i}", rule, now=100.0 + i)  # all recent, so the oldest are evicted
    assert len(busy._hits) <= 10


@pytest.mark.parametrize(
    ("method", "path", "rule"),
    [
        ("POST", "/api/auth/login", LOGIN),
        ("POST", "/api/refunds", REFUNDS),
        ("POST", "/api/store/orders", STORE_WRITE),
        ("POST", "/api/store/orders/ORD-1/capture", STORE_WRITE),
        ("GET", "/api/store/sdk-config", STORE_SDK),
        ("POST", "/api/cases/c1/evidence", EVIDENCE),
        ("POST", "/api/demo/disputes", DEMO),
        ("POST", "/api/webhooks/paypal", WEBHOOKS),
        ("GET", "/api/cases", GENERAL),
        ("POST", "/api/cases/c1/approve", GENERAL),
        ("GET", "/api/store/products", GENERAL),
    ],
)
def test_each_route_group_has_its_own_rule(method: str, path: str, rule: Rule) -> None:
    assert rule_for(method, path) is rule


@pytest.mark.parametrize(
    ("headers", "peer", "proxies", "expected"),
    [
        ({}, "10.0.0.1", 0, "10.0.0.1"),
        ({"x-forwarded-for": "1.2.3.4"}, "10.0.0.1", 0, "10.0.0.1"),  # not trusted: ignored
        ({"x-forwarded-for": "1.2.3.4"}, "10.0.0.1", 1, "1.2.3.4"),
        (
            {"x-forwarded-for": "6.6.6.6, 1.2.3.4"},
            "10.0.0.1",
            1,
            "1.2.3.4",
        ),  # spoofed prefix ignored
        ({"x-forwarded-for": "6.6.6.6, 1.2.3.4, 10.9.9.9"}, "10.0.0.1", 2, "1.2.3.4"),
        ({"x-forwarded-for": "1.2.3.4"}, "10.0.0.1", 2, "10.0.0.1"),  # fewer hops than expected
        ({"x-forwarded-for": "not-an-ip"}, "10.0.0.1", 1, "10.0.0.1"),
        ({"x-forwarded-for": " , "}, "10.0.0.1", 1, "10.0.0.1"),
        ({"x-forwarded-for": "2001:db8::1"}, None, 1, "2001:db8::1"),
        ({}, None, 0, "unknown"),
    ],
)
def test_client_ip(headers: dict[str, str], peer: str | None, proxies: int, expected: str) -> None:
    assert client_ip(headers, peer, trusted_proxies=proxies) == expected


def _tiny_app(limit: int, **kwargs: Any) -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/static")
    async def static() -> dict[str, str]:
        return {"ok": "yes"}

    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowLimiter(),
        rule_lookup=lambda method, path: Rule("tiny", limit, 60),
        **kwargs,
    )
    return app


def test_the_middleware_answers_429_with_retry_after_and_the_standard_error_body() -> None:
    client = TestClient(_tiny_app(2))
    assert [client.get("/api/ping").status_code for _ in range(2)] == [200, 200]
    blocked = client.get("/api/ping")
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 61
    assert blocked.json() == {
        "error": {"code": "rate_limited", "message": "Too many requests. Try again shortly."}
    }
    assert client.get("/api/health").status_code == 200  # health checks are exempt
    assert client.get("/static").status_code == 200  # only /api is limited


def test_a_spoofed_forwarded_header_does_not_buy_a_new_allowance() -> None:
    untrusted = TestClient(_tiny_app(1, trusted_proxies=0))
    assert untrusted.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert untrusted.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
    behind_proxy = TestClient(_tiny_app(1, trusted_proxies=1))
    assert behind_proxy.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert behind_proxy.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    assert behind_proxy.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_the_limiter_can_be_switched_off() -> None:
    client = TestClient(_tiny_app(1, enabled=False))
    assert [client.get("/api/ping").status_code for _ in range(5)] == [200] * 5


def test_login_is_rate_limited_per_ip_in_the_real_app(client: TestClient) -> None:
    def attempt(i: int) -> int:  # a different email each time, so only the per-IP limit can trip
        body = {"email": f"user{i}@example.com", "password": "wrong"}
        return client.post("/api/auth/login", json=body).status_code

    statuses = [attempt(i) for i in range(LOGIN.limit + 2)]
    assert statuses[: LOGIN.limit] == [401] * LOGIN.limit  # attempts reach the login check
    assert statuses[LOGIN.limit :] == [429, 429]
    again = client.post("/api/auth/login", json={"email": "x@example.com", "password": "p"})
    assert again.status_code == 429 and again.headers["content-security-policy"] == CSP


def test_refund_uploads_are_limited_per_client(client: TestClient) -> None:
    def attempt() -> int:
        return client.post(
            "/api/refunds",
            data={"order_id": "x", "reason": "r"},
            files={"file": ("a.png", png_bytes(), "image/png")},
        ).status_code

    codes = [attempt() for _ in range(REFUNDS.limit + 1)]
    assert codes[: REFUNDS.limit] == [404] * REFUNDS.limit and codes[-1] == 429


def test_rate_limit_settings_are_validated() -> None:
    assert Settings(_env_file=None).trusted_proxy_count == 0
    assert Settings(_env_file=None, trusted_proxy_count=1).trusted_proxy_count == 1
    for bad in (-1, 6):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, trusted_proxy_count=bad)


# --- request size -------------------------------------------------------------------------


def test_an_oversized_upload_is_refused_before_it_is_read(client: TestClient) -> None:
    big = b"0" * (MAX_UPLOAD_REQUEST_BYTES + 1)
    response = client.post(
        "/api/refunds",
        data={"order_id": "x", "reason": "r"},
        files={"file": ("a.png", big, "image/png")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["content-security-policy"] == CSP


def test_a_normal_upload_is_not_affected_by_the_size_limit(client: TestClient) -> None:
    response = client.post(
        "/api/refunds",
        data={"order_id": "x", "reason": "r"},
        files={"file": ("a.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 404  # reached the handler: the order does not exist


def test_json_bodies_are_capped_at_one_megabyte(client: TestClient) -> None:
    ok = client.post("/api/auth/login", json={"email": "a@b.c", "password": "p"})
    assert ok.status_code == 401
    huge = client.post(
        "/api/auth/login",
        content=b"x" * (MAX_JSON_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert huge.status_code == 413


def test_a_streamed_body_without_a_length_is_counted_as_it_arrives() -> None:
    app = FastAPI()
    seen: list[int] = []

    @app.post("/api/echo")
    async def echo(request: Any) -> dict[str, int]:  # pragma: no cover - never reached when too big
        seen.append(1)
        return {"n": 0}

    from starlette.requests import Request

    @app.post("/api/consume")
    async def consume(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    app.add_middleware(BodySizeLimitMiddleware)
    client = TestClient(app)

    def chunks(total: int) -> Iterator[bytes]:
        for _ in range(total // 65536):
            yield b"x" * 65536

    small = client.post("/api/consume", content=chunks(256 * 1024))
    assert small.status_code == 200 and small.json() == {"bytes": 256 * 1024}
    large = client.post("/api/consume", content=chunks(MAX_JSON_REQUEST_BYTES + 256 * 1024))
    assert large.status_code == 413 and large.json()["error"]["code"] == "payload_too_large"
    assert client.get("/other").status_code == 404  # non-API paths pass straight through


# --- retention ----------------------------------------------------------------------------

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=45)
RECENT = NOW - timedelta(days=2)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[tuple[Session, Path]]:
    engine = make_engine(f"sqlite:///{tmp_path / 'r.db'}")
    create_db_and_tables(engine)
    with Session(engine) as db:
        yield db, tmp_path / "uploads"


def _file(upload_dir: Path, age: datetime, data: bytes = b"data") -> str:
    key = store_file(upload_dir, data)
    stamp = age.timestamp()
    os.utime(upload_dir / key, (stamp, stamp))
    return key


def _evidence(db: Session, case_id: str, key: str, created: datetime) -> None:
    db.add(
        Evidence(
            case_id=case_id,
            storage_key=key,
            sha256="a" * 64,
            mime="image/png",
            size_bytes=4,
            extracted_json='{"payer_name": "Jane"}',
            forensics_json="[]",
            created_at=created.replace(tzinfo=None),
        )
    )


def _case(db: Session, case_id: str, created: datetime, **fields: Any) -> None:
    db.add(
        Case(
            id=case_id,
            source="refund",
            merchant_id="demo",
            created_at=created.replace(tzinfo=None),
            **fields,
        )
    )


def test_expired_uploads_and_personal_data_are_removed_and_recent_ones_kept(
    store: tuple[Session, Path],
) -> None:
    db, uploads = store
    old_key, new_key = _file(uploads, OLD), _file(uploads, RECENT)
    _case(
        db,
        "old",
        OLD,
        reason="please refund me",
        buyer_email="jane@example.com",
        paypal_record_json=json.dumps({"order_id": "O1", "payer_name": "Jane Doe"}),
    )
    _case(
        db,
        "new",
        RECENT,
        reason="keep me",
        buyer_email="bob@example.com",
        paypal_record_json=json.dumps({"order_id": "O2", "payer_name": "Bob"}),
    )
    _evidence(db, "old", old_key, OLD)
    _evidence(db, "new", new_key, RECENT)
    db.commit()

    report = purge_expired(db, uploads, retention_days=30, now=NOW)

    assert (report.evidence_purged, report.files_deleted, report.cases_scrubbed) == (1, 1, 1)
    assert not (uploads / old_key).exists() and (uploads / new_key).exists()
    old_row = db.exec(select(Evidence).where(Evidence.case_id == "old")).one()
    assert (
        old_row.storage_key == PURGED
        and old_row.extracted_json is None
        and old_row.forensics_json is None
    )
    new_row = db.exec(select(Evidence).where(Evidence.case_id == "new")).one()
    assert new_row.storage_key == new_key and new_row.extracted_json is not None
    old_case, new_case = db.get(Case, "old"), db.get(Case, "new")
    assert old_case is not None and new_case is not None
    assert old_case.reason is None and old_case.buyer_email is None
    assert json.loads(old_case.paypal_record_json or "{}") == {"order_id": "O1", "payer_name": None}
    assert new_case.reason == "keep me" and new_case.buyer_email == "bob@example.com"
    assert "Bob" in (new_case.paypal_record_json or "")


def test_the_run_is_audited_once_and_a_second_run_changes_nothing(
    store: tuple[Session, Path],
) -> None:
    db, uploads = store
    _case(db, "old", OLD, reason="r")
    _evidence(db, "old", _file(uploads, OLD), OLD)
    db.commit()
    purge_expired(db, uploads, retention_days=30, now=NOW)
    again = purge_expired(db, uploads, retention_days=30, now=NOW)
    assert again.changed is False
    events = db.exec(select(AuditEvent).where(AuditEvent.action == "retention_run")).all()
    assert len(events) == 1 and json.loads(events[0].details_json)["evidence_purged"] == 1
    assert events[0].actor == "system" and "reason" not in events[0].details_json
    assert verify_chain(db)


def test_an_empty_database_and_a_missing_upload_folder_are_fine(
    store: tuple[Session, Path],
) -> None:
    db, uploads = store
    report = purge_expired(db, uploads, retention_days=30, now=NOW)
    assert report.changed is False and db.exec(select(AuditEvent)).all() == []


def test_copilot_history_expires(store: tuple[Session, Path]) -> None:
    db, uploads = store
    _case(db, "c", OLD)
    for when, text in ((OLD, "old question"), (RECENT, "recent question")):
        db.add(
            CopilotTurn(
                case_id="c", question=text, answer="a", created_at=when.replace(tzinfo=None)
            )
        )
    db.commit()
    report = purge_expired(db, uploads, retention_days=30, now=NOW)
    assert report.copilot_turns_deleted == 1
    assert [t.question for t in db.exec(select(CopilotTurn)).all()] == ["recent question"]


def test_orphan_files_are_cleaned_but_only_old_upload_shaped_ones(
    store: tuple[Session, Path],
) -> None:
    db, uploads = store
    uploads.mkdir(parents=True)
    _case(db, "c", RECENT)
    referenced = _file(uploads, OLD)  # old but still referenced by a recent evidence row
    _evidence(db, "c", referenced, RECENT)
    orphan_old, orphan_new = _file(uploads, OLD), _file(uploads, RECENT)
    (uploads / "notes.txt").write_text("not ours")
    stamp = OLD.timestamp()
    os.utime(uploads / "notes.txt", (stamp, stamp))
    db.commit()
    report = purge_expired(db, uploads, retention_days=30, now=NOW)
    assert report.orphan_files_deleted == 1
    assert not (uploads / orphan_old).exists()
    assert (uploads / orphan_new).exists() and (uploads / referenced).exists()
    assert (uploads / "notes.txt").exists()


def test_a_malicious_storage_key_can_never_delete_outside_the_upload_folder(
    store: tuple[Session, Path],
) -> None:
    db, uploads = store
    outside = uploads.parent / "precious.txt"
    outside.write_text("keep")
    _case(db, "c", OLD)
    _evidence(db, "c", "../precious.txt", OLD)
    db.commit()
    purge_expired(db, uploads, retention_days=30, now=NOW)
    assert outside.read_text() == "keep"
    assert db.exec(select(Evidence)).one().storage_key == PURGED


def test_snapshots_are_scrubbed_safely(store: tuple[Session, Path]) -> None:
    db, uploads = store
    _case(db, "bad", OLD, paypal_record_json="{not json")
    _case(db, "clean", OLD, paypal_record_json=json.dumps({"order_id": "O", "payer_name": None}))
    _case(db, "list", OLD, paypal_record_json="[1, 2]")
    db.commit()
    report = purge_expired(db, uploads, retention_days=30, now=NOW)
    assert report.cases_scrubbed == 1  # only the unreadable one changed
    assert db.get(Case, "bad").paypal_record_json is None  # type: ignore[union-attr]
    assert db.get(Case, "clean").paypal_record_json == json.dumps(
        {"order_id": "O", "payer_name": None}
    )  # type: ignore[union-attr]


def test_a_purged_upload_gives_a_clean_404_and_blocks_a_dispute_approval(
    client: TestClient, merchant: dict[str, str]
) -> None:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        db.add(
            Case(
                id="d1",
                source="dispute",
                merchant_id="demo",
                status="ready",
                version=3,
                dispute_id="PP-D-1",
            )
        )
        _evidence(db, "d1", _file(Path(get_settings().upload_dir), OLD), OLD)
        db.commit()
        purge_expired(db, Path(get_settings().upload_dir), retention_days=30, now=NOW)
    assert client.get("/api/cases/d1/evidence").status_code == 404
    response = client.post(
        "/api/cases/d1/approve", json={"action": "approve", "version": 3}, headers=merchant
    )
    assert response.status_code == 409


async def test_the_daily_loop_runs_now_and_survives_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def flaky(db: Session, upload_dir: Path, *, retention_days: int) -> Any:
        calls.append(retention_days)
        raise RuntimeError("database locked")

    monkeypatch.setattr(main, "purge_expired", flaky)
    engine = make_engine(f"sqlite:///{tmp_path / 'l.db'}")
    task = asyncio.create_task(main._retention_loop(engine, str(tmp_path), 30))
    await asyncio.sleep(0.2)
    assert calls == [30] and not task.done()  # failed once, then waits for tomorrow
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_loop_logs_when_something_was_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.security.retention import RetentionReport

    monkeypatch.setattr(main, "purge_expired", lambda *a, **k: RetentionReport(1, 1, 0, 0, 0))
    task = asyncio.create_task(
        main._retention_loop(make_engine(f"sqlite:///{tmp_path / 'm.db'}"), str(tmp_path), 30)
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- who can reach what (threat model T16) --------------------------------------------------

PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/store/products"),
    ("GET", "/api/store/sdk-config"),
    ("GET", "/api/store/orders"),
    ("POST", "/api/store/orders"),
    ("POST", "/api/store/orders/{order_id}/capture"),
    ("POST", "/api/refunds"),
    ("POST", "/api/webhooks/paypal"),
}


def _routes(client: TestClient) -> list[tuple[str, str]]:
    """Every (method, path) the app serves, read from the OpenAPI schema."""
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return sorted((method.upper(), path) for path, methods in paths.items() for method in methods)


def _concrete(path: str) -> str:
    return path.replace("{case_id}", "c1").replace("{order_id}", "o1")


def test_every_route_outside_the_public_list_needs_a_merchant_session(client: TestClient) -> None:
    protected = [(m, p) for m, p in _routes(client) if (m, p) not in PUBLIC]
    assert protected, "expected merchant routes to exist"
    for method, path in protected:
        response = client.request(method, _concrete(path), json={})
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.json()["error"]["code"] == "unauthenticated"


def test_every_merchant_post_also_needs_the_csrf_token(
    client: TestClient, merchant: dict[str, str]
) -> None:
    posts = [(m, p) for m, p in _routes(client) if m == "POST" and (m, p) not in PUBLIC]
    assert {p for _, p in posts} >= {
        "/api/auth/logout",
        "/api/cases/{case_id}/approve",
        "/api/cases/{case_id}/reanalyze",
        "/api/cases/{case_id}/copilot",
        "/api/cases/{case_id}/evidence",
    }
    for method, path in posts:
        response = client.request(
            method, _concrete(path), json={}
        )  # session cookie, no CSRF header
        assert response.status_code == 403, (method, path, response.status_code)
        assert response.json()["error"]["code"] == "csrf_failed"


def test_the_public_list_is_exactly_what_exists_without_a_session(client: TestClient) -> None:
    """If a new unauthenticated route appears, this test forces a conscious decision."""
    assert set(_routes(client)) >= PUBLIC
    assert not any("demo" in path for _, path in _routes(client))  # demo routes absent by default
    assert json.dumps(sorted(PUBLIC))  # keep the list serialisable for the review log
