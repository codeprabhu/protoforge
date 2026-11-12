"""The committed baseline artifacts must be self-consistent and provenance-complete.

These tests pin the claims in ``docs/research/VALID_BASELINE_REPORT.md`` to
tracked artifacts. Raw run directories are gitignored, so the tracked summary
must carry the provenance itself.

Two baselines are tracked:

* ``results/bench_baseline_v2/`` — pre-fix. Every first attempt failed
  compilation with ``Unable to bind wire/reg/memory HALF`` and only passed
  after repair. It is kept as evidence for the scaffold bug, not as a
  capability measurement.
* ``results/bench_baseline_v3/`` — the corrected baseline (commit 6f3fc15).
* ``results/bench_baseline_v4/`` — the current baseline (commit f8d204f),
  after the multi-byte scaffold fixes.

Targeted multi-byte validations are also tracked:

* ``results/bench_multibyte_v4/`` — after the structural fix, 0/8.
* ``results/bench_multibyte_v6/`` — after the payload-constant and retrieval
  fixes, 3/8. Retained as evidence that the dual payload-naming scheme was
  harness-owned; see ``docs/research/VALID_BASELINE_REPORT.md`` §4.5.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from bench.run import (
    INFRASTRUCTURE_CLASSIFICATIONS,
    classify_final_result,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "results" / "bench_baseline_v3"
PRE_FIX = ROOT / "results" / "bench_baseline_v2"
SINGLE_BYTE = {
    "c01-default-1byte",
    "c02-alt-address-1byte",
    "c03-zero-payload",
    "c04-all-ones-payload",
    "c05-low-address",
    "c09-single-device",
    "c10-three-devices",
    "c12-top-address",
}
MULTI_BYTE = {
    "c06-two-byte",
    "c07-two-byte-alt",
    "c08-three-byte",
    "c11-three-devices-two-byte",
}
MODEL = "qwen2.5-coder:7b"
MODEL_DIGEST = "dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364"


def _rows(directory: Path) -> list[dict[str, str]]:
    with (directory / "runs.csv").open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _index(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _summary() -> dict:
    return json.loads((BASELINE / "summary.json").read_text(encoding="utf-8"))


def test_artifacts_describe_the_same_24_runs() -> None:
    rows, index, summary = _rows(BASELINE), _index(BASELINE), _summary()

    assert len(rows) == 24
    assert len(index) == 24
    assert summary["rows"] == 24
    assert {row["run_id"] for row in rows} == {entry["run_id"] for entry in index}
    assert len(summary["cases"]) == 12
    # 12 cases x 2 samples, every case completed twice.
    per_case = Counter(row["case_id"] for row in rows)
    assert len(per_case) == 12
    assert set(per_case.values()) == {2}


def test_provenance_is_recorded_in_tracked_summary() -> None:
    summary = _summary()

    assert summary["model"] == MODEL
    assert summary["model_digest"] == MODEL_DIGEST
    assert len(summary["git_commit"]) == 40
    int(summary["git_commit"], 16)
    assert len(summary["retrieval_corpus_hash"]) == 64
    int(summary["retrieval_corpus_hash"], 16)
    assert summary["retrieval_memory_frozen"] is True
    assert summary["temperature"] == 0.25
    assert summary["max_repairs"] == 2
    assert summary["samples_per_case"] == 2
    assert summary["base_seed"] == 42
    assert summary["configs"]["on-on"]["model_invoked"] is True


def test_explicit_outcomes_match_raw_csv_classifications() -> None:
    rows = _rows(BASELINE)
    stats = _summary()["configs"]["on-on"]

    observed = Counter(
        classify_final_result(row["final"], row["error"]) for row in rows
    )
    expected = {name: observed.get(name, 0) for name in stats["explicit_outcomes"]}
    assert stats["explicit_outcomes"] == expected
    assert stats["outcomes"] == stats["explicit_outcomes"]
    # Every row records its own classification, not just the aggregate.
    assert all(row["explicit_classification"] for row in rows)


def test_batch_is_valid_and_model_was_invoked() -> None:
    rows, stats = _rows(BASELINE), _summary()["configs"]["on-on"]

    assert stats["batch_valid"] is True
    assert stats["batch_invalid_reason"] == "Batch valid"
    assert stats["model_invoked"] is True
    assert stats["runs"] == 24
    assert stats["candidate_runs"] == 24
    assert stats["runs_without_candidate"] == 0
    assert stats["infrastructure_failures"] == 0
    assert all(int(row["attempts"]) >= 1 for row in rows)
    assert not any(
        classify_final_result(row["final"], row["error"])
        in INFRASTRUCTURE_CLASSIFICATIONS
        for row in rows
    )


def test_first_attempt_and_repair_are_reported_separately() -> None:
    rows, stats = _rows(BASELINE), _summary()["configs"]["on-on"]

    first_attempt_passes = sum(row["first_attempt_pass"] == "True" for row in rows)
    final_passes = sum(row["final"] == "pass" for row in rows)
    recovered = sum(row["recovered_after_repair"] == "True" for row in rows)

    assert first_attempt_passes == stats["pass_at_1"] == 16
    assert final_passes == stats["pass_after_repair"] == 16
    assert recovered == stats["recovered_by_repair"] == 0
    assert stats["candidate_verification_failures"] == 8
    assert stats["successful_candidates"] == 16
    assert stats["distinct_first_designs"] == 22


def test_single_byte_and_multi_byte_splits() -> None:
    rows = _rows(BASELINE)

    assert {row["case_id"] for row in rows} == SINGLE_BYTE | MULTI_BYTE
    single = [row for row in rows if row["case_id"] in SINGLE_BYTE]
    multi = [row for row in rows if row["case_id"] in MULTI_BYTE]

    assert len(single) == 16
    assert len(multi) == 8
    # Corrected first attempt: single-byte passes on the first candidate.
    assert sum(row["first_attempt_pass"] == "True" for row in single) == 16
    assert sum(row["final"] == "pass" for row in single) == 16
    # Multi-byte remains the blocker, including after repair.
    assert sum(row["first_attempt_pass"] == "True" for row in multi) == 0
    assert sum(row["final"] == "pass" for row in multi) == 0
    assert sum(row["recovered_after_repair"] == "True" for row in multi) == 0


def test_pre_fix_baseline_kept_as_evidence_of_the_half_bug() -> None:
    """The pre-fix run must stay visible and must not be quoted as capability."""
    rows, summary = _rows(PRE_FIX), json.loads(
        (PRE_FIX / "summary.json").read_text(encoding="utf-8")
    )
    stats = summary["configs"]["on-on"]

    assert len(rows) == 24
    assert stats["pass_at_1"] == 0
    assert stats["pass_after_repair"] == 16
    assert stats["batch_valid"] is True
    # Every single-byte run needed repair; every first attempt failed.
    assert all(row["first_attempt_pass"] == "False" for row in rows)
    assert sum(row["recovered_after_repair"] == "True" for row in rows) == 16


def test_report_renders_the_same_summary() -> None:
    report = (BASELINE / "report.md").read_text(encoding="utf-8")
    stats = _summary()["configs"]["on-on"]

    assert "| Config |" in report
    assert f"{stats['pass_at_1']}/{stats['runs']}" in report
    assert f"{stats['pass_after_repair']}/{stats['runs']}" in report
    assert "✅" in report


MULTI_VALIDATION = ROOT / "results" / "bench_multibyte_v4"


def test_multibyte_validation_has_no_harness_owned_failures() -> None:
    """After the scaffold fix, multi-byte failures must be candidate failures.

    The pre-fix baseline charged two multi-byte structural failures to the
    model although the harness's own instance/driver code had been duplicated.
    The validation run (same corpus, commit 3954966) shows that class of
    failure is gone: every multi-byte failure is a compile/simulation failure.
    """
    rows = list(csv.DictReader((MULTI_VALIDATION / "runs.csv").open(encoding="utf-8")))
    summary = json.loads((MULTI_VALIDATION / "summary.json").read_text(encoding="utf-8"))
    stats = summary["configs"]["on-on"]

    assert len(rows) == 8
    assert {row["case_id"] for row in rows} == MULTI_BYTE
    assert stats["batch_valid"] is True
    assert stats["infrastructure_failures"] == 0
    assert stats["model_invoked"] is True
    assert summary["retrieval_corpus_hash"] == _summary()["retrieval_corpus_hash"]
    outcomes = Counter(classify_final_result(row["final"], row["error"]) for row in rows)
    assert outcomes.get("structural_failure", 0) == 0, outcomes
    assert outcomes.get("simulation_failure", 0) == 5, outcomes
    assert outcomes.get("compile_failure", 0) == 3, outcomes
    # Multi-byte is still unsolved; the point is that it fails honestly now.
    assert sum(row["final"] == "pass" for row in rows) == 0


MULTI_V4 = ROOT / "results" / "bench_multibyte_v4"
MULTI_V6 = ROOT / "results" / "bench_multibyte_v6"


def _multi(directory: Path) -> tuple[list[dict[str, str]], dict]:
    rows = list(csv.DictReader((directory / "runs.csv").open(encoding="utf-8")))
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    return rows, summary["configs"]["on-on"]


def test_multi_byte_v6_records_the_pre_scheme_fix_outcome() -> None:
    """v6 is retained evidence: 3/8, later traced to the dual naming scheme.

    These counts must not be quoted as model capability. The replay in
    ``docs/research/VALID_BASELINE_REPORT.md`` §4.5 shows 4 of the 8 runs were
    corrupted by the scaffold; the v4 baseline records the corrected outcome.
    """
    rows, stats = _multi(MULTI_V6)
    summary = json.loads((MULTI_V6 / "summary.json").read_text(encoding="utf-8"))

    assert len(rows) == 8
    assert {row["case_id"] for row in rows} == MULTI_BYTE
    assert summary["git_commit"] == "be4517f8a6ac44d369db7c456e9435c9d1f1ac33"
    assert summary["retrieval_corpus_hash"] == _summary()["retrieval_corpus_hash"]
    assert stats["batch_valid"] is True
    assert stats["infrastructure_failures"] == 0
    assert stats["model_invoked"] is True
    # Every remaining failure is a protocol/simulation failure.
    assert stats["explicit_outcomes"]["compile_failure"] == 0
    assert stats["explicit_outcomes"]["structural_failure"] == 0
    assert stats["explicit_outcomes"]["simulation_failure"] == 5
    assert stats["explicit_outcomes"]["pass"] == 3


def test_multi_byte_v6_improves_on_v4() -> None:
    """The fixes are measured, not asserted: 0/8 -> 3/8 after repair."""
    v4_rows, v4 = _multi(MULTI_V4)
    v6_rows, v6 = _multi(MULTI_V6)

    assert len(v4_rows) == len(v6_rows) == 8
    assert v4["pass_after_repair"] == 0
    assert v6["pass_after_repair"] == 3
    assert v6["pass_at_1"] == 2
    assert v6["recovered_by_repair"] == 1
    # v4 (after the structural fix) still charged three compile failures to
    # the model; v6 has none.
    assert v4["explicit_outcomes"]["compile_failure"] == 3
    assert v4["explicit_outcomes"]["structural_failure"] == 0
    assert v6["explicit_outcomes"]["compile_failure"] == 0
    assert v6["explicit_outcomes"]["structural_failure"] == 0


def test_multi_byte_v3_recorded_the_harness_owned_structural_failures() -> None:
    """The original baseline charged two structural failures to the model."""
    multi = [row for row in _rows(BASELINE) if row["case_id"] in MULTI_BYTE]
    outcomes = Counter(
        classify_final_result(row["final"], row["error"]) for row in multi
    )
    assert outcomes["structural_failure"] == 2
    assert outcomes["compile_failure"] == 2
    assert outcomes["simulation_failure"] == 4


CURRENT = ROOT / "results" / "bench_baseline_v4"


def _current() -> tuple[list[dict[str, str]], dict, dict]:
    rows = list(csv.DictReader((CURRENT / "runs.csv").open(encoding="utf-8")))
    summary = json.loads((CURRENT / "summary.json").read_text(encoding="utf-8"))
    return rows, summary, summary["configs"]["on-on"]


def test_current_baseline_describes_the_same_24_runs() -> None:
    rows, summary, stats = _current()

    assert len(rows) == 24
    assert summary["rows"] == 24
    assert len(summary["cases"]) == 12
    assert len({row["run_id"] for row in rows}) == 24
    per_case = Counter(row["case_id"] for row in rows)
    assert len(per_case) == 12
    assert set(per_case.values()) == {2}
    assert {row["case_id"] for row in rows} == SINGLE_BYTE | MULTI_BYTE


def test_current_baseline_provenance_matches_the_recorded_corpus() -> None:
    _, summary, stats = _current()

    assert summary["git_commit"] == "f8d204f11e552408c70c6b5998d0ffaeebc5d5ff"
    assert summary["model"] == MODEL
    assert summary["model_digest"] == MODEL_DIGEST
    assert summary["retrieval_corpus_hash"] == _summary()["retrieval_corpus_hash"]
    assert summary["retrieval_memory_frozen"] is True
    assert summary["temperature"] == 0.25
    assert summary["max_repairs"] == 2
    assert summary["samples_per_case"] == 2
    assert summary["base_seed"] == 42
    assert stats["batch_valid"] is True
    assert stats["batch_invalid_reason"] == "Batch valid"
    assert stats["model_invoked"] is True
    assert stats["infrastructure_failures"] == 0
    assert stats["runs_without_candidate"] == 0


def test_current_baseline_outcomes_are_derived_from_the_rows() -> None:
    rows, _, stats = _current()

    observed = Counter(
        classify_final_result(row["final"], row["error"]) for row in rows
    )
    assert stats["explicit_outcomes"] == {
        name: observed.get(name, 0) for name in stats["explicit_outcomes"]
    }
    assert stats["explicit_outcomes"]["pass"] == 23
    assert stats["explicit_outcomes"]["simulation_failure"] == 1
    assert not any(
        classify_final_result(row["final"], row["error"])
        in INFRASTRUCTURE_CLASSIFICATIONS
        for row in rows
    )


def test_current_baseline_reports_first_attempt_and_repair_separately() -> None:
    rows, _, stats = _current()

    first = sum(row["first_attempt_pass"] == "True" for row in rows)
    final = sum(row["final"] == "pass" for row in rows)
    recovered = sum(row["recovered_after_repair"] == "True" for row in rows)

    assert first == stats["pass_at_1"] == 21
    assert final == stats["pass_after_repair"] == 23
    assert recovered == stats["recovered_by_repair"] == 2
    assert stats["candidate_verification_failures"] == 1
    assert stats["successful_candidates"] == 23
    assert stats["distinct_first_designs"] == 22


def test_current_baseline_single_and_multi_byte_splits() -> None:
    rows, _, stats = _current()
    single = [row for row in rows if row["case_id"] in SINGLE_BYTE]
    multi = [row for row in rows if row["case_id"] in MULTI_BYTE]

    assert len(single) == 16 and len(multi) == 8
    assert sum(row["first_attempt_pass"] == "True" for row in single) == 16
    assert sum(row["final"] == "pass" for row in single) == 16
    # Multi-byte is no longer 0/8 after the naming-scheme fix.
    assert sum(row["first_attempt_pass"] == "True" for row in multi) == 5
    assert sum(row["final"] == "pass" for row in multi) == 7
    assert sum(row["recovered_after_repair"] == "True" for row in multi) == 2
    # The only remaining failure is the three-byte case, sample 0.
    failures = [row for row in rows if row["final"] != "pass"]
    assert [(row["case_id"], row["sample"]) for row in failures] == [
        ("c08-three-byte", "0")
    ]
    assert failures[0]["explicit_classification"] == "simulation_failure"


def test_current_baseline_improves_on_the_previous_corrected_baseline() -> None:
    """The improvement is measured, not asserted: 16/24 -> 21/24 pass@1."""
    previous = _summary()["configs"]["on-on"]
    _, _, current = _current()

    assert previous["pass_at_1"] == 16
    assert current["pass_at_1"] == 21
    assert previous["pass_after_repair"] == 16
    assert current["pass_after_repair"] == 23
    # v3 charged 2 compile and 2 structural failures to the model; v4 has none.
    assert previous["explicit_outcomes"]["compile_failure"] == 2
    assert previous["explicit_outcomes"]["structural_failure"] == 2
    assert current["explicit_outcomes"]["compile_failure"] == 0
    assert current["explicit_outcomes"]["structural_failure"] == 0


MULTI_V7 = ROOT / "results" / "bench_multibyte_v7"


def test_three_byte_seed_did_not_change_the_multi_byte_rate() -> None:
    """Adding the 3-byte seed was a measured neutral result, not a win.

    Retrieval had no 3-byte seed, so a 3-byte spec fell back to the 2-byte
    design. The seed is gate-verified and strictly more informative than the
    fallback, but the aggregate multi-byte rate did not move; one case's
    failure mode changed from simulation to a model-owned syntax error. These
    artifacts are kept so the corpus change is auditable.
    """
    v6_rows, v6_stats = _multi(MULTI_V6)
    v7_rows, v7_stats = _multi(MULTI_V7)
    summary = json.loads((MULTI_V7 / "summary.json").read_text(encoding="utf-8"))

    assert len(v7_rows) == 8
    assert {row["case_id"] for row in v7_rows} == MULTI_BYTE
    assert v7_stats["batch_valid"] is True
    assert v7_stats["infrastructure_failures"] == 0
    assert v7_stats["model_invoked"] is True
    # The corpus changed, so this batch must not be pooled with v6.
    assert summary["retrieval_corpus_hash"] != json.loads(
        (MULTI_V6 / "summary.json").read_text(encoding="utf-8")
    )["retrieval_corpus_hash"]
    # Compare against the multi-byte subset of the v4 baseline, which has the
    # same code but the corpus without the 3-byte seed.
    v4_rows = list(csv.DictReader((CURRENT / "runs.csv").open(encoding="utf-8")))
    v4_multi = [row for row in v4_rows if row["case_id"] in MULTI_BYTE]
    v4_pass1 = sum(row["first_attempt_pass"] == "True" for row in v4_multi)
    v4_after = sum(row["final"] == "pass" for row in v4_multi)
    assert (v7_stats["pass_at_1"], v7_stats["pass_after_repair"]) == (v4_pass1, v4_after)
    # No harness-owned failure classes appear.
    assert v7_stats["explicit_outcomes"]["structural_failure"] == 0
    assert v7_stats["explicit_outcomes"]["generation_error"] == 0
    assert v7_stats["explicit_outcomes"]["runner_error"] == 0


def test_three_byte_seed_failure_is_model_owned() -> None:
    """The c08 failure must be the model's illegal Verilog, not the scaffold."""
    from tools.rtl_memory import retrieve_rtl_examples

    three_byte = retrieve_rtl_examples(
        "I2C open-drain master FSM START address ACK 3 data bytes STOP Verilog-2001",
        max_examples=1,
        enabled=True,
        prefer_byte_count=3,
    )
    assert three_byte["examples"][0]["label"] == "validated I2C reference (3-byte)"

    rows, stats = _multi(MULTI_V7)
    failures = [row for row in rows if row["final"] != "pass"]
    assert len(failures) == 1
    assert failures[0]["case_id"] == "c08-three-byte"
    assert failures[0]["explicit_classification"] == "compile_failure"


