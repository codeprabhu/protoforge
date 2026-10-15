"""Regression tests for contract-driven multi-device wrapper generation."""

from pathlib import Path

import pytest

from tools.verilog_parser import parse_verilog_source
from agent.rtl_generator import build_device_instance_blocks


def test_parser_rejects_multiple_modules_in_one_device_file():
    source = """
    module first(input wire clk, input wire rst_n); endmodule
    module second(input wire clk, input wire rst_n); endmodule
    """
    with pytest.raises(ValueError, match="exactly one module"):
        parse_verilog_source(source)


def test_parser_rejects_device_without_reset():
    source = "module bad(input wire clk, output wire ready); endmodule"
    with pytest.raises(ValueError, match="clk.*rst_n"):
        parse_verilog_source(source)


def test_three_heterogeneous_devices_get_unique_instances_and_signals(tmp_path):
    files = []
    modules = {
        "sensor.v": """module sensor(input wire clk, input wire rst_n, input wire wr, input wire [7:0] addr, output wire ready); endmodule""",
        "actuator.v": """module actuator(input wire clk, input wire rst_n, input wire start, input wire [3:0] mode, output wire busy); endmodule""",
        "status.v": """module status(input wire clk, input wire rst_n, output wire [15:0] value); endmodule""",
    }
    for name, source in modules.items():
        path = tmp_path / name
        path.write_text(source)
        files.append(str(path))

    blocks = build_device_instance_blocks(files)

    assert [b["info"].name for b in blocks] == ["sensor", "actuator", "status"]
    assert [b["instance_name"] for b in blocks] == [
        "u_device_a", "u_device_b", "u_device_c"
    ]
    text = "\n".join(b["code"] for b in blocks)
    assert text.count(".clk(clk)") == 3
    assert "a_addr" in text and "b_mode" in text and "c_value" in text
