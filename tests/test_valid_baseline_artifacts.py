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
"""

from __future__ import annotations

import csv
import json
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
