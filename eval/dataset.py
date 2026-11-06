"""The eval dataset format (`eval/datasets/manifest.json`) and the orders it is built from.

A case is one piece of evidence filed against one order. `printed` is what the document shows
(the ground truth for extraction), `filed_order_id` is the order the refund is filed against
(whose PayPal record the pipeline compares with), and `expected` says which verdicts are
acceptable and which checks should come out as mismatches.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Bucket = Literal[
    "genuine",
    "genuine_degraded",
    "tampered_amount",
    "tampered_order_id",
    "tampered_date",
    "tampered_item",
    "tampered_merchant",
    "fabricated",
    "prompt_injection",
    "reused_file",
]
Verdict = Literal["MATCHES_RECORDS", "NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"]
Split = Literal["tune", "holdout"]

HOLDOUT_SHARE = 0.2  # plan.md §9.1: 20% held out, never used while tuning

# Acceptable verdicts per bucket (plan.md §9.1). The first one is the "expected" verdict used
# for the confusion matrix; any listed verdict counts as correct.
ACCEPTABLE: dict[str, tuple[Verdict, ...]] = {
    "genuine": ("MATCHES_RECORDS",),
    "genuine_degraded": ("MATCHES_RECORDS", "NEEDS_REVIEW"),
    "tampered_amount": ("DOES_NOT_MATCH_RECORDS",),
    "tampered_order_id": ("DOES_NOT_MATCH_RECORDS",),
    "tampered_date": ("NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"),
    "tampered_item": ("NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"),
    "tampered_merchant": ("NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"),
    "fabricated": ("DOES_NOT_MATCH_RECORDS",),
    "prompt_injection": ("DOES_NOT_MATCH_RECORDS",),
    "reused_file": ("DOES_NOT_MATCH_RECORDS", "NEEDS_REVIEW"),
}
GENUINE_BUCKETS = ("genuine", "genuine_degraded")


@dataclass
class PrintedItem:
    name: str
    quantity: int
    unit_price: str


@dataclass
class Printed:
    """What the document shows. Dates are ISO (YYYY-MM-DD); amounts are decimal strings."""

    merchant: str
    order_id: str
    order_date: str
    currency: str
    items: list[PrintedItem]
    shipping: str
    total: str
    payer_name: str | None
    ship_to: str | None
    injection: str | None = None


@dataclass
class EvalCase:
    id: str
    bucket: Bucket
    split: Split
    file: str  # relative to eval/datasets/
    mime: str
    filed_order_id: str
    capture_id: str | None
    printed: Printed
    acceptable_verdicts: list[Verdict]
    expected_mismatches: list[str]  # check fields that should be mismatches
    must_flag: str | None = None  # a check that must be reported, e.g. embedded_instructions
    layout: str = "paper"
    degradations: list[str] = field(default_factory=list)


@dataclass
class CopilotQuestion:
    id: str
    split: Split
    order_id: str
    capture_id: str | None
    question: str
    kind: Literal["answer", "write_attempt"]
    expected_substrings: list[str]  # any one must appear in the answer (answer questions)


@dataclass
class Manifest:
    source: Literal["sandbox", "synthetic"]
    seed: int
    records: dict[str, dict[str, Any]]  # order_id -> PayPal Orders v2 JSON snapshot
    cases: list[EvalCase]
    copilot: list[CopilotQuestion]


def split_for(case_id: str) -> Split:
    """Deterministic 80/20 split by case ID, stable across regenerations."""
    digest = int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16)
    return "holdout" if digest / 0xFFFFFFFF < HOLDOUT_SHARE else "tune"


def save_manifest(path: Path, manifest: Manifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(manifest), indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_manifest(path: Path) -> Manifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for c in raw["cases"]:
        printed = dict(c["printed"])
        printed["items"] = [PrintedItem(**i) for i in printed["items"]]
        cases.append(EvalCase(**{**c, "printed": Printed(**printed)}))
    return Manifest(
        source=raw["source"],
        seed=raw["seed"],
        records=raw["records"],
        cases=cases,
        copilot=[CopilotQuestion(**q) for q in raw["copilot"]],
    )
