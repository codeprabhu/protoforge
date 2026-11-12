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
        "protocols/i2c/spec.md", out_path=str(out), model="stub", seed=42,
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


def test_half_referenced_inside_wire_declaration_is_not_treated_as_declared():
    """Regression: `wire half_done = (half_cnt == HALF - 1);` is not a HALF declaration.

    The enricher used to see the token HALF anywhere in a wire statement and
    skip `localparam HALF = 4;`, so the first candidate failed compilation with
    "Unable to bind wire/reg/memory HALF". Every single-byte baseline run hit
    this and only passed after repair.
    """
    fragment = """
reg [3:0] state;
reg [7:0] half_cnt;
wire half_done = (half_cnt == HALF - 1);
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) state <= IDLE;
  else if (half_done) state <= START;
end
"""
    rtl = generator.normalize_model_rtl(
        fragment, ["devices/device_sensor.v", "devices/device_controller.v"]
    )
    assert rtl.count("localparam HALF = 4;") == 1
    assert rtl.count("wire half_done;") == 0
    assert "wire half_done = (half_cnt == HALF - 1);" in rtl


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


def test_model_instance_blocks_and_bus_assigns_are_dropped():
    """Regression: a contract-violating response must not duplicate the scaffold.

    Two pre-fix baseline runs failed the structural gate only because the
    repair fragment re-emitted device instances and `assign sda`/`assign scl`
    drivers that the harness owns. The failure was charged to the candidate.
    """
    fragment = """
reg [3:0] state;
reg sda_drive_low;
reg scl_drive_low;
assign sda = sda_drive_low ? 1'b0 : 1'bz;
assign scl = scl_drive_low ? 1'b0 : 1'bz;
device_sensor u_device_a (
    .clk(clk), .rst_n(rst_n)
);
device_controller u_device_b (
    .clk(clk), .rst_n(rst_n)
);
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin state <= IDLE; tb_done <= 0; end
  else if (tb_start) state <= START;
end
"""
    rtl = generator.normalize_model_rtl(
        fragment, ["devices/device_sensor.v", "devices/device_controller.v"]
    )
    checks = structural_checks(rtl, ["device_sensor", "device_controller"])
    assert checks["pass"], checks["failed_checks"]
    assert rtl.count("device_sensor u_device_a") == 1
    assert rtl.count("device_controller u_device_b") == 1
    assert rtl.count("assign sda = sda_drive_low ? 1'b0 : 1'bz;") == 1
    assert rtl.count("assign scl = scl_drive_low ? 1'b0 : 1'bz;") == 1


def test_real_failing_repair_artifact_normalizes_cleanly():
    """The two recorded structural failures must pass the gate after this fix."""
    import json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    run_ids = ["20261111T194854-927cb916", "20261111T205616-70697215"]
    for run_id in run_ids:
        attempt = root / "results" / "bench_baseline_v3" / "runs" / run_id / "attempt_2.json"
        if not attempt.is_file():
            continue
        raw = json.loads(attempt.read_text()).get("raw_output", "")
        devices = ["devices/device_sensor.v", "devices/device_controller.v"]
        rtl = generator.normalize_model_rtl(raw, devices)
        assert structural_checks(rtl, ["device_sensor", "device_controller"])["pass"], run_id


def test_multi_byte_payload_constants_are_scaffolded_from_the_spec():
    """Regression: DATA_BYTE1/DATA_BYTE2 must be supplied, not left undefined.

    The model references the payload bytes by these names. A candidate that
    omits the declarations used to fail with
    "Unable to bind wire/reg/memory DATA_BYTE1" -- a harness-owned compile
    failure, because the deterministic wrapper is responsible for the
    protocol constants.
    """
    fragment = """
reg [3:0] state;
reg [7:0] cur_byte;
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin state <= IDLE; tb_done <= 0; end
  else begin
    cur_byte <= DATA_BYTE1;
    cur_byte <= DATA_BYTE2;
    state <= STOP_LOW;
  end
end
"""
    rtl = generator.normalize_model_rtl(
        fragment,
        ["devices/device_sensor.v", "devices/device_controller.v"],
        "50", "11", ["11", "22"],
    )
    assert "localparam DATA_BYTE1 = 8'h11;" in rtl
    assert "localparam DATA_BYTE2 = 8'h22;" in rtl


