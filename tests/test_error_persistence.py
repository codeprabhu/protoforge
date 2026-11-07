"""Failure diagnostics must survive to disk (workflow Phase 3, step 44).

A run that dies before the first gate used to record ``generation_error`` only
in the in-memory log: neither ``run.json`` nor the one-line ``runs.jsonl``
index carried the message. The committed benchmark therefore showed two whole
configurations at 0.0% pass with 0 tokens and no explanation at all, which is
indistinguishable from "the model is bad". These tests pin the error text to
both artifacts.
"""

from __future__ import annotations

import json

from agent_core.agent import run_once
from agent_core.config import Config
from core import outcomes


def _config(tmp_path, name: str) -> Config:
    return Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / f"{name}.jsonl"),
        trace_dir=str(tmp_path / "traces"),
    )


def _boom(*args, **kwargs):
    raise ConnectionError("ollama service refused the connection")


def test_generation_error_is_written_to_the_run_header(tmp_path):
    cfg = _config(tmp_path, "header")
    log = run_once(config=cfg, generate_fn=_boom)

    assert log["final"] == outcomes.GENERATION_ERROR

    header = json.loads((tmp_path / "runs" / log["run_id"] / "run.json").read_text())
    assert header["final"] == outcomes.GENERATION_ERROR
    assert "ollama service refused the connection" in header["generation_error"]
    assert header["attempts"] == 0


def test_generation_error_is_written_to_the_one_line_index(tmp_path):
    cfg = _config(tmp_path, "index")
    log = run_once(config=cfg, generate_fn=_boom)

    lines = (tmp_path / "index.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["run_id"] == log["run_id"]
    assert entry["final"] == outcomes.GENERATION_ERROR
    assert "ollama service refused" in entry["error"]


def test_generation_error_outcome_is_a_known_final(tmp_path):
    """It must never land in the undifferentiated "other" bucket."""
    cfg = _config(tmp_path, "known")
    log = run_once(config=cfg, generate_fn=_boom)
    assert log["final"] in outcomes.KNOWN_FINALS


def test_successful_run_header_has_no_error_field(tmp_path):
    cfg = _config(tmp_path, "clean")
    good = "protocols/i2c/reference/good_master_reset_safe.v"
    code = open(good, encoding="utf-8").read()
    log = run_once(
        config=cfg,
        generate_fn=lambda *a, **k: code,
        on_success=lambda *a, **k: None,
        on_failure=lambda *a, **k: None,
        compile_fn=lambda *a, **k: {"success": True, "stdout": "", "stderr": "", "returncode": 0},
        hardware_check_fn=lambda *a, **k: {"success": True, "stage": "hardware_pass"},
        sim_fn=lambda *a, **k: {
            "success": True,
            "stdout": "RESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
            "stderr": "",
        },
    )
    assert log["final"] == outcomes.PASS

    header = json.loads((tmp_path / "runs" / log["run_id"] / "run.json").read_text())
    assert "generation_error" not in header
    assert "tool_error" not in header

    entry = json.loads((tmp_path / "clean.jsonl").read_text().splitlines()[0])
    assert entry["error"] is None
