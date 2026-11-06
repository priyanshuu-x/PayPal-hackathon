import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api import routes_eval
from app.db.models import Case, CheckResult


def _case(case_id: str, day: int, verdict: str | None, status: str, amount: str = "10.00") -> Case:
    return Case(
        id=case_id,
        source="refund",
        merchant_id="m",
        status=status,
        verdict=verdict,
        amount=amount,
        currency="USD",
        created_at=datetime(2026, 11, day, 12, 0, tzinfo=UTC),
    )


def _mismatch(case_id: str, field: str, result: str = "mismatch") -> CheckResult:
    return CheckResult(case_id=case_id, field=field, result=result, severity="major")


@pytest.fixture
def seeded(client: TestClient) -> None:
    with Session(client.app.state.engine) as db:  # type: ignore[attr-defined]
        db.add(_case("a", 1, "DOES_NOT_MATCH_RECORDS", "ready", "19.99"))
        db.add(_case("b", 1, "DOES_NOT_MATCH_RECORDS", "declined", "10.01"))
        db.add(_case("c", 2, "DOES_NOT_MATCH_RECORDS", "approved", "50.00"))  # paid out anyway
        db.add(_case("d", 2, "MATCHES_RECORDS", "declined"))
        db.add(_case("e", 3, "NEEDS_REVIEW", "ready"))
        db.add(_case("f", 3, None, "processing"))
        db.commit()
        db.add(_mismatch("a", "amount"))
        db.add(_mismatch("a", "order"))
        db.add(_mismatch("b", "amount"))
        db.add(_mismatch("b", "date", result="match"))
        db.add(_mismatch("e", "items"))
        db.commit()


def test_summary_needs_a_merchant(client: TestClient) -> None:
    assert client.get("/api/insights/summary").status_code == 401


@pytest.mark.usefixtures("seeded", "merchant")
def test_summary_counts_verdicts_money_overrides_and_mismatch_types(client: TestClient) -> None:
    body = client.get("/api/insights/summary").json()
    assert body == {
        "by_verdict": {"MATCHES_RECORDS": 1, "NEEDS_REVIEW": 1, "DOES_NOT_MATCH_RECORDS": 3},
        "amount_at_risk_caught": "30.00",
        "currency": "USD",
        "merchant_overrides": 2,
        "mismatch_types_by_day": [
            {"date": "2026-11-01", "field": "amount", "count": 2},
            {"date": "2026-11-01", "field": "order", "count": 1},
            {"date": "2026-11-03", "field": "items", "count": 1},
        ],
    }


@pytest.mark.usefixtures("seeded", "merchant")
def test_summary_filters_by_date_including_both_ends(client: TestClient) -> None:
    body = client.get("/api/insights/summary?from=2026-11-02&to=2026-11-03").json()
    assert body["by_verdict"]["DOES_NOT_MATCH_RECORDS"] == 1
    assert body["amount_at_risk_caught"] == "0.00"
    assert [r["date"] for r in body["mismatch_types_by_day"]] == ["2026-11-03"]


@pytest.mark.usefixtures("merchant")
def test_summary_is_zero_with_no_cases(client: TestClient) -> None:
    body = client.get("/api/insights/summary").json()
    assert body["amount_at_risk_caught"] == "0.00"
    assert body["mismatch_types_by_day"] == []


@pytest.mark.usefixtures("merchant")
def test_summary_rejects_a_malformed_date(client: TestClient) -> None:
    assert client.get("/api/insights/summary?from=yesterday").status_code == 422


def _report(path: Path, monkeypatch: pytest.MonkeyPatch, body: str | None) -> None:
    target = path / "latest.json"
    if body is not None:
        target.write_text(body, encoding="utf-8")
    monkeypatch.setattr(routes_eval, "LATEST_REPORT", target)


def test_eval_report_needs_a_merchant(client: TestClient) -> None:
    assert client.get("/api/eval/latest").status_code == 401


@pytest.mark.usefixtures("merchant")
def test_eval_report_is_served(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report: dict[str, Any] = {"meta": {"split": "all"}, "summary": {"cases": 140}}
    _report(tmp_path, monkeypatch, json.dumps(report))
    response = client.get("/api/eval/latest")
    assert response.status_code == 200
    assert response.json() == report


@pytest.mark.usefixtures("merchant")
@pytest.mark.parametrize("body", [None, "{not json"])
def test_missing_or_damaged_report_is_a_404(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str | None
) -> None:
    _report(tmp_path, monkeypatch, body)
    response = client.get("/api/eval/latest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
