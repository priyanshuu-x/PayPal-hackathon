"""A 0 to 100 risk score for sorting the case list. It never changes the verdict."""

from app.verify.models import CheckOutcome, Verdict

_POINTS = {"critical": 45, "major": 20, "minor": 5, "info": 0}
_BANDS: dict[Verdict, tuple[int, int]] = {
    "MATCHES_RECORDS": (0, 30),
    "NEEDS_REVIEW": (40, 69),
    "DOES_NOT_MATCH_RECORDS": (70, 100),
}
MAX_FORENSIC_RISK = 15


def risk_score(outcomes: tuple[CheckOutcome, ...], verdict: Verdict, forensic_risk: int) -> int:
    """Points from mismatches plus weak forensic signals, kept inside the verdict's band so the
    score and the verdict never disagree."""
    forensic = max(0, min(forensic_risk, MAX_FORENSIC_RISK))
    raw = sum(_POINTS[o.severity] for o in outcomes if o.result == "mismatch") + forensic
    low, high = _BANDS[verdict]
    return max(low, min(low + raw, high))
