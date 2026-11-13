"""Tool-free integration test of the full generate/repair orchestration."""

from pathlib import Path
from types import SimpleNamespace
import json

import agent.rtl_generator as rtl_generator
import agent_core.agent as pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_model_repair_loop_recovers_from_missing_stop(monkeypatch, tmp_path):
    from agent_core.config import Config
    run_config = Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / "runs.jsonl"),
        trace_dir=str(tmp_path / "traces"),
        max_repairs=1,
    )
    broken = (ROOT / "protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read_text()
    good = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    calls = {"count": 0}

    def fake_generate(prompt, **kwargs):
        calls["count"] += 1
        code = broken if calls["count"] == 1 else good
        return f"```verilog\n{code}\n```"

    sim_results = [
        "RESULT:NONCE={nonce} saw_start=1 saw_stop=0 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
        "RESULT:NONCE={nonce} saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
    ]
    sim_index = {"value": 0}

    def fake_sim(*args, **kwargs):
        nonce = kwargs.get("nonce", "0000000000000000")
        idx = sim_index["value"]
        sim_index["value"] += 1
        return {
            "success": True, "returncode": 0,
            "stdout": sim_results[idx].format(nonce=nonce),
            "stderr": "", "timeout": False,
        }

    monkeypatch.setattr(rtl_generator, "generate_verilog", fake_generate)
    monkeypatch.setattr(pipeline, "compile_verilog", lambda *args, **kwargs: {
        "success": True, "stdout": "", "stderr": "", "returncode": 0,
    })
    monkeypatch.setattr(pipeline, "run_simulation", fake_sim)
    monkeypatch.setattr(pipeline, "record_passing_example", lambda *args, **kwargs: Path("verified.v"))

    result = pipeline.run_once(model="stub-model", max_repairs=1, config=run_config)

    assert result["final"] == "pass"
    assert [attempt["failed_checks"] for attempt in result["attempts"]] == [["stop"], []]
    assert calls["count"] == 2
    assert Path(run_config.results_log).is_file()
    # Per-attempt artifacts are isolated under the run directory.
    run_dir = Path(result["run_dir"])
    assert (run_dir / "attempt_0.v").is_file()
    assert (run_dir / "attempt_1.v").is_file()
    assert (run_dir / "attempt_0_prompt.txt").is_file()


def test_repair_prompt_preserves_valid_phases():
    prompt = rtl_generator.REPAIR_PROMPT.format(
        failure_kind="compiled but failed protocol checks",
        code="module top_glue; endmodule",
        failure_detail="No STOP condition was observed; tb_done was never asserted.",
        verified_context="context",
    )
    prompt += "\nObserved-failure preservation contract:\n" + rtl_generator._repair_discipline(
        "No STOP condition was observed; tb_done was never asserted."
    )
    assert "address ACK" in prompt and "data ACK" in prompt
    assert "Already-valid phases" in prompt


def test_failure_analysis_parses_model_hypothesis_without_marking_it_verified(monkeypatch):
    expected = {
        "failure_classification": "missing_stop",
        "root_cause_hypothesis": "The FSM may leave STOP before SDA is released.",
        "supporting_evidence": ["judge did not observe STOP"],
        "confidence": 0.7,
    }
    monkeypatch.setattr(rtl_generator, "generate_verilog", lambda *_args, **_kwargs: {
        "content": json.dumps(expected), "provider_model": "analyst-model", "prompt_eval_count": 4,
    })
    result = rtl_generator.analyze_rtl_failures(
        "module top_glue; endmodule", [SimpleNamespace(
            stage="judge", failed_checks=["stop"], diagnostics="STOP not observed",
            stderr="", stdout="protocol failure",
        )], model="analyst-model",
    )
    assert result["status"] == "model_hypothesis"
    assert result["model"] == "analyst-model"
    assert result["root_cause_hypothesis"] == expected["root_cause_hypothesis"]
    assert result["provider_metadata"]["prompt_eval_count"] == 4