BASELINE_V5 = ROOT / "results" / "bench_baseline_v5"


def _v5() -> tuple[list[dict[str, str]], dict, dict]:
    rows = list(csv.DictReader((BASELINE_V5 / "runs.csv").open(encoding="utf-8")))
    summary = json.loads((BASELINE_V5 / "summary.json").read_text(encoding="utf-8"))
    return rows, summary, summary["configs"]["on-on"]


def test_v5_baseline_is_valid_on_the_current_corpus() -> None:
    rows, summary, stats = _v5()

    assert len(rows) == 24
    assert summary["rows"] == 24
    assert len(summary["cases"]) == 12
    assert len({row["run_id"] for row in rows}) == 24
    assert {row["case_id"] for row in rows} == SINGLE_BYTE | MULTI_BYTE
    assert summary["git_commit"] == "31c8ab6a639eff89b242af2d44aa326eda62b459"
    assert summary["model"] == MODEL
    assert summary["model_digest"] == MODEL_DIGEST
    assert summary["temperature"] == 0.25
    assert summary["max_repairs"] == 2
    assert summary["base_seed"] == 42
    assert summary["samples_per_case"] == 2
    assert summary["retrieval_memory_frozen"] is True
    assert stats["batch_valid"] is True
    assert stats["infrastructure_failures"] == 0
    assert stats["model_invoked"] is True
    assert stats["runs_without_candidate"] == 0


