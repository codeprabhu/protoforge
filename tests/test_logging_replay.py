"""Phase 3 regression: runs are auditable and replayable from disk."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import agent.rtl_generator as rtl_generator
from agent_core import agent as pipeline
from core.event_store import EventStore
from tools.rtl_memory import record_passing_example, retrieve_rtl_examples
from tools.replay import replay_run

ROOT = Path(__file__).resolve().parents[1]
GOOD = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
BROKEN = (ROOT / "protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read_text()
# The adapter generates its own nonce and passes it to both sim_fn and parse_result_line.
# The stubbed sim_fn must capture the nonce and include it in the RESULT line.
RESULT_OK_TEMPLATE = (
    "RESULT:NONCE={nonce} saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 "
    "addr=a0 data=3c tb_done=1 starts=1 stops=1\n"
)


def _stub_model(monkeypatch, code: str = GOOD):
    def fake_generate(prompt, **kwargs):
        return {"content": code, "model": "stub", "eval_count": 7, "total_duration": 11}

    monkeypatch.setattr(rtl_generator, "generate_verilog", fake_generate)


def _fast_gates() -> dict:
    captured_nonce = {"value": None}
    def fake_sim(*args, **kwargs):
        captured_nonce["value"] = kwargs.get("nonce", "0000000000000000")
        return {
            "success": True,
            "stdout": RESULT_OK_TEMPLATE.format(nonce=captured_nonce["value"]),
            "stderr": "",
            "timeout": False,
        }
    return {
        "compile_fn": lambda *a, **k: {"success": True, "stdout": "", "stderr": ""},
        "hardware_check_fn": lambda *a, **k: {"success": True, "stage": "passed"},
        "sim_fn": fake_sim,
        "on_success": lambda *a, **k: None,
        "on_failure": lambda *a, **k: None,
    }


def _run(run_config, monkeypatch, code: str = GOOD) -> dict:
    _stub_model(monkeypatch, code)
    return pipeline.run_once(model="stub", config=run_config, **_fast_gates())


def test_run_writes_header_attempt_record_and_summary(run_config, monkeypatch):
    result = _run(run_config, monkeypatch)
    run_dir = Path(result["run_dir"])
    assert result["final"] == "pass"

    header = json.loads((run_dir / "run.json").read_text())
    assert header["run_id"] == result["run_id"]
    assert header["model"] == "stub"
    assert "model_digest" in header
    assert header["seed"] == run_config.seed
    assert header["temperature"] == run_config.temperature
    assert header["config"]["spec_path"] == run_config.spec_path
    assert header["final"] == "pass"

    record = json.loads((run_dir / "attempt_0.json").read_text())
    assert record["prompt"] and record["raw_output"] and record["cleaned_code"]
    assert record["final_code"].startswith("module top_glue")
    assert "device_sensor" in record["final_code"]
    assert record["code_sha256"]
    assert record["model_metadata"]["eval_count"] == 7
    assert [stage["stage"] for stage in record["stages"]] == [
        "structural", "compile", "hardware", "simulation", "judge",
    ]
    assert all(stage["passed"] for stage in record["stages"])
    # Retrieval must say which example was used, where it came from, and its score.
    assert record["retrieval"]["enabled"] is True
    assert record["retrieval"]["examples"]
    example = record["retrieval"]["examples"][0]
    assert {"label", "origin", "score", "chars"} <= set(example)
    assert example["origin"] in {"seed", "learned"}
    # Full attempt source and prompt are on disk, not only hashes.
    assert (run_dir / "attempt_0.v").read_text() == record["final_code"]
    assert (run_dir / "attempt_0_prompt.txt").read_text() == record["prompt"]

    lines = Path(run_config.results_log).read_text().strip().splitlines()
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert summary["run_dir"] == str(run_dir)
    assert summary["final"] == "pass"
    assert summary["attempts"] == 1
    # The index is a one-line pointer; bulky payloads stay in the run directory.
    assert "raw" not in summary and "prompt" not in summary and "attempts_log" not in summary


def test_run_emits_minimal_events(run_config, monkeypatch):
    result = _run(run_config, monkeypatch)
    trace = EventStore(run_config.trace_dir).get_trace(result["run_id"])
    assert trace is not None
    types = [event.event_type for event in trace.events]
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    assert types.count("model_invoked") == 1
    assert types.count("stage_finished") == 5
    assert trace.status == "completed"
    assert trace.events[-1].data["final"] == "pass"
    assert trace.events[-1].data["run_dir"] == result["run_dir"]


def test_retrieval_metadata_reports_origin_and_score(tmp_path):
    record_passing_example(GOOD, "unit-model", {"protocol": "i2c-v1"}, tmp_path)
    retrieved = retrieve_rtl_examples(
        "I2C start stop address ACK SDA SCL", max_examples=4,
        max_chars=30000, memory_dir=tmp_path,
    )
    assert retrieved["enabled"] is True
    assert retrieved["examples"]
    origins = {example["origin"] for example in retrieved["examples"]}
    assert origins <= {"seed", "learned"}
    assert all(example["score"] > 0 for example in retrieved["examples"])
    assert retrieved["text"] == retrieve_rtl_examples(
        "I2C start stop address ACK SDA SCL", max_examples=4,
        max_chars=30000, memory_dir=tmp_path,
    )["text"]


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog not installed",
)
def test_replay_matches_stored_verdict_and_detects_tampering(run_config, monkeypatch):
    _stub_model(monkeypatch)
    result = pipeline.run_once(
        model="stub", config=run_config,
        on_success=lambda *a, **k: None, on_failure=lambda *a, **k: None,
    )
    assert result["final"] == "pass"
    run_dir = Path(result["run_dir"])

    first = replay_run(run_dir, verbose=False)
    assert first["matched"] is True

    # Tampering with the stored candidate must be detected, proving the replay
    # actually re-runs the oracle instead of trusting the stored verdict.
    (run_dir / "attempt_0.v").write_text(BROKEN, encoding="utf-8")
    second = replay_run(run_dir, verbose=False)
    assert second["matched"] is False
    assert second["attempts"][0]["replay_outcome"] == "sim_checks_failed"

    # Second tamper case: alter the stored verdict (attempt_0.json) to claim pass
    # while keeping the original good code. Replay must detect the mismatch.
    result = _run(run_config, monkeypatch)
    run_dir2 = Path(result["run_dir"])
    first2 = replay_run(run_dir2, verbose=False)
    assert first2["matched"] is True

    # Modify the stored attempt record to change the judge outcome to "pass"
    # even though the code is the original good code (which should pass).
    # We change the judge stage to fail to simulate a tampered verdict.
    attempt_record = json.loads((run_dir2 / "attempt_0.json").read_text())
    # Find the judge stage and flip its passed flag
    for stage in attempt_record["stages"]:
        if stage["stage"] == "judge":
            stage["passed"] = False
            stage["failed_checkes"] = ["tampered"]
            break
    (run_dir2 / "attempt_0.json").write_text(json.dumps(attempt_record), encoding="utf-8")

    third = replay_run(run_dir2, verbose=False)
    assert third["matched"] is False
    # The replay outcome should still be pass (code is good), but the stored
    # verdict claims fail, so matched=False
    assert third["attempts"][0]["replay_outcome"] == "pass"
