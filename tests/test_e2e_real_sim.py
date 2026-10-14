"""End-to-end pipeline with REAL iverilog/vvp and a stubbed LLM.

The 'model' first returns the known missing-STOP design, then (after the
repair prompt) the correct one. Everything else -- structural gate, compile,
simulation, judge, repair routing -- is the real code. Skipped without iverilog.
"""

import shutil
from pathlib import Path

import pytest

import agent.rtl_generator as rtl_generator
import agent_core.agent as pipeline

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog not installed",
)


def _run(tmp_path, monkeypatch, outputs, max_repairs=2):
    monkeypatch.chdir(ROOT)  # pipeline uses repo-relative device/testbench paths
    queue = list(outputs)
    prompts = []

    def fake_generate(prompt, model=None, temperature=None, seed=None):
        prompts.append(prompt)
        return f"```verilog\n{queue.pop(0)}\n```"

    monkeypatch.setattr(rtl_generator, "generate_verilog", fake_generate)
    monkeypatch.setattr(pipeline, "TOP", str(tmp_path / "top_glue.v"))
    monkeypatch.setattr(pipeline, "SIM_OUT", str(tmp_path / "sim.out"))
    monkeypatch.setattr(pipeline, "RESULTS_LOG", str(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(pipeline, "record_passing_example", lambda *a, **k: tmp_path / "ok.v")
    monkeypatch.setattr(pipeline, "record_failed_example", lambda *a, **k: tmp_path / "bad.v")
    monkeypatch.setattr(rtl_generator, "save_file", lambda path, text: Path(path).write_text(text))
    return pipeline.run_once(model="stub", max_repairs=max_repairs), prompts


def _ref(name):
    return (ROOT / "protocols/i2c/reference" / name).read_text()


def test_broken_then_repaired_passes_for_real(tmp_path, monkeypatch):
    result, prompts = _run(tmp_path, monkeypatch, [
        _ref("broken_master_no_stop_reset_safe.v"),
        _ref("good_master_reset_safe.v"),
    ])
    assert result["final"] == "pass"
    assert result["first_attempt_pass"] is False and result["recovered_after_repair"] is True
    assert result["attempts"][0]["failed_checks"] == ["stop", "single_stop"]
    # The repair prompt must carry the semantic diagnosis, not just "failed".
    assert "STOP" in prompts[1]


def test_correct_design_passes_first_try(tmp_path, monkeypatch):
    result, prompts = _run(tmp_path, monkeypatch, [_ref("good_master_reset_safe.v")])
    assert result["final"] == "pass" and result["first_attempt_pass"] is True
    assert len(prompts) == 1


def test_persistent_fault_exhausts_repair_budget(tmp_path, monkeypatch):
    broken = _ref("broken_master_no_stop_reset_safe.v")
    result, _ = _run(tmp_path, monkeypatch, [broken, broken, broken], max_repairs=2)
    assert result["final"] == "sim_checks_failed"
    assert len(result["attempts"]) == 3
