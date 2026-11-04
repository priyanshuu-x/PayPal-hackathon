"""analyze_case: fetch PayPal's record, read the document, run forensics, compare, store.

A fixed sequence, not an agent (plan section 6). The verdict comes from verify/, never from a
model. Nothing here moves money. Drafting (A7) is added as a last step.

Idempotent: a case that is already `ready`, `approved` or `declined` is left alone, so a retry
by Render Workflows or a duplicate enqueue is harmless.
"""

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, func, update
from sqlmodel import Session, col, delete, select

from app.config import Settings
from app.db.models import Case, CheckResult, Draft, Evidence
from app.drafting.draft import draft_refund_note, facts_from_snapshot
from app.extraction.schema import ClaimedFacts
from app.extraction.service import ExtractionOutcome, run_extraction
from app.forensics.signals import analyze as analyze_forensics
from app.llm.budget import try_consume
from app.llm.groq_client import GroqClient
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.orders import get_order
from app.security.audit import append_event
from app.security.uploads import read_file
from app.verify import verify_case
from app.verify.models import CheckOutcome, PayPalRecord, VerifyInput
from app.verify.records import record_from_order
from app.verify.rules import decide_verdict
from app.verify.scoring import risk_score

logger = logging.getLogger(__name__)

CLAIMABLE = ("new", "error")  # statuses a worker may pick up
SYSTEM = "system"


def _now() -> datetime:
    return datetime.now(UTC)


