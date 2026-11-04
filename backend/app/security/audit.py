"""Tamper-evident audit log: each event's hash covers the previous event's hash.

`append_event` only flushes. The caller commits it in the same transaction as the state change
it records, so a change and its audit event succeed or fail together.
"""

import hashlib
import json
from typing import Any

from sqlmodel import Session, col, select

from app.db.models import AuditEvent, utcnow_iso
from app.security.redact import redact

GENESIS_HASH = "0" * 64


def _payload(
    case_id: str | None, actor: str, action: str, details_json: str, created_at: str
) -> str:
    return json.dumps(
        [case_id, actor, action, details_json, created_at],
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append_event(
    session: Session,
    *,
    case_id: str | None,
    actor: str,
    action: str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add one event to the chain. Details are redacted before they are stored."""
    details_json = json.dumps(redact(details or {}), sort_keys=True, separators=(",", ":"))
    last = session.exec(select(AuditEvent).order_by(col(AuditEvent.id).desc()).limit(1)).first()
    prev_hash = last.hash if last is not None else GENESIS_HASH
    created_at = utcnow_iso()
    event = AuditEvent(
        case_id=case_id,
        actor=actor,
        action=action,
        details_json=details_json,
        created_at=created_at,
        prev_hash=prev_hash,
        hash=_hash(prev_hash, _payload(case_id, actor, action, details_json, created_at)),
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session) -> bool:
    """True when every event links to its predecessor and its own hash recomputes."""
    expected_prev = GENESIS_HASH
    for event in session.exec(select(AuditEvent).order_by(col(AuditEvent.id))):
        payload = _payload(
            event.case_id, event.actor, event.action, event.details_json, event.created_at
        )
        if event.prev_hash != expected_prev or event.hash != _hash(event.prev_hash, payload):
            return False
        expected_prev = event.hash
    return True
