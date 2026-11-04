"""Daily cap on model calls (DAILY_LLM_CALL_CAP). Over the cap, cases become NEEDS_REVIEW."""

from datetime import UTC, datetime

from sqlmodel import Session

from app.db.models import LlmUsage


def try_consume(db: Session, cap: int) -> bool:
    """Count one model call for today (UTC). False, and no count, when the cap is reached."""
    day = datetime.now(UTC).date().isoformat()
    usage = db.get(LlmUsage, day)
    if usage is None:
        usage = LlmUsage(day=day, calls=0)
    if usage.calls >= cap:
        return False
    usage.calls += 1
    db.add(usage)
    db.commit()
    return True
