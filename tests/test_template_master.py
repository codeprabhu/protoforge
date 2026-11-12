"""Phase 5: the deterministic template must pass every gate.

These tests are the upper-bound baseline. If the template fails, either the
template or the oracle is wrong; a model failure cannot be blamed on an
impossible target. Real Icarus/Verilator/Yosys are used when available.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_core.agent import run_once
from agent_core.config import Config
from baseline.template_master import generate_template_rtl
from tools.compile_tool import compile_verilog
from tools.hardware_checks import run_hardware_checks
from tools.i2c_spec import parse_i2c_spec
from tools.judge import judge
from tools.simulation_tool import parse_result_line, run_simulation
from tools.structural_checks import structural_checks

ROOT = Path(__file__).resolve().parents[1]
TB = ROOT / "protocols/i2c/testbench_i2c.v"
DEVICES = [
    ROOT / "devices/device_sensor.v",
    ROOT / "devices/device_controller.v",
]
SPEC = ROOT / "protocols/i2c/spec.md"
SPEC_2BYTE = ROOT / "protocols/i2c/spec_2byte.md"
GOLD_2BYTE = ROOT / "protocols/i2c/reference/good_master_2byte_reset_safe.v"
GOLD_1BYTE = ROOT / "protocols/i2c/reference/good_master_reset_safe.v"
GOLD_3BYTE = ROOT / "protocols/i2c/reference/good_master_3byte_reset_safe.v"

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None
    or shutil.which("vvp") is None
    or shutil.which("verilator") is None
    or shutil.which("yosys") is None,
    reason="Icarus Verilog, Verilator and Yosys are required",
)


def _run_gates(
    rtl: str,
    tmp_path: Path,
    device_paths: list[Path],
    spec,
    *,
    expected_instances: list[str] | None = None,
) -> tuple[dict, dict, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    required = expected_instances or [Path(path).stem for path in device_paths]
    struct = structural_checks(rtl, required)
    assert struct["pass"], struct["failed_checks"]

    top = tmp_path / "top_glue.v"
    top.write_text(rtl, encoding="utf-8")
    vvp = tmp_path / "sim.vvp"
    compiled = compile_verilog([*map(str, device_paths), str(top), str(TB)], out=str(vvp))
    assert compiled["success"], compiled["stderr"]

    hardware = run_hardware_checks([*map(str, device_paths), str(top)])
    assert hardware["success"], hardware

    plusargs = [
        f"+I2C_ADDR={spec.address_hex}",
        f"+I2C_DATA={spec.data_hex}",
        f"+I2C_NBYTES={spec.nbytes}",
    ]
    nonce = os.urandom(8).hex()
    plusargs.append(f"+NONCE={nonce}")
    simulated = run_simulation(str(vvp), plusargs=plusargs)
    assert simulated["success"], simulated["stderr"]
    parsed = parse_result_line(simulated["stdout"], nonce=nonce)
    verdict = judge(
        parsed,
        expected_addr=spec.address_hex,
        expected_data=spec.data_hex,
        expected_data_bytes=spec.nbytes,
        expected_data_list=spec.data_hexes,
    )
    return verdict, parsed, simulated["stdout"]


def test_default_template_passes_all_gates(tmp_path):
    spec = parse_i2c_spec(str(SPEC))
    rtl = generate_template_rtl(spec_path=str(SPEC))
    verdict, parsed, _ = _run_gates(rtl, tmp_path, DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["bytes"] == 2 and parsed["sda_glitches"] == 0


@pytest.mark.parametrize(
    "address,data",
    [(0x50, 0x3C), (0x2A, 0x5A), (0x10, 0x00), (0x77, 0xFF), (0x03, 0x81)],
)
def test_template_supports_five_address_data_pairs(address, data, tmp_path):
    spec_path = tmp_path / f"spec_{address:02x}.md"
    spec_path.write_text(
        f"Master writes ONE byte, `0x{data:02X}`, to slave address 0x{address:02X}.\n",
        encoding="utf-8",
    )
    spec = parse_i2c_spec(str(spec_path))
    rtl = generate_template_rtl(spec_path=str(spec_path))
    verdict, parsed, _ = _run_gates(rtl, tmp_path, DEVICES, spec)
    assert verdict["pass"], (verdict, parsed)
    assert parsed["addr"] == spec.address_hex
    assert parsed["data"] == spec.data_hex


def test_template_supports_one_device(tmp_path):
    devices = [DEVICES[0]]
    spec = parse_i2c_spec(str(SPEC))
    rtl = generate_template_rtl(device_paths=[str(path) for path in devices], spec_path=str(SPEC))
    assert "device_controller" not in rtl
    verdict, _, _ = _run_gates(rtl, tmp_path, devices, spec, expected_instances=["device_sensor"])
    assert verdict["pass"], verdict


def test_template_supports_three_devices(tmp_path):
    third = tmp_path / "device_timer.v"
    third.write_text(
        """module device_timer (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       start,
    input  wire [7:0] period,
    output reg        tick
);
    reg [7:0] count;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 8'h00;
            tick  <= 1'b0;
        end else if (start) begin
            if (count >= period) begin
                count <= 8'h00;
                tick  <= 1'b1;
            end else begin
                count <= count + 1'b1;
                tick  <= 1'b0;
            end
        end else begin
            tick <= 1'b0;
        end
    end
