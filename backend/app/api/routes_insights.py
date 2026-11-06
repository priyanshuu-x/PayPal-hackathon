"""Read-only merchant numbers for the AG Studio Insights page."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, col, select

from app.api.deps import db_dep, merchant_dep
from app.db.models import Case, CheckResult
from app.paypal.orders import money
from app.security.auth import MerchantSession
from app.store_catalog import CURRENCY

router = APIRouter(prefix="/api/insights", tags=["insights"])

VERDICTS = ("MATCHES_RECORDS", "NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS")


def _start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _is_override(case: Case) -> bool:
    """The merchant went against the verdict."""
    return (case.verdict == "DOES_NOT_MATCH_RECORDS" and case.status == "approved") or (
        case.verdict == "MATCHES_RECORDS" and case.status == "declined"
    )


@router.get("/summary")
async def insights_summary(
    _: Annotated[MerchantSession, Depends(merchant_dep)],
    db: Annotated[Session, Depends(db_dep)],
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    query = select(Case)
    if from_:
        query = query.where(col(Case.created_at) >= _start(from_))
    if to:  # inclusive: everything before the start of the next day
        query = query.where(col(Case.created_at) < _start(to + timedelta(days=1)))
    cases = db.exec(query).all()

    by_verdict = dict.fromkeys(VERDICTS, 0)
    caught = Decimal("0")
    overrides = 0
    for case in cases:
        if case.verdict in by_verdict:
            by_verdict[case.verdict] += 1
        # Money the checks flagged and the merchant did not pay out.
        if (
            case.verdict == "DOES_NOT_MATCH_RECORDS"
            and case.status != "approved"
            and case.amount
            and case.currency == CURRENCY
        ):
            caught += Decimal(case.amount)
        overrides += _is_override(case)

    days = {case.id: case.created_at.date().isoformat() for case in cases}
    counts: dict[tuple[str, str], int] = {}
    if days:
        mismatches = db.exec(
            select(CheckResult).where(
                CheckResult.result == "mismatch", col(CheckResult.case_id).in_(list(days))
            )
        ).all()
        for check in mismatches:
            key = (days[check.case_id], check.field)
            counts[key] = counts.get(key, 0) + 1

    return {
        "by_verdict": by_verdict,
        "amount_at_risk_caught": money(caught),
        "currency": CURRENCY,
        "merchant_overrides": overrides,
        "mismatch_types_by_day": [
            {"date": day, "field": field, "count": count}
            for (day, field), count in sorted(counts.items())
        ],
    }
