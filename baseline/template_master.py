"""Deterministic I2C master template.

This is the upper-bound baseline for the generation task: a hand-written,
parameterised RTL emitter that never calls a model. It proves the oracle
accepts a correct design for any supported address/data/device combination,
so a model failure cannot be blamed on an impossible target.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from agent.rtl_generator import _assemble_multi_fragment, build_device_instance_blocks
from tools.i2c_spec import parse_i2c_spec

DEFAULT_DEVICE_PATHS = (
    "devices/device_sensor.v",
    "devices/device_controller.v",
)
DEFAULT_ADDRESS = 0x50
DEFAULT_DATA_BYTES = (0x3C,)
DEFAULT_HALF = 4


def _device_input_defaults(blocks: list[dict]) -> list[str]:
    """Reset assignments for every device input so simulation has no X inputs."""
    lines: list[str] = []
    for block in blocks:
        for port in block["info"].ports:
            if port.direction == "input" and port.name not in ("clk", "rst_n"):
                lines.append(f"            {block['signals'][port.name]} <= 0;")
    return lines


def _next_byte_branches(data_bytes: Sequence[int]) -> list[str]:
    """Emit the byte-index dispatch that loads each data byte in order."""
    lines: list[str] = []
    for index, _ in enumerate(data_bytes):
        keyword = "if" if index == 0 else "else if"
        lines.extend([
            f"                    {keyword} (byte_idx == {index}) begin",
            f"                        cur_byte <= DATA_BYTE_{index};",
            f"                        byte_idx <= {index + 1};",
            "                        bit_idx  <= 3'd7;",
            "                        state    <= BIT_LOW;",
            "                    end",
        ])
    lines.extend([
        "                    else begin",
        "                        state <= STOP_LOW;",
        "                    end",
    ])
    return lines


def _fsm_fragment(
    address_byte: int,
    data_bytes: Sequence[int],
    blocks: list[dict],
    half: int,
) -> str:
    if not 1 <= half <= 255:
        raise ValueError("HALF must be between 1 and 255 clock cycles")
    if not data_bytes:
        raise ValueError("At least one data byte is required")

    lines = [
        f"    localparam ADDR_BYTE = 8'h{address_byte:02X};",
    ]
    lines.extend(
        f"    localparam DATA_BYTE_{index} = 8'h{byte:02X};"
        for index, byte in enumerate(data_bytes)
    )
    lines.extend([
        f"    localparam HALF = {half};",
        "",
        "    localparam IDLE = 0, START = 1, START_HOLD = 2, BIT_LOW = 3,",
        "               BIT_HIGH = 4, ACK_LOW = 5, ACK_HIGH = 6, NEXT_BYTE = 7,",
        "               STOP_LOW = 8, STOP_HIGH = 9, DONE = 10;",
        "",
        "    reg [3:0]  state;",
        "    reg [2:0]  bit_idx;",
        "    reg [7:0]  byte_idx;",
        "    reg [7:0]  cur_byte;",
        "    reg [7:0]  half_cnt;",
        "",
        "    task automatic advance_half;",
        "        begin",
        "            if (half_cnt == HALF - 1) half_cnt <= 0;",
        "            else half_cnt <= half_cnt + 1;",
        "        end",
        "    endtask",
        "",
        "    wire half_done = (half_cnt == HALF - 1);",
        "",
        "    always @(posedge clk or negedge rst_n) begin",
        "        if (!rst_n) begin",
        "            state         <= IDLE;",
        "            sda_drive_low <= 0;",
        "            scl_drive_low <= 0;",
        "            tb_done       <= 0;",
        "            half_cnt      <= 0;",
        "            bit_idx       <= 0;",
        "            byte_idx      <= 0;",
        "            cur_byte      <= 8'h00;",
        "            // Device inputs are held at zero; the oracle observes the I2C bus.",
        *_device_input_defaults(blocks),
        "        end else begin",
        "            case (state)",
        "",
        "                IDLE: begin",
        "                    sda_drive_low <= 0;",
        "                    scl_drive_low <= 0;",
        "                    tb_done <= 0;",
        "                    if (tb_start) begin",
        "                        cur_byte <= ADDR_BYTE;",
        "                        byte_idx <= 0;",
        "                        state    <= START;",
        "                        half_cnt <= 0;",
        "                    end",
        "                end",
        "",
        "                // SCL high while SDA falls -> START condition",
        "                START: begin",
        "                    scl_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        sda_drive_low <= 1;",
        "                        state    <= START_HOLD;",
        "                        half_cnt <= 0;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                // Hold the START window for a full period.",
        "                START_HOLD: begin",
        "                    scl_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 1;",
        "                        bit_idx  <= 3'd7;",
        "                        state    <= BIT_LOW;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                // SCL low: change SDA safely.",
        "                BIT_LOW: begin",
        "                    scl_drive_low <= 1;",
        "                    sda_drive_low <= cur_byte[bit_idx] ? 1'b0 : 1'b1;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 0;",
        "                        state    <= BIT_HIGH;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                // SCL high: hold SDA, then advance or start the ACK cell.",
        "                BIT_HIGH: begin",
        "                    scl_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 1;",
        "                        if (bit_idx == 0) begin",
        "                            sda_drive_low <= 0;",
        "                            state <= ACK_LOW;",
        "                        end else begin",
        "                            bit_idx <= bit_idx - 1;",
        "                            state   <= BIT_LOW;",
        "                        end",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                ACK_LOW: begin",
        "                    scl_drive_low <= 1;",
        "                    sda_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 0;",
        "                        state    <= ACK_HIGH;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                ACK_HIGH: begin",
        "                    scl_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 1;",
        "                        state    <= NEXT_BYTE;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                NEXT_BYTE: begin",
        *_next_byte_branches(data_bytes),
        "                end",
        "",
        "                STOP_LOW: begin",
        "                    scl_drive_low <= 1;",
        "                    sda_drive_low <= 1;",
        "                    if (half_done) begin",
        "                        half_cnt <= 0;",
        "                        scl_drive_low <= 0;",
        "                        state    <= STOP_HIGH;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                STOP_HIGH: begin",
        "                    scl_drive_low <= 0;",
        "                    if (half_done) begin",
        "                        sda_drive_low <= 0;",
        "                        state <= DONE;",
        "                    end else begin",
        "                        advance_half;",
        "                    end",
        "                end",
        "",
        "                DONE: begin",
        "                    tb_done <= 1;",
        "                end",
        "",
        "                default: state <= IDLE;",
        "            endcase",
        "        end",
        "    end",
    ])
    return "\n".join(lines) + "\n"


def generate_template_rtl(
    device_paths: Sequence[str] | None = None,
    address: int = DEFAULT_ADDRESS,
    data_bytes: Sequence[int] | None = None,
    spec_path: str | None = None,
    half: int = DEFAULT_HALF,
) -> str:
    """Emit a complete ``top_glue`` for the given transaction and devices.

    ``spec_path`` (when supplied) overrides ``address``/``data_bytes`` with the
    values parsed from the protocol spec, so the same emitter serves both the
    one-byte and two-byte tasks.
    """
    if spec_path is not None:
        spec = parse_i2c_spec(spec_path)
        address = spec.slave_address
        data_bytes = spec.data_bytes
    if data_bytes is None:
        data_bytes = DEFAULT_DATA_BYTES
    data_bytes = tuple(int(byte) for byte in data_bytes)
    if not 0 <= int(address) <= 0x7F:
        raise ValueError("address must be a 7-bit I2C slave address")
    if any(not 0 <= byte <= 0xFF for byte in data_bytes):
        raise ValueError("data bytes must fit in 8 bits")

    paths = list(device_paths or DEFAULT_DEVICE_PATHS)
    blocks = build_device_instance_blocks(paths)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    fragment = _fsm_fragment((int(address) << 1) | 0, data_bytes, blocks, half)
    return _assemble_multi_fragment(instance_code, fragment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=None, help="protocol spec path")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=DEFAULT_ADDRESS)
    parser.add_argument("--data", action="append", type=lambda value: int(value, 0),
                        help="data byte (repeatable)")
    parser.add_argument("--device", action="append", help="device Verilog path (repeatable)")
    parser.add_argument("--half", type=int, default=DEFAULT_HALF)
    parser.add_argument("--out", default=None, help="write RTL here instead of stdout")
    args = parser.parse_args(argv)

    rtl = generate_template_rtl(
        device_paths=args.device,
        address=args.address,
        data_bytes=args.data,
        spec_path=args.spec,
        half=args.half,
    )
    if args.out:
        Path(args.out).write_text(rtl, encoding="utf-8")
    else:
        print(rtl, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