def test_v5_corpus_is_not_the_v4_corpus() -> None:
    """The 3-byte seed changed the corpus, so the batches must not be pooled."""
    _, v5_summary, _ = _v5()
    _, current_summary, _ = _current()
    v6_summary = json.loads((MULTI_V6 / "summary.json").read_text(encoding="utf-8"))

    assert v5_summary["retrieval_corpus_hash"] != current_summary["retrieval_corpus_hash"]
    assert v5_summary["retrieval_corpus_hash"] != v6_summary["retrieval_corpus_hash"]
    assert len(v5_summary["retrieval_corpus_hash"]) == 64
    int(v5_summary["retrieval_corpus_hash"], 16)


def test_v5_outcomes_and_splits() -> None:
    rows, _, stats = _v5()

    observed = Counter(
        classify_final_result(row["final"], row["error"]) for row in rows
    )
    assert stats["explicit_outcomes"] == {
        name: observed.get(name, 0) for name in stats["explicit_outcomes"]
    }
    assert stats["explicit_outcomes"] == {
        "pass": 23,
        "simulation_failure": 0,
        "compile_failure": 1,
        "structural_failure": 0,
        "generation_error": 0,
        "runner_error": 0,
        "lint_failure": 0,
        "oracle_failure": 0,
    }
    assert stats["pass_at_1"] == 22
    assert stats["pass_after_repair"] == 23
    assert stats["recovered_by_repair"] == 1
    assert stats["candidate_verification_failures"] == 1

    single = [row for row in rows if row["case_id"] in SINGLE_BYTE]
    multi = [row for row in rows if row["case_id"] in MULTI_BYTE]
    assert sum(row["first_attempt_pass"] == "True" for row in single) == 16
    assert sum(row["final"] == "pass" for row in single) == 16
    assert sum(row["first_attempt_pass"] == "True" for row in multi) == 6
    assert sum(row["final"] == "pass" for row in multi) == 7
    failures = [row for row in rows if row["final"] != "pass"]
    assert [(row["case_id"], row["sample"]) for row in failures] == [("c08-three-byte", "0")]
    assert failures[0]["explicit_classification"] == "compile_failure"


def test_v5_only_failure_is_model_owned() -> None:
    """c08 sample 0 fails on the model's own illegal Verilog, not the scaffold."""
    rows, _, _ = _v5()
    failure = next(row for row in rows if row["final"] != "pass")
    run_dir = Path(failure["run_dir"])
    if not run_dir.is_dir():
        return  # raw run directories are gitignored; skipped in CI

    saw_illegal = False
    for attempt in sorted(run_dir.glob("attempt_*.json")):
        raw = json.loads(attempt.read_text()).get("raw_output", "")
        if re.search(r"\}\[\s*\w+\s*\]", raw) or "}[byte_idx]" in raw:
            saw_illegal = True
    assert saw_illegal, "the model's indexed part-select should be reproducible"