endmodule
""",
        encoding="utf-8",
    )
    devices = [*DEVICES, third]
    spec = parse_i2c_spec(str(SPEC))
    rtl = generate_template_rtl(device_paths=[str(path) for path in devices], spec_path=str(SPEC))
    assert "u_device_c" in rtl and "device_timer" in rtl
    verdict, _, _ = _run_gates(
        rtl, tmp_path, devices, spec,
        expected_instances=["device_sensor", "device_controller", "device_timer"],
    )
    assert verdict["pass"], verdict


def test_two_byte_template_and_reference_pass(tmp_path):
    spec = parse_i2c_spec(str(SPEC_2BYTE))
    template = generate_template_rtl(spec_path=str(SPEC_2BYTE))
    verdict, parsed, _ = _run_gates(template, tmp_path / "template", DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["data_bytes"] == ["11", "22"]
    assert parsed["bytes"] == 3

    reference = GOLD_2BYTE.read_text(encoding="utf-8")
    ref_verdict, ref_parsed, _ = _run_gates(reference, tmp_path / "reference", DEVICES, spec)
    assert ref_verdict["pass"], ref_verdict
    assert ref_parsed["data_bytes"] == ["11", "22"]


def test_two_byte_reference_specializes_to_a_new_payload(tmp_path):
    """The deterministic scaffold must rewrite 0-based payload constants.

    The checked-in 2-byte reference uses DATA_BYTE_0/DATA_BYTE_1. When it is
    reused for a different spec, those values must be rewritten to the new
    payload instead of silently writing 0x11/0x22.
    """
    from agent.rtl_generator import normalize_model_rtl

    spec_path = tmp_path / "spec_2byte_alt.md"
    spec_path.write_text(
        "Master writes TWO bytes, `0xDE`, `0xAD`, to slave address 0x48.\n",
        encoding="utf-8",
    )
    spec = parse_i2c_spec(str(spec_path))
    rtl = normalize_model_rtl(
        GOLD_2BYTE.read_text(encoding="utf-8"),
        [str(path) for path in DEVICES],
        spec.address_hex,
        spec.data_hex,
        spec.data_hexes,
    )
    verdict, parsed, _ = _run_gates(rtl, tmp_path, DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["data_bytes"] == ["de", "ad"]


def test_three_byte_reference_passes_every_gate(tmp_path):
    """The new 3-byte seed must itself pass every gate.

    A retrieval seed that fails the oracle would teach the model a wrong
    design, so this is the precondition for using it.
    """
    spec_path = tmp_path / "spec_3byte.md"
    spec_path.write_text(
        "Master writes THREE bytes, `0x01`, `0x02`, `0x03`, "
        "to slave address 0x50.\n",
        encoding="utf-8",
    )
    spec = parse_i2c_spec(str(spec_path))
    assert spec.nbytes == 3

    verdict, parsed, _ = _run_gates(GOLD_3BYTE.read_text(encoding="utf-8"), tmp_path, DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["data_bytes"] == ["01", "02", "03"]


def test_three_byte_reference_specializes_to_another_payload(tmp_path):
    """The 0-based payload constants must be rewritten, not copied."""
    from agent.rtl_generator import normalize_model_rtl

    spec_path = tmp_path / "spec_3byte_alt.md"
    spec_path.write_text(
        "Master writes THREE bytes, `0xDE`, `0xAD`, `0xBE`, "
        "to slave address 0x48.\n",
        encoding="utf-8",
    )
    spec = parse_i2c_spec(str(spec_path))
    rtl = normalize_model_rtl(
        GOLD_3BYTE.read_text(encoding="utf-8"),
        [str(path) for path in DEVICES],
        spec.address_hex,
        spec.data_hex,
        spec.data_hexes,
    )
    assert "localparam DATA_BYTE_0 = 8'hDE;" in rtl
    assert "localparam DATA_BYTE_1 = 8'hAD;" in rtl
    assert "localparam DATA_BYTE_2 = 8'hBE;" in rtl

    verdict, parsed, _ = _run_gates(rtl, tmp_path, DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["data_bytes"] == ["de", "ad", "be"]


def test_three_byte_design_fails_the_two_byte_spec(tmp_path):
    """Byte count is checked: a 3-byte design must not pass a 2-byte spec."""
    spec = parse_i2c_spec(str(SPEC_2BYTE))
    verdict, parsed, _ = _run_gates(GOLD_3BYTE.read_text(encoding="utf-8"), tmp_path, DEVICES, spec)
    assert not verdict["pass"]
    assert "byte_count" in verdict["failed_checks"]


def test_one_based_payload_reference_passes_every_gate(tmp_path):
    """A model that uses 1-based DATA_BYTE_1/DATA_BYTE_2 must not be corrupted.

    Recorded multi-byte candidates wrote correct 1-based declarations, but the
    scaffold also injected 0-based aliases and rewrote DATA_BYTE_1 to the
    second payload byte. This replays that naming scheme end to end.
    """
    import re

    from agent.rtl_generator import normalize_model_rtl

    spec_path = tmp_path / "spec_2byte_alt.md"
    spec_path.write_text(
        "Master writes TWO bytes, `0xDE`, `0xAD`, to slave address 0x48.\n",
        encoding="utf-8",
    )
    spec = parse_i2c_spec(str(spec_path))
    source = GOLD_2BYTE.read_text(encoding="utf-8")
    one_based = re.sub(
        r"\bDATA_BYTE_(\d+)\b",
        lambda match: f"DATA_BYTE_{int(match.group(1)) + 1}",
        source,
    )
    assert "DATA_BYTE_0" not in one_based
    rtl = normalize_model_rtl(
        one_based,
        [str(path) for path in DEVICES],
        spec.address_hex,
        spec.data_hex,
        spec.data_hexes,
    )
    assert "localparam DATA_BYTE_1 = 8'hDE;" in rtl
    assert "localparam DATA_BYTE_2 = 8'hAD;" in rtl
    verdict, parsed, _ = _run_gates(rtl, tmp_path, DEVICES, spec)
    assert verdict["pass"], verdict
    assert parsed["data_bytes"] == ["de", "ad"]


def test_one_byte_design_fails_two_byte_spec(tmp_path):
    spec = parse_i2c_spec(str(SPEC_2BYTE))
    one_byte = GOLD_1BYTE.read_text(encoding="utf-8")
    verdict, parsed, _ = _run_gates(one_byte, tmp_path, DEVICES, spec)
    assert not verdict["pass"]
    assert "byte_count" in verdict["failed_checks"], verdict["failed_checks"]
    assert parsed["bytes"] == 2  # address + one data byte, not the required three


def test_template_output_is_deterministic():
    first = generate_template_rtl(spec_path=str(SPEC_2BYTE))
    second = generate_template_rtl(spec_path=str(SPEC_2BYTE))
    assert first == second


def test_live_pipeline_runs_two_byte_spec(tmp_path):
    # Exercise the adapter's +I2C_NBYTES/judge plumbing, not just the raw
    # simulator helper: a stubbed model returns the known-good template.
    template = generate_template_rtl(spec_path=str(SPEC_2BYTE))
    config = Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / "runs.jsonl"),
        trace_dir=str(tmp_path / "traces"),
        spec_path=str(SPEC_2BYTE),
        max_repairs=0,
    )
    result = run_once(
        model="template-stub",
        config=config,
        generate_fn=lambda *args, **kwargs: template,
        on_success=lambda *args, **kwargs: None,
        on_failure=lambda *args, **kwargs: None,
    )
    assert result["final"] == "pass", result
    record = json.loads((Path(result["run_dir"]) / "attempt_0.json").read_text())
    judge_stage = next(stage for stage in record["stages"] if stage["stage"] == "judge")
    assert judge_stage["passed"]
    assert judge_stage["metadata"]["raw"]["data_bytes"] == ["11", "22"]
