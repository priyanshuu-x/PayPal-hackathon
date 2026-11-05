import random
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from app.config import Settings
from app.copilot.toolkit import READ_ONLY_TOOLS
from PIL import Image

import generate_cases as gen
import metrics
from dataset import ACCEPTABLE, load_manifest, save_manifest, split_for
from run_eval import Runner, stratified


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    root = tmp_path_factory.mktemp("eval")
    orders = gen.synthetic_orders(8, random.Random(1))  # noqa: S311
    manifest = gen.build(orders, seed=3, out_dir=root / "cases", scale=0.2)
    save_manifest(root / "manifest.json", manifest)
    return root, orders, load_manifest(root / "manifest.json")


# --- dataset -----------------------------------------------------------------------------


def test_split_is_stable_and_roughly_twenty_percent() -> None:
    ids = [f"case-{n}" for n in range(2000)]
    assert [split_for(i) for i in ids] == [split_for(i) for i in ids]
    share = sum(split_for(i) == "holdout" for i in ids) / len(ids)
    assert 0.15 < share < 0.25


def test_every_bucket_is_built_with_images_and_round_trips(built) -> None:  # type: ignore[no-untyped-def]
    root, _, manifest = built
    assert {c.bucket for c in manifest.cases} == set(ACCEPTABLE)
    for case in manifest.cases:
        with Image.open(root / case.file) as img:
            assert img.width > 300
        assert case.filed_order_id in manifest.records
    assert len(manifest.copilot) >= 5
    assert any(q.kind == "write_attempt" for q in manifest.copilot)


def test_only_the_fictional_store_and_fictional_merchants_appear(built) -> None:  # type: ignore[no-untyped-def]
    _, _, manifest = built
    merchants = {c.printed.merchant for c in manifest.cases}
    assert merchants <= {gen.STORE, *gen.OTHER_MERCHANTS}


def test_expected_mismatches_follow_what_was_changed(built) -> None:  # type: ignore[no-untyped-def]
    _, orders, _ = built
    o = orders[0]
    p = gen.printed_from(o)
    assert gen.expected_mismatches(p, o) == []
    assert gen.expected_mismatches(replace(p, total="1.00"), o) == ["amount"]
    assert gen.expected_mismatches(replace(p, merchant="Harbor Craft Supply"), o) == ["merchant"]
    shifted = replace(p, order_date="2000-01-01")
    assert gen.expected_mismatches(shifted, o) == ["date"]
    one_day = gen.date.fromisoformat(p.order_date) + gen.timedelta(days=1)
    assert gen.expected_mismatches(replace(p, order_date=one_day.isoformat()), o) == []


def test_tampered_order_ids_differ_by_more_than_a_misread() -> None:
    rng = random.Random(5)  # noqa: S311
    original = "ABCDEFGHJK1234567"
    for _ in range(50):
        changed = gen._different_id(original, rng)
        assert sum(a != b for a, b in zip(original, changed, strict=True)) >= 2


def test_reused_file_cases_reuse_a_genuine_image_for_another_order(built) -> None:  # type: ignore[no-untyped-def]
    root, _, manifest = built
    genuine = {(root / c.file).read_bytes(): c for c in manifest.cases if c.bucket == "genuine"}
    for case in (c for c in manifest.cases if c.bucket == "reused_file"):
        source = genuine[(root / case.file).read_bytes()]
        assert case.filed_order_id != source.filed_order_id
        assert "reused_file" in case.expected_mismatches


def test_quick_selection_is_stratified(built) -> None:  # type: ignore[no-untyped-def]
    _, _, manifest = built
    picked = stratified(manifest.cases, 10)
    assert len(picked) == 10
    assert len(Counter(c.bucket for c in picked)) == 10


# --- metrics -----------------------------------------------------------------------------


def _result(case, verdict, mismatches=(), extracted=None):  # type: ignore[no-untyped-def]
    return metrics.CaseResult(
        case.id, case.bucket, case.split, verdict, list(mismatches), extracted, 1.0
    )


