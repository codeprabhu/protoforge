from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import subprocess

from tools.rtl_memory import record_failed_example, record_passing_example, retrieve_rtl_context
from tools.simulation_tool import parse_result_line, run_simulation
from tools.structural_checks import structural_checks

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_reference_passes_structural_gate():
    code = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    result = structural_checks(code, ["device_sensor", "device_controller"])
    assert result["pass"] is True, result["failed_checks"]


def test_reference_without_stop_still_passes_shape_gate():
    # The structural gate checks RTL shape; the simulation oracle is
    # responsible for finding functional protocol defects such as missing STOP.
    code = (ROOT / "protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read_text()
    result = structural_checks(code, ["device_sensor", "device_controller"])
    assert result["pass"] is True, result["failed_checks"]


def test_structural_gate_rejects_delay_and_procedural_bus_drives():
    code = '''module top_glue(input wire clk, input wire rst_n, inout wire sda,
      inout wire scl, input wire tb_start, output reg tb_done);
      device_sensor u_sensor (.clk(clk), .rst_n(rst_n));
      device_controller u_controller (.clk(clk), .rst_n(rst_n));
      always @(posedge clk) begin sda <= 1'b0; #10 tb_done <= 1'b1; end
      assign scl = 1'b0;
    endmodule'''
    result = structural_checks(code, ["device_sensor", "device_controller"])
    assert "no_delay_statements" in result["failed_checks"]
    assert "no_procedural_bus_writes" in result["failed_checks"]
    assert "open_drain_sda" in result["failed_checks"]


def test_result_line_parsing():
    parsed = parse_result_line(
        "EVENT:START:12\nRESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 "
        "saw_ack_data=1 addr=a0 data=3c\n"
    )
    assert parsed == {
        "parsed": True,
        "authenticated": False,
        "saw_start": 1,
        "saw_stop": 1,
        "saw_ack_addr": 1,
        "saw_ack_data": 1,
        "addr": "a0",
        "data": "3c",
    }


def test_simulation_wrapper_reports_failure_for_a_nonzero_exit():
    failed = SimpleNamespace(returncode=1, stdout="", stderr="ERROR: iverilog failed")
    with patch("tools.simulation_tool.subprocess.run", return_value=failed):
        result = run_simulation("dummy.out")
    assert result["success"] is False
    assert result["timeout"] is False
    assert "ERROR: iverilog failed" in result["stderr"]


def test_simulation_wrapper_marks_a_timeout_as_a_timeout():
    timed_out = subprocess.TimeoutExpired(cmd="vvp", timeout=30)
    with patch("tools.simulation_tool.subprocess.run", side_effect=timed_out):
        result = run_simulation("dummy.out")
    assert result["success"] is False
    assert result["timeout"] is True


def test_memory_only_retrieves_verified_passing_examples(tmp_path):
    good = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    failed = (
        "// BROKEN-DESIGN-MARKER\n"
        + (ROOT / "protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read_text()
    )
    passing_path = record_passing_example(good, "unit-model", {"protocol": "i2c-v1"}, tmp_path)
    record_failed_example(failed, "unit-model", "simulate", ["stop"], memory_dir=tmp_path)

    context = retrieve_rtl_context(
        "I2C start stop address 0xA0 ACK SDA SCL", max_examples=4, max_chars=30000,
        memory_dir=tmp_path,
    )
    assert passing_path.stem in context
    # The failed snapshot is stored on disk but must never reach the prompt.
    assert (tmp_path / "failed").exists()
    assert "BROKEN-DESIGN-MARKER" not in context
    assert "RETRIEVED VERIFIED EXAMPLE" in context


def test_ollama_client_uses_stable_options_without_real_ollama(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from agent import ollama_client

    seen = {}

    def fake_chat(**kwargs):
        seen.update(kwargs)
        return {"message": {"content": "module top_glue; endmodule"}}

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(chat=fake_chat))
    response = ollama_client.generate_verilog("write an I2C master", model="qwen-test")

    assert response == "module top_glue; endmodule"
    assert seen["model"] == "qwen-test"
    # Reproducibility contract: these are the recorded stable defaults, pinned as
    # literals so an accidental drift fails the suite instead of silently
    # invalidating every committed baseline number.
    assert seen["options"] == {
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "num_predict": 4096,
    }


def test_tampered_passing_memory_entry_is_not_retrieved(tmp_path):
    from tools.rtl_memory import _load_successful_examples

    good = (ROOT / "protocols/i2c/reference/good_master_reset_safe.v").read_text()
    path = record_passing_example(good, "unit-model", {}, tmp_path)
    path.write_text(good + "\n// edited after verification\n")
    examples = _load_successful_examples(tmp_path)
    assert not any(origin == "learned" for _, _, origin in examples)
