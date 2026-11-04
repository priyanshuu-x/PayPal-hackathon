"""Turn DB rows into the JSON shapes in docs/api-contract.md."""

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from app.db.models import AuditEvent, Case, CheckResult, Draft, Evidence
from app.security.audit import verify_chain


def iso_z(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def case_summary(case: Case) -> dict[str, Any]:
    return {
        "id": case.id,
        "source": case.source,
        "status": case.status,
        "verdict": case.verdict,
        "risk_score": case.risk_score,
        "amount": case.amount,
        "currency": case.currency,
        "paypal_order_id": case.paypal_order_id,
        "created_at": iso_z(case.created_at),
        "version": case.version,
    }


def _dispute(case: Case) -> dict[str, Any] | None:
    if case.source != "dispute":
        return None
    return {
        "reason": case.dispute_reason,
        "status": case.dispute_status,
        "response_due": case.dispute_due,
    }


def _evidence_count(db: Session, case: Case) -> int:
    return len(db.exec(select(Evidence).where(Evidence.case_id == case.id)).all())


def _forensic_notes(db: Session, case: Case) -> list[str]:
    evidence = db.exec(
        select(Evidence).where(Evidence.case_id == case.id).order_by(col(Evidence.id))
    ).first()
    if evidence is None or not evidence.forensics_json:
        return []
    return [str(s["note"]) for s in json.loads(evidence.forensics_json)]


def case_detail(db: Session, case: Case) -> dict[str, Any]:
    checks = db.exec(
        select(CheckResult).where(CheckResult.case_id == case.id).order_by(col(CheckResult.id))
    ).all()
    draft = db.exec(
        select(Draft).where(Draft.case_id == case.id).order_by(col(Draft.id).desc()).limit(1)
    ).first()
    audit = db.exec(
        select(AuditEvent).where(AuditEvent.case_id == case.id).order_by(col(AuditEvent.id))
    ).all()
    return {
        **case_summary(case),
        "capture_id": case.capture_id,
        "refund_id": case.refund_id,
        "dispute": _dispute(case),
        "evidence_count": _evidence_count(db, case),
        "dispute_id": case.dispute_id,
        "checks": [
            {
                "field": c.field,
                "claimed": c.claimed,
                "actual": c.actual,
                "result": c.result,
                "severity": c.severity,
                "note": c.note,
            }
            for c in checks
        ],
        "forensic_notes": _forensic_notes(db, case),
        "paypal_record": json.loads(case.paypal_record_json) if case.paypal_record_json else None,
        "draft": None
        if draft is None
        else {
            "text": draft.text,
            "grounded": draft.grounded,
            "unsupported_values": json.loads(draft.unsupported_values_json),
            "edited_by_human": draft.edited_by_human,
        },
        "audit": [
            {"at": e.created_at, "actor": e.actor, "action": e.action, "hash": e.hash}
            for e in audit
        ],
        "audit_chain_valid": verify_chain(db),
    }
