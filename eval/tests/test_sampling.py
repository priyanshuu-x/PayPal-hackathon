import random
from collections import Counter
from pathlib import Path

import pytest

import generate_cases as gen
import metrics
import run_eval
from dataset import load_manifest, save_manifest
from sampling import Progress, ProviderLimit, run_key, sample_proportional


@pytest.fixture(scope="module")
def full_manifest(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The real bucket sizes from plan.md section 9.1 (140 cases), built from fictional orders."""
    root = tmp_path_factory.mktemp("sampling")
    orders = gen.synthetic_orders(24, random.Random(2))  # noqa: S311
    manifest = gen.build(orders, seed=5, out_dir=root / "cases", scale=1.0)
    save_manifest(root / "manifest.json", manifest)
    return root, load_manifest(root / "manifest.json")


def test_the_full_dataset_has_the_planned_size(full_manifest) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    assert len(manifest.cases) == 140


@pytest.mark.parametrize("size", [100, 90, 60, 21, 10])
def test_a_sample_is_the_requested_size_and_keeps_every_bucket(full_manifest, size: int) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    picked = sample_proportional(manifest.cases, size)
    assert len(picked) == size and len({c.id for c in picked}) == size
    all_buckets = {c.bucket for c in manifest.cases}
    assert {c.bucket for c in picked} == all_buckets  # nothing is dropped entirely


def test_the_sample_is_proportional_and_keeps_both_splits(full_manifest) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    picked = sample_proportional(manifest.cases, 100)
    full = Counter(c.bucket for c in manifest.cases)
    got = Counter(c.bucket for c in picked)
    for bucket, count in full.items():
        assert abs(got[bucket] - count * 100 / 140) <= 1.0, bucket  # within one case
    holdout = sum(c.split == "holdout" for c in picked)
    assert 12 <= holdout <= 30  # the held-out share is still represented


def test_the_sample_is_deterministic_and_does_not_depend_on_input_order(full_manifest) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    first = [c.id for c in sample_proportional(manifest.cases, 100)]
    again = [c.id for c in sample_proportional(list(reversed(manifest.cases)), 100)]
    assert sorted(first) == sorted(again)


def test_asking_for_everything_or_more_returns_everything(full_manifest) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    assert len(sample_proportional(manifest.cases, 140)) == 140
    assert len(sample_proportional(manifest.cases, 999)) == 140


def test_select_cases_uses_the_sample_for_the_full_run_only(full_manifest) -> None:  # type: ignore[no-untyped-def]
    _, manifest = full_manifest
    assert len(run_eval.select_cases(manifest, "all", False, None, 100)) == 100
    assert len(run_eval.select_cases(manifest, "all", False, None, None)) == 140
    assert len(run_eval.select_cases(manifest, "all", True, None, 100)) == run_eval.QUICK_SIZE


def test_the_run_key_changes_with_the_cases_models_and_prompts() -> None:
    base = run_key(["a", "b"], "groq", "model-1", "EXTRACT_V1", "DRAFT_V1")
    assert base == run_key(["b", "a"], "groq", "model-1", "EXTRACT_V1", "DRAFT_V1")
    assert base != run_key(["a", "b", "c"], "groq", "model-1", "EXTRACT_V1", "DRAFT_V1")
    assert base != run_key(["a", "b"], "groq", "model-2", "EXTRACT_V1", "DRAFT_V1")
    assert base != run_key(["a", "b"], "groq", "model-1", "EXTRACT_V2", "DRAFT_V1")


def _result(case_id: str, verdict: str = "MATCHES_RECORDS") -> metrics.CaseResult:
    return metrics.CaseResult(
        case_id, "genuine", "tune", verdict, ["items"], {"order_id": "X"}, 1.5
    )


def test_progress_round_trips_and_can_be_cleared(tmp_path: Path) -> None:
    progress = Progress(tmp_path / "sub" / "progress.jsonl")
    assert progress.load() == {}
    progress.start_fresh()
    progress.append(_result("a"))
    progress.append(_result("b", "NEEDS_REVIEW"))
    done = progress.load()
    assert list(done) == ["a", "b"] and done["b"].verdict == "NEEDS_REVIEW"
    assert done["a"].extracted == {"order_id": "X"} and done["a"].mismatches == ["items"]
    progress.start_fresh()
    assert progress.load() == {}
    progress.append(_result("c"))
    progress.finish()
    assert not progress.path.exists()
    progress.finish()  # clearing twice is fine


# --- stop at the provider limit, then resume ------------------------------------------------


async def test_a_provider_limit_stops_the_run_with_progress_saved_and_resume_finishes_it(
    full_manifest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # type: ignore[no-untyped-def]
    root, manifest = full_manifest
    reports = tmp_path / "reports"
    monkeypatch.setattr(run_eval._paths, "REPORTS", reports)
    monkeypatch.setattr(run_eval.report, "previous", lambda *a, **k: None)
    calls: list[str] = []
    limit_at = {"id": None}

    async def fake_run_case(self, case):  # type: ignore[no-untyped-def]
        if case.id == limit_at["id"]:
            raise ProviderLimit(case.id)
        calls.append(case.id)
        return metrics.CaseResult(
            case.id, case.bucket, case.split, case.acceptable_verdicts[0], [], None, 0.1
        )

    monkeypatch.setattr(run_eval.Runner, "run_case", fake_run_case)

    args = ["--llm", "oracle", "--size", "30", "--manifest", str(root / "manifest.json")]
    chosen = run_eval.select_cases(manifest, "all", False, None, 30)
    limit_at["id"] = chosen[12].id

    code = await run_eval.run(run_eval.parse_args(args))
    out = capsys.readouterr().out
    assert code == 3 and len(calls) == 12
    assert "usage limit was reached after 12 of 30 cases" in out and "--resume" in out
    saved = list(reports.glob("progress-*.jsonl"))
    assert len(saved) == 1 and len(saved[0].read_text().splitlines()) == 12
    assert not list(reports.glob("*latest*"))  # no report from an incomplete run

    limit_at["id"] = None  # the limit has cleared
    calls.clear()
    code = await run_eval.run(run_eval.parse_args([*args, "--resume"]))
    out = capsys.readouterr().out
    assert "Resuming: 12 of 30 cases already done." in out
    assert len(calls) == 18 and not set(calls) & {c.id for c in chosen[:12]}  # no repeats
    assert not list(reports.glob("progress-*.jsonl"))  # cleaned up after a complete run
    assert (reports / "oracle-latest.json").exists()  # oracle runs are reported separately
    import json

    meta = json.loads((reports / "oracle-latest.json").read_text())["meta"]
    assert meta["sample_size"] == 30 and meta["full_dataset_size"] == 140
