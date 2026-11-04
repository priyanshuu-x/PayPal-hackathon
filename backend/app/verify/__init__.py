"""Deterministic verification: claimed facts against PayPal's record. No LLM anywhere in here."""

from app.verify.models import VerifyInput, VerifyResult
from app.verify.rules import build_reasons, decide_verdict, evaluate
from app.verify.scoring import risk_score


def verify_case(inp: VerifyInput) -> VerifyResult:
    checks = evaluate(inp)
    verdict = decide_verdict(checks)
    return VerifyResult(
        verdict=verdict,
        risk_score=risk_score(checks, verdict, inp.forensic_risk),
        checks=checks,
        reasons=build_reasons(checks),
    )


__all__ = ["VerifyInput", "VerifyResult", "verify_case"]
