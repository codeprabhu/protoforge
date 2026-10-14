"""Small-model generation uses a deterministic wrapper around an RTL fragment."""
from pathlib import Path

import agent.rtl_generator as generator
from tools.structural_checks import structural_checks

ROOT = Path(__file__).resolve().parents[1]


def test_fragment_response_is_wrapped_with_fixed_ports_instances_and_bus_drivers(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    fragment = '''
reg [1:0] state;
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin
    state <= 0;
    tb_done <= 0;
    sda_drive_low <= 0;
    scl_drive_low <= 0;
  end else begin
    case (state)
      0: if (tb_start) state <= 1;
      1: begin tb_done <= 1; state <= 0; end
      default: state <= 0;
    endcase
  end
end
'''
    monkeypatch.setattr(generator, "generate_verilog", lambda *a, **k: fragment)
    monkeypatch.setattr(generator, "save_file", lambda path, content: Path(path).write_text(content))
    out = tmp_path / "top_glue.v"
    generator.generate_glue_rtl(
        "devices/device_sensor.v", "devices/device_controller.v",
        "protocols/i2c/spec.md", out_path=str(out), debug_prompt_path=None,
        model="stub", seed=42,
    )
    rtl = out.read_text()
    checks = structural_checks(rtl, ["device_sensor", "device_controller"])
    assert checks["pass"], checks["failed_checks"]
    assert rtl.count("module top_glue") == 1
    assert rtl.count("device_sensor u_device_a") == 1
    assert rtl.count("device_controller u_device_b") == 1
    assert "assign sda = sda_drive_low ? 1'b0 : 1'bz;" in rtl