def test_stale_payload_literal_is_rewritten_to_the_spec_value():
    """A payload constant copied from the retrieved example must not win."""
    fragment = """
localparam DATA_BYTE1 = 8'h11;
localparam DATA_BYTE2 = 8'h22;
reg [3:0] state;
reg [7:0] cur_byte;
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin state <= IDLE; tb_done <= 0; end
  else cur_byte <= DATA_BYTE1;
end
"""
    rtl = generator.normalize_model_rtl(
        fragment,
        ["devices/device_sensor.v", "devices/device_controller.v"],
        "48", "de", ["de", "ad"],
    )
    assert "localparam DATA_BYTE1 = 8'hDE;" in rtl
    assert "localparam DATA_BYTE2 = 8'hAD;" in rtl
    assert "8'h11" not in rtl
    assert "8'h22" not in rtl


def test_real_multi_byte_artifact_keeps_its_payload_constants():
    """The recorded multi-byte compile failures were harness-owned.

    In two recorded c06 runs the model declared DATA_BYTE1/DATA_BYTE2 inside
    its full module, but slicing at the last localparam dropped them before
    compilation. Replaying those raw responses must now produce declarations.
    """
    import json

    root = Path(__file__).resolve().parents[1]
    run_ids = ["20261111T224705-39101990", "20261111T230216-0a2ec39c"]
    devices = ["devices/device_sensor.v", "devices/device_controller.v"]
    replayed = 0
    artifacts_present = False
    for run_id in run_ids:
        run_dir = root / "results" / "bench_multibyte_v4" / "runs" / run_id
        if not run_dir.is_dir():
            continue
        artifacts_present = True
        for attempt in sorted(run_dir.glob("attempt_*.json")):
            raw = json.loads(attempt.read_text()).get("raw_output", "")
            if "DATA_BYTE1" not in raw and "DATA_BYTE2" not in raw:
                continue
            replayed += 1
            rtl = generator.normalize_model_rtl(raw, devices, "50", "11", ["11", "22"])
            if "DATA_BYTE1" in rtl:
                assert "localparam DATA_BYTE1 = 8'h11;" in rtl, attempt
            if "DATA_BYTE2" in rtl:
                assert "localparam DATA_BYTE2 = 8'h22;" in rtl, attempt
    # Run directories are gitignored, so CI skips the replay; the unit tests
    # above still cover the fix. Locally the three affected attempts must be
    # found and normalized with declarations.
    if artifacts_present:
        assert replayed == 3


def test_retrieved_two_byte_reference_keeps_its_constant_declarations():
    """`_behavior_context` must not leave DATA_BYTE_0/DATA_BYTE_1 undefined.

    The 2-byte seed declares them above its device instances and HALF below,
    so the old slice dropped the declarations while the always block kept
    referencing them.
    """
    reference = (
        Path(__file__).resolve().parents[1]
        / "protocols" / "i2c" / "reference" / "good_master_2byte_reset_safe.v"
    )
    context = generator._behavior_context(reference.read_text())
    assert "DATA_BYTE_0" in context and "DATA_BYTE_1" in context
    assert "localparam DATA_BYTE_0" in context
    assert "localparam DATA_BYTE_1" in context


def test_zero_based_payload_constants_are_specialized_to_the_spec():
    fragment = """
localparam DATA_BYTE_0 = 8'h11;
localparam DATA_BYTE_1 = 8'h22;
reg [3:0] state;
reg [7:0] cur_byte;
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin state <= IDLE; tb_done <= 0; end
  else cur_byte <= DATA_BYTE_0;
end
"""
    rtl = generator.normalize_model_rtl(
        fragment,
        ["devices/device_sensor.v", "devices/device_controller.v"],
        "48", "de", ["de", "ad"],
    )
    assert "localparam DATA_BYTE_0 = 8'hDE;" in rtl
    assert "localparam DATA_BYTE_1 = 8'hAD;" in rtl
