"""Tool-free integration test of the full generate/repair orchestration."""

from pathlib import Path

import agent.rtl_generator as rtl_generator
import agent_core.agent as pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_model_repair_loop_recovers_from_missing_stop(tmp_path, monkeypatch):
    broken = (ROOT / "protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read_text()
    good = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    calls = {"count": 0}

    def fake_generate(prompt, model=None, temperature=None):
        calls["count"] += 1
        code = broken if calls["count"] == 1 else good
        return f"```verilog\n{code}\n```"

    sim_results = iter([
        {
            "success": True, "returncode": 0,
            "stdout": "RESULT:saw_start=1 saw_stop=0 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
            "stderr": "", "timeout": False,
        },
        {
            "success": True, "returncode": 0,
            "stdout": "RESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
            "stderr": "", "timeout": False,
        },
    ])

    monkeypatch.setattr(rtl_generator, "generate_verilog", fake_generate)
    monkeypatch.setattr(pipeline, "compile_verilog", lambda *args, **kwargs: {
        "success": True, "stdout": "", "stderr": "", "returncode": 0,
    })
    monkeypatch.setattr(pipeline, "run_simulation", lambda *args, **kwargs: next(sim_results))
    monkeypatch.setattr(pipeline, "TOP", str(tmp_path / "top_glue.v"))
    monkeypatch.setattr(pipeline, "SIM_OUT", str(tmp_path / "sim.out"))
    monkeypatch.setattr(pipeline, "RESULTS_LOG", str(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(pipeline, "record_passing_example", lambda *args, **kwargs: tmp_path / "verified.v")

    result = pipeline.run_once(model="stub-model", max_repairs=2)

    assert result["final"] == "pass"
    assert [attempt["failed_checks"] for attempt in result["attempts"]] == [["stop"], []]
    assert calls["count"] == 2
    assert Path(pipeline.RESULTS_LOG).is_file()


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