def _claim(db: Session, case_id: str) -> Case | None:
    """Atomically move new/error to processing. None when someone else has it or it is done."""
    result = db.execute(
        update(Case)
        .where(col(Case.id) == case_id, col(Case.status).in_(CLAIMABLE))
        .values(status="processing", version=Case.version + 1, updated_at=_now())
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        db.rollback()
        return None
    append_event(db, case_id=case_id, actor=SYSTEM, action="analysis_started")
    db.commit()
    return db.get(Case, case_id)


async def _load_record(case: Case, paypal: PayPalClient) -> tuple[PayPalRecord | None, bool]:
    """(record, lookup_failed). A 404 means the order really does not exist; any other PayPal
    failure means we could not check, which must never count as a pass."""
    if not case.paypal_order_id:
        return None, False
    try:
        order = await get_order(paypal, case.paypal_order_id)
        return record_from_order(order), False
    except PayPalError as exc:
        if exc.status_code == 404:
            return None, False
        logger.warning("record_lookup_failed", extra={"case_id": case.id})
        return None, True
    except (ValueError, KeyError):  # an order PayPal returned in a shape we cannot read
        logger.warning("record_unreadable", extra={"case_id": case.id})
        return None, True


def _extraction_problem(outcome: ExtractionOutcome) -> tuple[CheckOutcome, ...]:
    note = (outcome.reason or "could not read document").capitalize() + "."
    return (CheckOutcome("extraction", None, None, "mismatch", "major", note),)


def _record_snapshot(record: PayPalRecord) -> str:
    return json.dumps(
        {
            "order_id": record.order_id,
            "amount": str(record.total),
            "currency": record.currency,
            "created": record.created_at.isoformat().replace("+00:00", "Z"),
            "items": [
                {"name": i.name, "quantity": i.quantity, "unit_price": str(i.unit_price)}
                for i in record.items
            ],
            "payer_name": record.payer_name,
            "shipping_status": record.tracking_status,
            "tracking_number": record.tracking_number,
        }
    )


def _duplicate_count(db: Session, evidence: Evidence) -> int:
    return db.exec(
        select(func.count())
        .select_from(Evidence)
        .where(Evidence.sha256 == evidence.sha256, Evidence.case_id != evidence.case_id)
    ).one()


async def analyze_case(
    case_id: str,
    *,
    settings: Settings,
    engine: Engine,
    paypal: PayPalClient,
    groq: GroqClient,
) -> None:
    """Run the whole analysis for one case. Safe to call twice."""
    with Session(engine) as db:
        case = _claim(db, case_id)
        if case is None:
            return
        evidence = db.exec(
            select(Evidence).where(Evidence.case_id == case_id).order_by(col(Evidence.id))
        ).first()
        if evidence is None:
            _finish(db, case, (_problem("evidence", "No document is attached."),), 0, None, None)
            return

        record, lookup_failed = await _load_record(case, paypal)

        outcome: ExtractionOutcome | None = None
        facts = ClaimedFacts()
        if not lookup_failed:  # no point spending a model call when we cannot compare
            outcome = await run_extraction(db, evidence, settings, groq)
            if outcome.facts is not None:
                facts = outcome.facts

        report = None
        forensic_risk = 0
        try:
            data = read_file(Path(settings.upload_dir), evidence.storage_key)
            report = analyze_forensics(
                data,
                evidence.mime,
                claimed=facts,
                order_created_at=record.created_at if record else None,
            )
            forensic_risk = report.risk
        except FileNotFoundError:
            logger.warning("evidence_file_missing", extra={"case_id": case_id})

        if outcome is not None and outcome.facts is None:
            checks = _extraction_problem(outcome)
        else:
            checks = verify_case(
                VerifyInput(
                    claimed=facts,
                    record=record,
                    store_names=tuple(settings.store_name_list),
                    lookup_failed=lookup_failed,
                    duplicate_file_count=_duplicate_count(db, evidence),
                    forensic_risk=forensic_risk,
                )
            ).checks

        _finish(
            db,
            case,
            checks,
            forensic_risk,
            record,
            json.dumps([asdict(s) for s in report.signals]) if report else None,
            evidence=evidence,
        )
        if record is not None:
            await _store_draft(db, case, settings, groq)


async def _store_draft(db: Session, case: Case, settings: Settings, groq: GroqClient) -> None:
    """Write the refund note. A failure here never fails the analysis; the case simply has no
    draft and the merchant can still decide."""
    try:
        snapshot = json.loads(case.paypal_record_json or "null")
        facts = facts_from_snapshot(snapshot, case.capture_id) if snapshot else None
        if facts is None:
            return
        stores = settings.store_name_list
        result = await draft_refund_note(
            groq,
            model=settings.groq_model_text,
            facts=facts,
            store_name=stores[0] if stores else "our store",
            consume_budget=lambda: try_consume(db, settings.daily_llm_call_cap),
        )
        db.add(
            Draft(
                case_id=case.id,
                text=result.text,
                grounded=result.grounded,
                unsupported_values_json=json.dumps(list(result.unsupported)),
                prompt_version=result.prompt_version,
                model_id=result.model_id,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("draft_failed", extra={"case_id": case.id})


def _problem(field: str, note: str) -> CheckOutcome:
    return CheckOutcome(field, None, None, "mismatch", "major", note)


def _finish(
    db: Session,
    case: Case,
    checks: tuple[CheckOutcome, ...],
    forensic_risk: int,
    record: PayPalRecord | None,
    forensics_json: str | None,
    *,
    evidence: Evidence | None = None,
) -> None:
    verdict = decide_verdict(checks)
    score = risk_score(checks, verdict, forensic_risk)

    db.execute(delete(CheckResult).where(col(CheckResult.case_id) == case.id))
    for check in checks:
        db.add(
            CheckResult(
                case_id=case.id,
                field=check.field,
                claimed=check.claimed,
                actual=check.actual,
                result=check.result,
                severity=check.severity,
                note=check.note,
            )
        )
    if evidence is not None and forensics_json is not None:
        evidence.forensics_json = forensics_json
        db.add(evidence)

    case.verdict = verdict
    case.risk_score = score
    case.status = "ready"
    case.version += 1
    case.updated_at = _now()
    case.paypal_record_json = _record_snapshot(record) if record else None
    db.add(case)
    append_event(
        db,
        case_id=case.id,
        actor=SYSTEM,
        action="analysis_completed",
        details={"verdict": verdict, "risk_score": score, "checks": len(checks)},
    )
    db.commit()


def mark_failed(engine: Engine, case_id: str, error: BaseException) -> None:
    """Record that analysis crashed. Only the exception type is stored, never its message."""
    with Session(engine) as db:
        case = db.get(Case, case_id)
        if case is None or case.status != "processing":
            return
        case.status = "error"
        case.version += 1
        case.updated_at = _now()
        db.add(case)
        append_event(
            db,
            case_id=case_id,
            actor=SYSTEM,
            action="analysis_failed",
            details={"error": type(error).__name__},
        )
        db.commit()
