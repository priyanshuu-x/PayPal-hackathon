"""Writes eval reports: `<timestamp>.json`, `latest.json` (served by GET /api/eval/latest) and a
Markdown summary. Quick runs go to `quick-latest.*` so they never replace the published report."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _fmt(value: Any) -> str:
    """Rates (0 to 1) as percentages."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.1%}"
    return str(value)


def _secs(value: Any) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def markdown(report: dict[str, Any]) -> str:
    meta, s = report["meta"], report["summary"]
    lines = [
        f"# ProofCheck eval: {meta['split']}{' (quick)' if meta['quick'] else ''}",
        "",
        f"- Run: {meta['created_at']} · {s['cases']} cases · data: {meta['data_source']}",
        f"- Extraction model: `{meta['vision_model']}` · prompt `{meta['extract_prompt']}`",
        f"- Draft model: `{meta['text_model']}` · prompt `{meta['draft_prompt']}`",
    ]
    if meta["llm"] == "oracle":
        lines.append(
            "- **Oracle mode:** extraction returned the ground truth. These numbers test the "
            "pipeline and verify engine, not the model."
        )
    lines += [
        "",
        "## Targets (plan.md §9.3)",
        "",
        "| Metric | Value | Target | Met |",
        "|---|---|---|---|",
    ]
    for key, t in report["targets"].items():
        met = {True: "yes", False: "**no**", None: "n/a"}[t["met"]]
        value = _secs(t["value"]) if key.endswith("_s") else _fmt(t["value"])
        lines.append(f"| {key} | {value} | {t['target']} | {met} |")
    rule = report["merge_rule"]
    lines += [
        "",
        f"## Merge rule: {'pass' if rule['passed'] else '**FAIL**'}",
        "",
        *(f"- {k}: {_fmt(v)}" for k, v in rule["checks"].items()),
        "",
        "## Verdict accuracy by bucket",
        "",
        "| Bucket | Accuracy |",
        "|---|---|",
        *(f"| {b} | {_fmt(v)} |" for b, v in s["verdict_accuracy_by_bucket"].items()),
        "",
        "## Confusion matrix (rows: expected, columns: got)",
        "",
        "| Expected | MATCHES | NEEDS_REVIEW | DOES_NOT_MATCH | ERROR |",
        "|---|---|---|---|---|",
        *(
            f"| {exp} | " + " | ".join(str(row[v]) for v in row) + " |"
            for exp, row in s["confusion_matrix"].items()
        ),
        "",
        "## Field extraction accuracy",
        "",
        *(f"- {k}: {_fmt(v)}" for k, v in s["field_accuracy"].items()),
        "",
        f"Mismatch detection: precision {_fmt(s['mismatch_detection']['precision'])}, "
        f"recall {_fmt(s['mismatch_detection']['recall'])}, "
        f"F1 {_fmt(s['mismatch_detection']['f1'])}.",
        f"Latency p50 {_secs(s['latency_s']['p50'])} s, p95 {_secs(s['latency_s']['p95'])} s. "
        f"Model calls: {s['llm_calls']['extract']} extraction, {s['llm_calls']['draft']} drafting.",
    ]
    if s.get("copilot"):
        c = s["copilot"]
        lines += [
            "",
            "## Case Copilot",
            "",
            f"- Answer accuracy: {_fmt(c['answer_accuracy'])}",
            f"- Write attempts refused: {_fmt(c['write_attempts_refused'])}; "
            f"executed: {c['write_attempts_executed']} (must be 0)",
        ]
    return "\n".join(lines) + "\n"


def latest_name(*, quick: bool, oracle: bool) -> str:
    """Only full runs with the real model become `latest.json`, the published report."""
    if oracle:
        return "oracle-latest.json"
    return "quick-latest.json" if quick else "latest.json"


def write(report: dict[str, Any], reports_dir: Path, *, quick: bool, oracle: bool = False) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2) + "\n"
    if quick or oracle:
        target = reports_dir / latest_name(quick=quick, oracle=oracle)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (reports_dir / f"{stamp}.json").write_text(text, encoding="utf-8")
        target = reports_dir / "latest.json"
    target.write_text(text, encoding="utf-8")
    target.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return target


def previous(reports_dir: Path, *, quick: bool, oracle: bool = False) -> dict[str, Any] | None:
    path = reports_dir / latest_name(quick=quick, oracle=oracle)
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (FileNotFoundError, ValueError):
        return None
