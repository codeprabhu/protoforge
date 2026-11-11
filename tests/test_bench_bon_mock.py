"""Deterministic Best-of-N scheduler tests (Task 4).

These tests use an injected mock candidate runner (a deterministic mock
generator plus the same single verification entry point every real candidate
goes through). They prove the Best-of-N orchestration properties without
touching Ollama or an RTL toolchain:

* each candidate is generated independently with its own seed/run directory;
* every candidate receives identical configuration and verification;
* the first passing candidate wins and later candidates are not generated;
* an all-failure batch reports the last candidate and keeps every attempt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from bench.bon import run_one_bon

CASE = {
    "id": "c-mock",
    "address": "0x50",
    "data": ["0x3C"],
    "devices": ["device_sensor", "device_controller"],
}
CONFIG_SPEC = {
    "kind": "model",
    "retrieval": True,
    "patcher": True,
    "generator": "rtl",
}


class DeterministicCandidateRunner:
    """Mock generator + verifier with deterministic, seed-derived candidates.

    ``outcomes`` is consumed in candidate order. Each call generates exactly one
    candidate artifact (hash derived from the candidate seed) and records one
    verification result, mirroring ``run_once``'s single gate entry point.
    """

    def __init__(self, root: Path, outcomes: list[str], fail_on: set[int] | None = None):
        self.root = root
        self.outcomes = outcomes
        self.fail_on = fail_on or set()
        self.calls: list[dict[str, Any]] = []
        self.verified_hashes: list[str] = []

    def __call__(self, *, model: str, config: Any, **hooks: Any) -> dict[str, Any]:
        index = len(self.calls)
        self.calls.append({
            "index": index,
            "model": model,
            "seed": config.seed,
            "spec_path": config.spec_path,
            "device_paths": config.device_paths,
            "temperature": config.temperature,
            "max_repairs": config.max_repairs,
            "retrieval": config.retrieval,
            "patcher": config.patcher,
            "output_root": config.output_root,
            "hooks": hooks,
        })

        if index in self.fail_on:
            raise RuntimeError(f"mock runner crash on candidate {index}")

        final = self.outcomes[index]
        if final == "generation_error":
            # The model never produced a candidate: no attempt, no run dir.
            return {
                "final": "generation_error",
                "first_attempt_pass": False,
                "recovered_after_repair": False,
                "attempts": [],
                "run_id": "",
                "run_dir": "",
                "elapsed_sec": 0.0,
                "generation_error": "mock model unavailable",
            }

        code = (
            f"// deterministic mock candidate index={index} seed={config.seed}\n"
            "module top_glue;\nendmodule\n"
        )
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        self.verified_hashes.append(digest)

        run_dir = self.root / f"mock_run_{index}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "attempt_0.v").write_text(code, encoding="utf-8")
        (run_dir / "attempt_0.json").write_text(
            json.dumps({"model_metadata": {"prompt_eval_count": 10, "eval_count": 5}}),
            encoding="utf-8",
        )

        return {
            "final": final,
            "first_attempt_pass": final == "pass",
            "recovered_after_repair": final == "pass",
            "attempts": [{"sha256": digest}],
            "run_id": f"mock_run_{index}",
            "run_dir": str(run_dir),
            "elapsed_sec": 1.0,
            "generation_error": "",
        }


def _run(
    runner: Callable[..., dict[str, Any]],
    *,
    tmp_path: Path,
    n: int,
    learn: bool = False,
) -> dict[str, Any]:
    spec_path = tmp_path / "c-mock.md"
    spec_path.write_text("# mock spec\n", encoding="utf-8")
    return run_one_bon(
        config_name="on-on",
        config_spec=CONFIG_SPEC,
        case=CASE,
        spec_path=spec_path,
        sample=0,
        seed=42,
        model="mock-model",
        temperature=0.25,
        max_repairs=2,
        out_dir=tmp_path / "out",
        learn=learn,
        n=n,
        runner=runner,
    )


def test_first_passing_candidate_wins_and_stops(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(tmp_path, ["sim_checks_failed", "pass", "pass"])
    row = _run(runner, tmp_path=tmp_path, n=4)

    assert len(runner.calls) == 2, "must stop generating after the first pass"
    assert row["final"] == "pass"
    assert row["bon_candidate_index"] == 1
    assert row["bon_total_candidates"] == 2
    assert row["explicit_classification"] == "pass"


def test_all_candidates_fail_returns_last_and_keeps_all(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(
        tmp_path, ["compile_error", "sim_checks_failed", "judge_error"]
    )
    row = _run(runner, tmp_path=tmp_path, n=3)

    assert len(runner.calls) == 3, "all N candidates must be attempted"
    assert row["final"] == "judge_error"
    assert row["explicit_classification"] == "oracle_failure"
    assert row["bon_candidate_index"] == 2
    assert row["bon_total_candidates"] == 3


def test_candidates_are_independent_and_identically_verified(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(
        tmp_path, ["compile_error", "sim_checks_failed", "judge_error", "compile_error"]
    )
    _run(runner, tmp_path=tmp_path, n=4)

    assert len(runner.calls) == 4
    seeds = [call["seed"] for call in runner.calls]
    assert len(set(seeds)) == 4, f"candidate seeds must be independent, got {seeds}"
    assert seeds == [42, 1042, 2042, 3042], "candidate seeds must be deterministic"

    # Identical verification setup: one spec, one device set, one gate config.
    for key in ("spec_path", "device_paths", "temperature", "max_repairs",
                "retrieval", "patcher", "output_root"):
        values = {call[key] for call in runner.calls}
        assert len(values) == 1, f"candidates received different {key}: {values}"

    # Each candidate was generated independently and verified exactly once.
    assert len(set(runner.verified_hashes)) == 4
    assert len(runner.verified_hashes) == len(runner.calls)

    # Retrieval memory is frozen by default for a benchmark batch.
    for call in runner.calls:
        hooks = call["hooks"]
        assert hooks["on_success"]() is None
        assert hooks["on_failure"]() is None


def test_runner_exception_is_infrastructure_error_and_continues(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(
        tmp_path, ["pass", "pass"], fail_on={0}
    )
    row = _run(runner, tmp_path=tmp_path, n=2)

    assert len(runner.calls) == 2, "a crashed candidate must not abort the batch"
    assert row["final"] == "pass"
    assert row["bon_candidate_index"] == 1
    assert row["bon_total_candidates"] == 2


def test_all_candidates_crash_reports_runner_error(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(
        tmp_path, ["pass", "pass", "pass"], fail_on={0, 1, 2}
    )
    row = _run(runner, tmp_path=tmp_path, n=3)

    assert len(runner.calls) == 3
    assert row["final"] == "runner_error"
    assert row["explicit_classification"] == "runner_error"
    assert row["bon_candidate_index"] == 2
    assert row["bon_total_candidates"] == 3


def test_missing_candidate_artifacts_never_pass(tmp_path: Path) -> None:
    """A candidate that never staged an attempt must not be counted as a pass."""
    runner = DeterministicCandidateRunner(tmp_path, ["generation_error"])
    row = _run(runner, tmp_path=tmp_path, n=1)

    assert row["final"] == "generation_error"
    assert row["explicit_classification"] == "generation_error"
    assert row["attempts"] == 0
    assert row["run_dir"] == ""
    assert row["bon_total_candidates"] == 1


def test_bon_candidate_metadata_survives_csv_and_jsonl(tmp_path: Path) -> None:
    """The winning candidate index and attempt count must be auditable."""
    import csv

    from bench.run import CSV_FIELDS, _write_csv, _write_index

    runner = DeterministicCandidateRunner(tmp_path, ["sim_checks_failed", "pass"])
    row = _run(runner, tmp_path=tmp_path, n=4)
    assert row["bon_candidate_index"] == 1
    assert row["bon_total_candidates"] == 2

    csv_path = tmp_path / "runs.csv"
    _write_csv(csv_path, [row])
    written = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert set(CSV_FIELDS) <= set(written[0])
    assert written[0]["bon_candidate_index"] == "1"
    assert written[0]["bon_total_candidates"] == "2"
    assert written[0]["explicit_classification"] == "pass"

    index_path = tmp_path / "runs.jsonl"
    _write_index(index_path, [row])
    entry = json.loads(index_path.read_text(encoding="utf-8").strip())
    assert entry["explicit_classification"] == "pass"
    assert entry["bon_candidate_index"] == 1
    assert entry["bon_total_candidates"] == 2


def test_best_of_n_requires_positive_n(tmp_path: Path) -> None:
    runner = DeterministicCandidateRunner(tmp_path, [])
    with pytest.raises(ValueError, match="n >= 1"):
        _run(runner, tmp_path=tmp_path, n=0)
