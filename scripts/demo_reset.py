"""Reset ProofCheck's demo data before recording the video.

    make demo-reset                              # dry run: shows what would be deleted
    python scripts/demo_reset.py --yes           # delete cases, uploads, audit log, webhook log
    python scripts/demo_reset.py --yes --buyer-orders   # also forget store orders from browsers

Deletes every case with its evidence files, checks, drafts and Copilot turns, the audit log and
the webhook dedupe log, from the database in `DATABASE_URL`. The audit chain then restarts from
its genesis hash with one `demo_reset` event, so "log verified" stays true.

Seeded sandbox orders (`scripts/seed_sandbox.py`) are kept, and nothing is sent to PayPal:
refunds and dispute evidence already sent in the sandbox stay there.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import _backend  # noqa: F401  (puts backend/ on sys.path)

# isort: split

from app.config import get_settings
from app.db.models import (
    AuditEvent,
    Case,
    CheckResult,
    CopilotTurn,
    Draft,
    Evidence,
    StoreOrder,
    WebhookEvent,
)
from app.db.session import create_db_and_tables, make_engine
from app.security.audit import append_event, verify_chain
from sqlmodel import Session, col, delete, func, select

from seed_sandbox import SEED_BUYER_ID


@dataclass
class ResetCounts:
    cases: int = 0
    evidence: int = 0
    files: int = 0
    checks: int = 0
    drafts: int = 0
    copilot_turns: int = 0
    audit_events: int = 0
    webhook_events: int = 0
    buyer_orders: int = 0


def _count(db: Session, model: type) -> int:
    return int(db.exec(select(func.count()).select_from(model)).one())


def count_rows(db: Session, *, buyer_orders: bool) -> ResetCounts:
    counts = ResetCounts(
        cases=_count(db, Case),
        evidence=_count(db, Evidence),
        checks=_count(db, CheckResult),
        drafts=_count(db, Draft),
        copilot_turns=_count(db, CopilotTurn),
        audit_events=_count(db, AuditEvent),
        webhook_events=_count(db, WebhookEvent),
    )
    if buyer_orders:
        counts.buyer_orders = int(
            db.exec(
                select(func.count())
                .select_from(StoreOrder)
                .where(StoreOrder.buyer_id != SEED_BUYER_ID)
            ).one()
        )
    return counts


def reset(db: Session, upload_dir: Path, *, buyer_orders: bool) -> ResetCounts:
    """Delete demo data and restart the audit chain. Caller has confirmed."""
    counts = count_rows(db, buyer_orders=buyer_orders)

    for key in db.exec(select(Evidence.storage_key)).all():
        path = upload_dir / key
        # Keys are random names the backend chose; never follow anything outside upload_dir.
        if path.resolve().parent == upload_dir.resolve() and path.is_file():
            path.unlink()
            counts.files += 1

    # Children first: they reference case.id.
    for model in (Evidence, CheckResult, Draft, CopilotTurn):
        db.exec(delete(model))  # type: ignore[call-overload]
    db.exec(delete(Case))  # type: ignore[call-overload]
    db.exec(delete(AuditEvent))  # type: ignore[call-overload]
    db.exec(delete(WebhookEvent))  # type: ignore[call-overload]
    if buyer_orders:
        db.exec(delete(StoreOrder).where(col(StoreOrder.buyer_id) != SEED_BUYER_ID))  # type: ignore[call-overload]
    db.commit()

    append_event(
        db,
        case_id=None,
        actor="system",
        action="demo_reset",
        details={k: v for k, v in asdict(counts).items() if v},
    )
    db.commit()
    return counts


def describe_database(url: str) -> str:
    """The database without credentials, for the confirmation message."""
    if url.startswith("sqlite"):
        return url
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or '?'}{parts.path}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    parser.add_argument(
        "--buyer-orders",
        action="store_true",
        help="also delete store orders placed from browsers (seeded orders are kept)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)
    with Session(engine) as db:
        counts = count_rows(db, buyer_orders=args.buyer_orders)
        print(f"Database: {describe_database(settings.database_url)}")
        for name, value in asdict(counts).items():
            if name != "files" and (name != "buyer_orders" or args.buyer_orders):
                print(f"  {name.replace('_', ' ')}: {value}")
        if not args.yes:
            print("Dry run. Run again with --yes to delete these.")
            return 0
        done = reset(db, Path(settings.upload_dir), buyer_orders=args.buyer_orders)
        print(
            f"Deleted. Removed {done.files} uploaded files. Audit chain valid: {verify_chain(db)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
