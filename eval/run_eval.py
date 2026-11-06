"""Run the eval: the real analysis pipeline over the labelled cases, then metrics and a report.

    make eval-quick                         # 20 stratified tuning cases; paste before/after in PRs
    make eval                               # every case; writes eval/reports/latest.json
    python eval/run_eval.py --split holdout # the held-out 20%: report only, never tune on it
    python eval/run_eval.py --llm oracle    # offline: extraction returns the ground truth
    python eval/run_eval.py --copilot       # also ask the Case Copilot (live sandbox + Groq)

Each case runs `app.workflows.pipeline.analyze_case` (extraction, forensics, verify, drafting)
against a throwaway SQLite database. PayPal records come from the manifest's snapshots through
a read-only stand-in, so the run never calls or writes to PayPal; only the model calls are real.
Build the cases first with `python eval/generate_cases.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import _paths

# isort: split

import httpx
from app.config import SANDBOX_BASE_URL, Settings, get_settings
from app.copilot.toolkit import READ_ONLY_TOOLS
from app.db.models import Case, CheckResult, Draft, Evidence
from app.db.session import create_db_and_tables, make_engine
from app.drafting.prompts import DRAFT_VERSION
from app.extraction.prompts import EXTRACT_VERSION
from app.llm.groq_client import GROQ_BASE_URL, TIMEOUT, GroqClient
from app.paypal.client import PayPalClient
from app.security.uploads import store_file
from app.workflows.pipeline import analyze_case
from sqlmodel import Session, col, select

import metrics
import report
from dataset import EvalCase, Manifest, load_manifest
from fakes import CountingTransport, OracleLLM, RecordedPayPal
from sampling import Progress, ProviderLimit, run_key, sample_proportional

MANIFEST = _paths.DATASETS / "manifest.json"
QUICK_SIZE = 20


def select_cases(
    manifest: Manifest, split: str, quick: bool, limit: int | None, size: int | None = None
) -> list[EvalCase]:
    pool = [c for c in manifest.cases if split == "all" or c.split == split]
    if quick:
        pool = stratified([c for c in manifest.cases if c.split == "tune"], QUICK_SIZE)
    elif size:
        pool = sample_proportional(pool, size)
    if limit:
        pool = pool[:limit]
    # Reused-file cases last, so the file they reuse is already in the database.
    return sorted(pool, key=lambda c: c.bucket == "reused_file")


def stratified(cases: list[EvalCase], size: int) -> list[EvalCase]:
    """Round-robin over buckets, in ID order, so every bucket is represented."""
    by_bucket: dict[str, list[EvalCase]] = {}
    for c in sorted(cases, key=lambda c: c.id):
        by_bucket.setdefault(c.bucket, []).append(c)
    picked: list[EvalCase] = []
    while len(picked) < size and any(by_bucket.values()):
        for bucket in sorted(by_bucket):
            if by_bucket[bucket] and len(picked) < size:
                picked.append(by_bucket[bucket].pop(0))
    return picked


class Runner:
    def __init__(
        self,
        manifest: Manifest,
        settings: Settings,
        *,
        oracle: bool,
        datasets_dir: Path = _paths.DATASETS,
    ) -> None:
        self.datasets_dir = datasets_dir
        self.manifest = manifest
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        create_db_and_tables(self.engine)
        self.upload_dir = Path(settings.upload_dir)
        self.paypal_stub = RecordedPayPal(manifest.records)
        self.paypal = PayPalClient(
            settings, http=httpx.AsyncClient(base_url=SANDBOX_BASE_URL, transport=self.paypal_stub)
        )
        self.oracle = OracleLLM() if oracle else None
        inner: httpx.AsyncBaseTransport = self.oracle or httpx.AsyncHTTPTransport()
        self.counter = CountingTransport(inner)
        self.groq = GroqClient(
            settings,
            http=httpx.AsyncClient(base_url=GROQ_BASE_URL, timeout=TIMEOUT, transport=self.counter),
        )
        self.files_in_db: set[str] = set()

    async def close(self) -> None:
        await self.paypal.aclose()
        await self.groq.aclose()
        self.engine.dispose()  # releases the SQLite file (Windows cannot delete an open file)

    def _insert(self, case: EvalCase, data: bytes, *, analysed: bool = True) -> str:
        case_id = (
            f"{case.id}-{uuid.uuid4().hex[:8]}" if analysed else f"prior-{uuid.uuid4().hex[:8]}"
        )
        record = self.manifest.records.get(case.filed_order_id)
        amount = record["purchase_units"][0]["amount"] if record else None
        sha = hashlib.sha256(data).hexdigest()
        with Session(self.engine) as db:
            db.add(
                Case(
                    id=case_id,
                    source="refund",
                    merchant_id="eval",
                    paypal_order_id=case.filed_order_id,
                    capture_id=case.capture_id,
                    amount=amount["value"] if amount else None,
                    currency=amount["currency_code"] if amount else None,
                    status="new" if analysed else "declined",
                )
            )
            db.commit()
            key = store_file(self.upload_dir, data)
            db.add(
                Evidence(
                    case_id=case_id,
                    storage_key=key,
                    sha256=sha,
                    mime=case.mime,
                    size_bytes=len(data),
                )
            )
            db.commit()
        self.files_in_db.add(sha)
        return case_id

    async def run_case(self, case: EvalCase) -> metrics.CaseResult:
        data = (self.datasets_dir / case.file).read_bytes()
        if (
            case.bucket == "reused_file"
            and hashlib.sha256(data).hexdigest() not in self.files_in_db
        ):
            self._insert(case, data, analysed=False)  # an earlier case that used the same file
        case_id = self._insert(case, data)
        if self.oracle:
            self.oracle.current = case
        before = dict(self.counter.calls)
        started = time.perf_counter()
        error = None
        try:
            await analyze_case(
                case_id,
                settings=self.settings,
                engine=self.engine,
                paypal=self.paypal,
                groq=self.groq,
            )
        except Exception as exc:  # report and carry on; a crash is a result too
            error = type(exc).__name__
        latency = time.perf_counter() - started
        calls = {k: self.counter.calls[k] - before.get(k, 0) for k in self.counter.calls}

        with Session(self.engine) as db:
            row = db.get(Case, case_id)
            checks = db.exec(select(CheckResult).where(CheckResult.case_id == case_id)).all()
            evidence = db.exec(select(Evidence).where(Evidence.case_id == case_id)).first()
            draft = db.exec(
                select(Draft).where(Draft.case_id == case_id).order_by(col(Draft.id).desc())
            ).first()
            if any(
                c.field == "extraction" and c.note.startswith("Analysis limit reached")
                for c in checks
            ):
                raise ProviderLimit(case.id)  # not a verdict about the document: do not score it
            verdict = row.verdict if row and row.status == "ready" and row.verdict else "ERROR"
            extracted = (
                json.loads(evidence.extracted_json)
                if evidence and evidence.extracted_json
                else None
            )
            return metrics.CaseResult(
                id=case.id,
                bucket=case.bucket,
                split=case.split,
                verdict=verdict,
                mismatches=sorted({c.field for c in checks if c.result == "mismatch"}),
                extracted=extracted,
                latency_s=round(latency, 3),
                extract_calls=calls.get("extract", 0),
                draft_calls=calls.get("draft", 0),
                draft_model_used=None if draft is None else draft.model_id is not None,
                error=error,
            )


async def run_copilot(
    manifest: Manifest, split: str, quick: bool
) -> list[metrics.CopilotResultRow]:
    """Live: the real toolkit tools against the sandbox and the real model. Read-only by design."""
    from app.copilot.agent import CaseContext, CaseCopilot, make_chat_model
    from app.copilot.toolkit import assert_read_only, build_toolkit_tools

    settings = get_settings()
    tools = build_toolkit_tools(settings)
    assert_read_only(tools)
    copilot = CaseCopilot(
        tools, make_chat_model(settings), max_tool_calls=settings.copilot_max_tool_calls,
        model_id=settings.groq_model_text,
    )  # fmt: skip
    questions = [q for q in manifest.copilot if split == "all" or q.split == split]
    if quick:
        questions = questions[:5] + [q for q in questions if q.kind == "write_attempt"][:2]
    rows = []
    for q in questions:
        record = manifest.records.get(q.order_id, {})
        amount = (record.get("purchase_units") or [{}])[0].get("amount", {})
        context = CaseContext(
            case_id=f"eval-{q.id}", status="ready", verdict=None, risk_score=None,
            amount=amount.get("value"), currency=amount.get("currency_code"),
            paypal_order_id=q.order_id, capture_id=q.capture_id, dispute_id=None, checks=(),
        )  # fmt: skip
        started = time.perf_counter()
        result = await copilot.ask(q.question, context, consume_budget=lambda: True)
        answer = result.answer.lower()
        rows.append(
            metrics.CopilotResultRow(
                id=q.id,
                kind=q.kind,
                split=q.split,
                answer=result.answer,
                tools_called=[t.name for t in result.tool_calls],
                refused_tools=list(result.refused),
                correct=any(s.lower() in answer for s in q.expected_substrings)
                if q.kind == "answer"
                else True,
                latency_s=round(time.perf_counter() - started, 3),
            )  # fmt: skip
        )
    return rows


async def run(args: argparse.Namespace) -> int:
    if not args.manifest.exists():
        print(f"{args.manifest} not found. Build it first: python eval/generate_cases.py")
        return 2
    manifest = load_manifest(args.manifest)
    cases = select_cases(manifest, args.split, args.quick, args.limit, args.size)
    base = get_settings()
    oracle = args.llm == "oracle"
    if not oracle and not base.groq_api_key:
        print(
            "GROQ_API_KEY is not set in .env. Set it, or run with --llm oracle "
            "(pipeline test only)."
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="proofcheck-eval-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        settings = Settings(
            database_url=f"sqlite:///{(work / 'eval.db').as_posix()}",
            upload_dir=str(work / "uploads"),
            paypal_client_id="eval",
            paypal_client_secret="eval",  # noqa: S106  (the PayPal stand-in ignores it)
            groq_api_key="oracle" if oracle else base.groq_api_key,
            groq_model_vision=base.groq_model_vision,
            groq_model_text=base.groq_model_text,
            daily_llm_call_cap=100_000,
            store_names=base.store_names,
        )
        runner = Runner(manifest, settings, oracle=oracle, datasets_dir=args.manifest.parent)
        results = []
        key = run_key(
            [c.id for c in cases], args.llm, base.groq_model_vision, EXTRACT_VERSION, DRAFT_VERSION
        )
        progress = Progress(_paths.REPORTS / f"progress-{key}.jsonl")
        done = progress.load() if args.resume else {}
        if not args.resume:
            progress.start_fresh()
        elif done:
            print(f"Resuming: {len(done)} of {len(cases)} cases already done.")
        try:
            for n, case in enumerate(cases, 1):
                if case.id in done:
                    results.append(done[case.id])
                    continue
                try:
                    result = await runner.run_case(case)
                except ProviderLimit:
                    print(
                        f"\nThe AI provider's usage limit was reached after {len(results)} of "
                        f"{len(cases)} cases. Progress is saved. Run the same command again "
                        "with --resume once the limit has cleared (Groq's free tier resets "
                        "gradually over 24 hours)."
                    )
                    return 3
                results.append(result)
                progress.append(result)
                mark = "ok" if result.verdict in case.acceptable_verdicts else "WRONG"
                extra = f"  ({result.error})" if result.error else ""
                print(
                    f"[{n:>3}/{len(cases)}] {case.id:<24} {result.verdict:<23} {mark}"
                    f"  {result.latency_s:.1f}s{extra}",
                    flush=True,
                )
        finally:
            await runner.close()
        if runner.paypal_stub.calls["refused"]:
            print(
                f"Note: the pipeline attempted {runner.paypal_stub.calls['refused']} "
                "PayPal write(s); all were refused."
            )

    by_id = {c.id: c for c in manifest.cases}
    summary = metrics.compute(by_id, results)
    if args.copilot:
        if manifest.source != "sandbox" or oracle:
            print("Skipping the Copilot: it needs sandbox orders and a real model.")
        else:
            rows = await run_copilot(manifest, args.split, args.quick)
            summary["copilot"] = metrics.compute_copilot(rows, READ_ONLY_TOOLS)
            summary["copilot_rows"] = [asdict(r) for r in rows]

    prev = report.previous(_paths.REPORTS, quick=args.quick, oracle=oracle)
    full: dict[str, Any] = {
        "meta": {
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "split": args.split if not args.quick else "tune",
            "quick": args.quick,
            "sample_size": len(cases),
            "full_dataset_size": len(manifest.cases),
            "llm": args.llm,
            "data_source": manifest.source,
            "vision_model": "oracle" if oracle else base.groq_model_vision,
            "text_model": "oracle" if oracle else base.groq_model_text,
            "extract_prompt": EXTRACT_VERSION,
            "draft_prompt": DRAFT_VERSION,
        },
        "summary": summary,
        "targets": metrics.check_targets(summary),
        "merge_rule": metrics.merge_rule(summary, prev["summary"] if prev else None),
        "previous": {
            k: prev["summary"].get(k)
            for k in ("false_alarm_rate_genuine", "injection_not_followed")
        }
        if prev
        else None,
        "cases": [asdict(r) for r in results],
    }
    path = report.write(full, _paths.REPORTS, quick=args.quick, oracle=oracle)
    progress.finish()
    s = summary
    print(
        f"\nverdict accuracy {s['verdict_accuracy']}, "
        f"false alarms (genuine) {s['false_alarm_rate_genuine']}, "
        f"injection not followed {s['injection_not_followed']}, "
        f"reported {s['injection_reported']}, "
        f"merge rule {'pass' if full['merge_rule']['passed'] else 'FAIL'}\nReport: {path}"
    )
    return 0 if full["merge_rule"]["passed"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--quick", action="store_true", help=f"{QUICK_SIZE} stratified tuning cases"
    )
    parser.add_argument("--split", choices=("all", "tune", "holdout"), default="all")
    parser.add_argument("--llm", choices=("groq", "oracle"), default="groq")
    parser.add_argument(
        "--copilot", action="store_true", help="also evaluate the Case Copilot (live)"
    )
    parser.add_argument("--limit", type=int, help="run only the first N selected cases")
    parser.add_argument(
        "--size",
        type=int,
        help="a stratified sample of about N cases (every bucket, both splits) instead of all",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue a run that stopped (provider limit or interruption) without redoing cases",
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
