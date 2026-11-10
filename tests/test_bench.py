"""Phase 6: benchmark cases, spec generation, Wilson CIs and the runner."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bench.run import resolve_configs, summarize, wilson_interval
from tools.i2c_spec import parse_i2c_spec
from tools.make_spec import case_device_paths, case_spec_text, load_cases, write_case_spec

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "benchmarks/cases.json"


def test_cases_file_has_at_least_ten_varied_cases():
    cases = load_cases(CASES)
    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["address"] for case in cases}) >= 5
    assert len({len(case["data"]) for case in cases}) >= 2
    assert len({len(case["devices"]) for case in cases}) >= 2
    assert {len(case["data"]) for case in cases} >= {1, 2, 3}


def test_case_spec_is_parseable_and_preserves_the_payload(tmp_path):
    cases = {case["id"]: case for case in load_cases(CASES)}
    for case in cases.values():
        path = write_case_spec(case, tmp_path)
        spec = parse_i2c_spec(str(path))
        assert spec.address_hex == f"{int(case['address'], 0) << 1:02x}"
        assert spec.data_hexes == [f"{int(value, 0):02x}" for value in case["data"]]
        assert spec.nbytes == len(case["data"])


def test_case_devices_resolve_to_real_files():
    for case in load_cases(CASES):
        for path in case_device_paths(case):
            assert Path(path).is_file(), path


def test_two_byte_case_spec_text_uses_a_count_word():
    case = next(c for c in load_cases(CASES) if len(c["data"]) == 2)
    text = case_spec_text(case)
    assert "writes TWO bytes" in text


def test_wilson_interval_matches_known_values():
    low, high = wilson_interval(1, 5)
    assert low == pytest.approx(0.0363, abs=1e-4)
    assert high == pytest.approx(0.6245, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(5, 5)
    assert low > 0.0 and high == 1.0
    low, high = wilson_interval(0, 5)
    assert low == 0.0 and high < 1.0


def test_config_aliases_dedupe_the_shared_on_on_cell():
    resolved = resolve_configs(["retrieval-on", "patcher-on", "on-on"])
    assert [name for name, _ in resolved] == ["on-on"]
    resolved = resolve_configs(["template", "on-on", "retrieval-off", "patcher-off"])
    assert [name for name, _ in resolved] == [
        "template", "on-on", "retrieval-off", "patcher-off"
    ]
    with pytest.raises(ValueError):
        resolve_configs(["not-a-config"])


def test_ir_switch_suffixes_model_configs_and_collapses_the_patcher_axis():
    resolved = resolve_configs(["on-on", "retrieval-off", "patcher-off"], ir=True)
    assert [name for name, _ in resolved] == ["on-on-ir", "retrieval-off-ir"]
    assert all(spec["generator"] == "ir" for _, spec in resolved)
    assert all(spec["patcher"] for _, spec in resolved)

    template = resolve_configs(["template"], ir=True)
    assert [name for name, _ in template] == ["template"]
    assert template[0][1]["generator"] == "template"


def test_ir_config_installs_the_json_generator_hooks(tmp_path, monkeypatch):
    import bench.run as bench
    from agent.ir_generator import generate_fsm_rtl, repair_fsm_rtl

    case = load_cases(CASES)[0]
    spec_path = write_case_spec(case, tmp_path)
    seen = {}

    def fake_run_once(**kwargs):
        seen.update(kwargs)
        return {
            "final": "pass",
            "attempts": [],
            "first_attempt_pass": True,
            "recovered_after_repair": False,
            "run_id": "r1",
            "run_dir": "",
            "elapsed_sec": 0.0,
        }

    monkeypatch.setattr(bench, "run_once", fake_run_once)

    def one(config_name, config_spec):
        return bench.run_one(
            config_name=config_name,
            config_spec=config_spec,
            case=case,
            spec_path=spec_path,
            sample=0,
            seed=42,
            model="qwen-test",
            temperature=0.0,
            max_repairs=1,
            out_dir=tmp_path,
        )

    row = one("on-on-ir", {**bench.CONFIGS["on-on"], "generator": "ir"})
    assert seen["generate_fn"] is generate_fsm_rtl
    assert seen["repair_fn"] is repair_fsm_rtl
    assert row["generator"] == "ir"

    seen.clear()
    row = one("on-on", bench.CONFIGS["on-on"])
    assert "generate_fn" not in seen and "repair_fn" not in seen
    assert row["generator"] == "rtl"


def test_resume_skips_completed_runs_and_repairs_a_partial_csv(tmp_path):
    """A long batch can restart after a crash without rerunning finished work."""
    from bench.run import main

    out = tmp_path / "bench"
    args = [
        "--cases", str(CASES), "--case-limit", "2", "--samples", "1",
        "--configs", "template", "--out-dir", str(out), "--quiet",
    ]
    assert main(args) == 0
    first = list(csv.DictReader((out / "runs.csv").open(encoding="utf-8")))
    assert len(first) == 2
    finished_ids = {row["run_id"] for row in first}

    # Resume with an intact csv: nothing is rerun.
    assert main([*args, "--resume"]) == 0
    again = list(csv.DictReader((out / "runs.csv").open(encoding="utf-8")))
    assert {row["run_id"] for row in again} == finished_ids

    # Simulate a crash after one row was flushed: the missing run is redone,
    # the finished one is kept, and the index has exactly one line per row.
    lines = (out / "runs.csv").read_text(encoding="utf-8").splitlines()
    (out / "runs.csv").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert main([*args, "--resume"]) == 0

    final = list(csv.DictReader((out / "runs.csv").open(encoding="utf-8")))
    index = [
        json.loads(line)
        for line in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert len(final) == len(index) == summary["rows"] == 2
    assert {row["run_id"] for row in final} == {row["run_id"] for row in index}
    assert len({row["run_id"] for row in final}) == 2


def test_summary_reports_rates_cis_and_distinct_designs():
    rows = [
        {
            "config": "on-on", "case_id": "c1", "sample": 0,
            "final": "pass", "first_attempt_pass": True,
            "recovered_after_repair": False, "elapsed_sec": 10.0,
            "total_tokens": 100, "first_sha256": "aaa",
        },
        {
            "config": "on-on", "case_id": "c1", "sample": 1,
            "final": "pass", "first_attempt_pass": False,
            "recovered_after_repair": True, "elapsed_sec": 20.0,
            "total_tokens": 200, "first_sha256": "bbb",
        },
        {
            "config": "on-on", "case_id": "c1", "sample": 2,
            "final": "sim_checks_failed", "first_attempt_pass": False,
            "recovered_after_repair": False, "elapsed_sec": 30.0,
            "total_tokens": 300, "first_sha256": "bbb",
        },
    ]
    stats = summarize(rows)["configs"]["on-on"]
    assert stats["runs"] == 3
    assert stats["pass_at_1"] == 1
    assert stats["pass_after_repair"] == 2
    assert stats["recovered_by_repair"] == 1
    assert stats["distinct_first_designs"] == 2
    assert stats["median_latency_sec"] == 20.0
    assert stats["median_tokens_per_run"] == 200.0
    assert len(stats["pass_at_1_ci95"]) == 2


def test_summary_separates_runs_that_never_produced_a_candidate():
    """A run where the model was never called must not read as a model result."""
    def row(sample, final, sha, attempts):
        return {
            "config": "on-on", "case_id": "c1", "sample": sample,
            "final": final, "first_attempt_pass": final == "pass",
            "recovered_after_repair": False, "elapsed_sec": 10.0,
            "total_tokens": 100, "first_sha256": sha, "attempts": attempts,
        }

    rows = [
        row(0, "generation_error", "", 0),
        row(1, "pass", "aaa", 1),
        row(2, "runner_error", "", 0),
    ]
    stats = summarize(rows)["configs"]["on-on"]
    assert stats["runs"] == 3
    assert stats["runs_without_candidate"] == 2
    assert stats["candidate_runs"] == 1
    assert stats["outcomes"] == {
        "generation_error": 1, "pass": 1, "runner_error": 1,
    }
    assert stats["pass_at_1"] == 1
    # Denominators stay over every requested run, but the contamination is
    # visible instead of being reported as a flat 33% pass rate.
    assert stats["pass_at_1_rate"] == pytest.approx(1 / 3, abs=1e-4)


def test_template_benchmark_smoke_writes_csv_and_summary(tmp_path):
    from bench.run import main

    out = tmp_path / "bench"
    rc = main([
        "--cases", str(CASES),
        "--case-limit", "1",
        "--samples", "1",
        "--configs", "template",
        "--out-dir", str(out),
        "--quiet",
    ])
    assert rc == 0
    rows = list(csv.DictReader((out / "runs.csv").open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["config"] == "template"
    assert rows[0]["final"] == "pass"
    assert rows[0]["first_attempt_pass"] == "True"
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    stats = summary["configs"]["template"]
    assert stats["pass_at_1"] == 1
    assert stats["pass_after_repair"] == 1
    assert (out / "report.md").is_file()


def test_rerunning_a_benchmark_overwrites_instead_of_accumulating(tmp_path):
    """csv truncated, jsonl appended: the three artifacts used to disagree.

    ``runs.csv`` was opened with ``"w"`` while the orchestrator appended to
    ``runs.jsonl``, and ``summary.json`` was computed from the in-memory rows
    only. After two invocations the committed report could claim 96 rows while
    the csv held 36 and the jsonl held 92.
    """
    from bench.run import main

    out = tmp_path / "bench"
    args = [
        "--cases", str(CASES), "--case-limit", "1", "--samples", "1",
        "--configs", "template", "--out-dir", str(out), "--quiet",
    ]
    assert main(args) == 0
    assert main(args) == 0

    csv_rows = list(csv.DictReader((out / "runs.csv").open(encoding="utf-8")))
    jsonl_rows = [
        json.loads(line)
        for line in (out / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))

    assert len(csv_rows) == len(jsonl_rows) == summary["rows"] == 1
    assert {r["run_id"] for r in csv_rows} == {r["run_id"] for r in jsonl_rows}
