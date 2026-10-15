"""Small-model generation uses a deterministic wrapper around an RTL fragment."""
from pathlib import Path

import agent.rtl_generator as generator
from tools.structural_checks import structural_checks

ROOT = Path(__file__).resolve().parents[1]


def test_fragment_response_is_wrapped_with_fixed_ports_instances_and_bus_drivers(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    fragment = '''
reg [1:0] state;
reg sda_drive_low;
reg scl_drive_low;
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
    assert rtl.count("reg sda_drive_low;") == 1
    assert rtl.count("reg scl_drive_low;") == 1


def test_full_module_response_is_reduced_to_deterministic_wrapper():
    full = """module top_glue(input wire clk, input wire rst_n, inout wire sda,
      inout wire scl, input wire tb_start, output reg tb_done);
      localparam ADDR_BYTE = 8'hA0;
      reg sda_oe;
      reg scl_oe;
      assign sda = sda_oe ? 1'b0 : 1'bz;
      assign scl = scl_oe ? 1'b0 : 1'bz;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sda_oe <= 0; scl_oe <= 0; tb_done <= 0; end
        else if (tb_start) tb_done <= 1;
      end
    endmodule"""
    fragment = generator._coerce_full_module_to_fragment(full)
    assert "module top_glue" not in fragment
    assert "assign sda" not in fragment
    assert "sda_drive_low" in fragment and "scl_drive_low" in fragment


def test_repair_response_uses_same_deterministic_multi_device_wrapper(tmp_path):
    full = """module top_glue(input wire clk, input wire rst_n, inout wire sda,
      inout wire scl, input wire tb_start, output reg tb_done);
      device_sensor u_stale_sensor(.clk(clk), .rst_n(rst_n));
      device_controller u_stale_controller(.clk(clk), .rst_n(rst_n));
      reg sda_oe;
      reg scl_oe;
      assign sda = sda_oe ? 1'b0 : 1'bz;
      assign scl = scl_oe ? 1'b0 : 1'bz;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sda_oe <= 0; scl_oe <= 0; tb_done <= 0; end
        else if (tb_start) tb_done <= 1;
      end
    endmodule"""
    repaired = generator.normalize_model_rtl(
        full, ["devices/device_sensor.v", "devices/device_controller.v"]
    )
    checks = structural_checks(repaired, ["device_sensor", "device_controller"])
    assert checks["pass"], checks["failed_checks"]
    assert repaired.count("device_sensor u_device_a") == 1
    assert repaired.count("device_controller u_device_b") == 1
    assert "u_stale_sensor" not in repaired
    assert repaired.count("assign sda = sda_drive_low ? 1'b0 : 1'bz;") == 1


def test_fragment_missing_protocol_constants_gets_scaffolded():
    fragment = """
reg [3:0] state;
reg [7:0] half_cnt;
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) state <= IDLE;
  else if (state == START) state <= STOP_HIGH;
  else if (half_done) state <= DONE;
end
"""
    rtl = generator.normalize_model_rtl(
        fragment, ["devices/device_sensor.v", "devices/device_controller.v"]
    )
    assert "localparam IDLE = 0;" in rtl
    assert "localparam START = 1;" in rtl
    assert "wire half_done;" in rtl
    assert rtl.count("localparam HALF = 4;") == 1
