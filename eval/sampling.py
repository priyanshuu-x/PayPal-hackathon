"""Choosing a smaller eval set, and saving progress so a run can stop and resume.

Why: the free Groq tier allows about 200,000 vision tokens a day, roughly 70 receipts. A
sample that keeps every bucket and the 20% held-out share is a fair, smaller eval; progress
files let a run stop cleanly when the daily limit is hit and carry on the next day without
losing or repeating work.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import metrics

if TYPE_CHECKING:
    from dataset import EvalCase


class ProviderLimit(Exception):
    """The AI provider's usage limit was reached: stop, save progress, resume later."""


def _stable_key(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()


def sample_proportional(cases: list[EvalCase], size: int) -> list[EvalCase]:
    """About `size` cases, each bucket in proportion to its share (largest remainder), at least
    one per bucket when the size allows. Within a bucket the cases are taken in a stable hash
    order, so the same input always gives the same sample, and both splits are represented."""
    if size >= len(cases):
        return list(cases)
    by_bucket: dict[str, list[EvalCase]] = {}
    for case in cases:
        by_bucket.setdefault(case.bucket, []).append(case)
    for members in by_bucket.values():
        members.sort(key=lambda c: _stable_key(c.id))

    total = len(cases)
    buckets = sorted(by_bucket)
    quotas = {b: len(by_bucket[b]) * size / total for b in buckets}
    take = {b: int(quotas[b]) for b in buckets}
    if size >= len(buckets):
        take = {b: max(take[b], 1) for b in buckets}
    # hand out the remaining places by largest fractional part, then trim any overshoot
    by_remainder = sorted(buckets, key=lambda b: (quotas[b] - int(quotas[b]), b), reverse=True)
    index = 0
    while sum(take.values()) < size:
        bucket = by_remainder[index % len(by_remainder)]
        if take[bucket] < len(by_bucket[bucket]):
            take[bucket] += 1
        index += 1
    by_size = sorted(buckets, key=lambda b: (take[b] - quotas[b], b), reverse=True)
    index = 0
    while sum(take.values()) > size:
        bucket = by_size[index % len(by_size)]
        if take[bucket] > 1:
            take[bucket] -= 1
        index += 1
    return [c for b in buckets for c in by_bucket[b][: take[b]]]


def run_key(case_ids: list[str], *parts: str) -> str:
    """Identifies a run: the exact cases plus the model and prompt versions. A progress file only
    resumes a run with the same key, so results from a different setup are never mixed in."""
    raw = json.dumps([sorted(case_ids), *parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class Progress:
    """One JSON line per finished case."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, metrics.CaseResult]:
        if not self.path.exists():
            return {}
        done: dict[str, metrics.CaseResult] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                data: dict[str, Any] = json.loads(line)
                done[data["id"]] = metrics.CaseResult(**data)
        return done

    def start_fresh(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def append(self, result: metrics.CaseResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(result)) + "\n")

    def finish(self) -> None:
        self.path.unlink(missing_ok=True)
