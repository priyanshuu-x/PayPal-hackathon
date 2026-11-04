"""Retention (threat model T15): uploaded files and personal data do not outlive RETENTION_DAYS.

What is removed after the window: the stored upload files, the text extracted from them, the
forensic notes, the buyer's reason and email, the payer's name in the PayPal record snapshot, and
the Copilot's question history. What stays: the case rows, checks, verdicts and the audit chain
(which holds no personal data), so the history of decisions is still verifiable.

Run daily by the API process and, if you prefer a Render cron job, with:
    python -m app.security.retention
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.models import Case, CopilotTurn, Evidence
from app.security.audit import append_event

logger = logging.getLogger(__name__)

PURGED = "purged"  # storage_key of an upload whose file has been deleted
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class RetentionReport:
    evidence_purged: int
    files_deleted: int
    orphan_files_deleted: int
    cases_scrubbed: int
    copilot_turns_deleted: int

    @property
    def changed(self) -> bool:
        return any(asdict(self).values())


def _scrub_snapshot(raw: str | None) -> str | None:
    """The record snapshot without the payer's name. Returns the same text when nothing to do."""
    if not raw:
        return raw
    try:
        snapshot = json.loads(raw)
    except ValueError:
        return None  # unreadable: drop it rather than keep something we cannot inspect
    if isinstance(snapshot, dict) and snapshot.get("payer_name") is not None:
        snapshot["payer_name"] = None
        return json.dumps(snapshot)
    return raw


def purge_expired(
    db: Session, upload_dir: Path, *, retention_days: int, now: datetime | None = None
) -> RetentionReport:
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(days=retention_days)
    cutoff_naive = cutoff.replace(tzinfo=None)  # the database stores naive UTC timestamps

    purged = deleted = 0
    for evidence in db.exec(
        select(Evidence).where(Evidence.created_at < cutoff_naive, Evidence.storage_key != PURGED)
    ):
        path = upload_dir / evidence.storage_key
        if _KEY_RE.fullmatch(evidence.storage_key) and path.is_file():
            path.unlink()
            deleted += 1
        evidence.storage_key = PURGED
        evidence.extracted_json = None
        evidence.forensics_json = None
        db.add(evidence)
        purged += 1

    scrubbed = 0
    for case in db.exec(select(Case).where(Case.created_at < cutoff_naive)):
        snapshot = _scrub_snapshot(case.paypal_record_json)
        if case.reason or case.buyer_email or snapshot != case.paypal_record_json:
            case.reason = None
            case.buyer_email = None
            case.paypal_record_json = snapshot
            db.add(case)
            scrubbed += 1

    turns = db.execute(delete(CopilotTurn).where(col(CopilotTurn.created_at) < cutoff_naive))
    db.flush()

    referenced = {key for key in db.exec(select(Evidence.storage_key))}
    orphans = 0
    if upload_dir.is_dir():
        for path in upload_dir.iterdir():
            expired = datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff
            if (
                path.is_file()
                and _KEY_RE.fullmatch(path.name)
                and path.name not in referenced
                and expired
            ):
                path.unlink()
                orphans += 1

    report = RetentionReport(
        evidence_purged=purged,
        files_deleted=deleted,
        orphan_files_deleted=orphans,
        cases_scrubbed=scrubbed,
        copilot_turns_deleted=int(turns.rowcount or 0),  # type: ignore[attr-defined]
    )
    if report.changed:
        append_event(
            db, case_id=None, actor="system", action="retention_run", details=asdict(report)
        )
    db.commit()
    return report


def main() -> None:  # pragma: no cover  (thin wrapper for a cron job)
    from app.config import get_settings
    from app.db.session import make_engine

    settings = get_settings()
    engine = make_engine(settings.database_url)
    with Session(engine) as db:
        report = purge_expired(
            db, Path(settings.upload_dir), retention_days=settings.retention_days
        )
    print(json.dumps(asdict(report)))


if __name__ == "__main__":  # pragma: no cover
    main()