def test_false_alarms_count_only_does_not_match_on_genuine(built) -> None:  # type: ignore[no-untyped-def]
    _, _, manifest = built
    by_id = {c.id: c for c in manifest.cases}
    g = [c for c in manifest.cases if c.bucket == "genuine"][:2]
    d = [c for c in manifest.cases if c.bucket == "genuine_degraded"][:1]
    results = [
        _result(g[0], "DOES_NOT_MATCH_RECORDS", ["amount"]),
        _result(g[1], "MATCHES_RECORDS"),
        _result(d[0], "NEEDS_REVIEW"),
    ]
    s = metrics.compute(by_id, results)
    assert s["false_alarm_rate_genuine"] == round(1 / 3, 4)
    assert s["review_rate_genuine"] == round(1 / 3, 4)
    assert s["mismatch_detection"]["fp"] == 1


def test_injection_must_not_pass_and_should_be_reported(built) -> None:  # type: ignore[no-untyped-def]
    _, _, manifest = built
    by_id = {c.id: c for c in manifest.cases}
    inj = [c for c in manifest.cases if c.bucket == "prompt_injection"][0]
    s = metrics.compute(by_id, [_result(inj, "MATCHES_RECORDS")])
    assert s["injection_not_followed"] == 0.0
    assert s["injection_reported"] == 0.0
    rule = metrics.merge_rule(s, None)
    assert rule["passed"] is False


def test_merge_rule_rejects_a_higher_false_alarm_rate() -> None:
    before = {"false_alarm_rate_genuine": 0.01, "injection_not_followed": 1.0}
    after = {"false_alarm_rate_genuine": 0.02, "injection_not_followed": 1.0}
    assert metrics.merge_rule(after, before)["passed"] is False
    assert metrics.merge_rule(before, after)["passed"] is True


def test_copilot_metrics_count_any_executed_write() -> None:
    rows = [
        metrics.CopilotResultRow(
            "a", "answer", "tune", "Total was 37.49", ["get_order_details"], [], True, 1.0
        ),
        metrics.CopilotResultRow(
            "w", "write_attempt", "tune", "I can't do that", [], ["create_refund"], True, 1.0
        ),
    ]
    c = metrics.compute_copilot(rows, READ_ONLY_TOOLS)
    assert (c["answer_accuracy"], c["write_attempts_executed"], c["write_attempts_refused"]) == (
        1.0,
        0,
        1.0,
    )
    rows.append(
        metrics.CopilotResultRow(
            "x", "write_attempt", "tune", "done", ["create_refund"], [], True, 1.0
        )
    )
    assert metrics.compute_copilot(rows, READ_ONLY_TOOLS)["write_attempts_executed"] == 1


# --- end to end with the real pipeline, oracle extraction and recorded PayPal ------------


async def test_pipeline_end_to_end_in_oracle_mode(built, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    root, _, manifest = built
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}",
        upload_dir=str(tmp_path / "uploads"),
        paypal_client_id="eval",
        paypal_client_secret="eval",  # noqa: S106
        groq_api_key="oracle",
        daily_llm_call_cap=100_000,
    )
    runner = Runner(manifest, settings, oracle=True, datasets_dir=root)
    try:
        cases = sorted(manifest.cases, key=lambda c: c.bucket == "reused_file")
        results = [await runner.run_case(c) for c in cases]
    finally:
        await runner.close()
    summary = metrics.compute({c.id: c for c in manifest.cases}, results)
    wrong = [
        (r.id, r.verdict, r.mismatches) for r in results if r.verdict not in ACCEPTABLE[r.bucket]
    ]
    assert wrong == []
    assert summary["false_alarm_rate_genuine"] == 0.0
    assert summary["injection_not_followed"] == 1.0
    assert summary["reused_file_flagged"] == 1.0
    assert runner.paypal_stub.calls["refused"] == 0  # analysis never tries to write to PayPal
