"""Mutation tests for the simulation oracle (testbench + judge).

These need a real Icarus Verilog install and are skipped without one. The
point: prove the oracle *rejects* designs that are almost right. A golden
reference must pass, and each single-fault mutant must fail on the check that
describes its fault. Without this, a passing run only shows the oracle is
lenient, not that the design is correct.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.judge import judge  # noqa: E402
from tools.simulation_tool import parse_result_line  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="Icarus Verilog not installed",
)

GOLDEN = ROOT / "protocols/i2c/reference/good_master_reset_safe.v"
DEVICES = [ROOT / "devices/device_sensor.v", ROOT / "devices/device_controller.v"]
TB = ROOT / "protocols/i2c/testbench_i2c.v"


def _simulate(
    src: str,
    tmp_path: Path,
    plusargs: list[str] | None = None,
    expected_addr: str = "a0",
    expected_data: str = "3c",
) -> dict:
    top = tmp_path / "top_glue.v"
    top.write_text(src)
    out = tmp_path / "sim.out"
    comp = subprocess.run(
        ["iverilog", "-g2001", "-o", str(out), *map(str, DEVICES), str(top), str(TB)],
        capture_output=True, text=True, timeout=30,
    )
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run(
        ["vvp", str(out), *(plusargs or [])],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
    )
    return judge(
        parse_result_line(run.stdout),
        expected_addr=expected_addr,
        expected_data=expected_data,
    ), run.stdout


def _mutate(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"mutation anchor must be unique: {old!r} x{src.count(old)}"
    return src.replace(old, new)


GOLD = GOLDEN.read_text()

MUTANTS = {
    # name: (source edit, check that must fail)
    "no_tb_done": (("DONE: begin\n                    tb_done <= 1;", "DONE: begin\n                    tb_done <= 0;"), "tb_done"),
    "no_stop": (("sda_oe <= 0; // release SDA while SCL high -> STOP condition",
                 "sda_oe <= 1; // MUTANT: keep SDA low, never STOP"), "stop"),
    "wrong_address": (("localparam ADDR_BYTE = 8'hA0;", "localparam ADDR_BYTE = 8'hA2;"), "address_ack"),
    "wrong_data": (("localparam DATA_BYTE = 8'h3C;", "localparam DATA_BYTE = 8'h00;"), "correct_data"),
    "no_start": (("sda_oe   <= 1; // drive SDA low: START condition",
                  "sda_oe   <= 0; // MUTANT: no START"), "start"),
    "lsb_first": (("sda_oe <= cur_byte[bit_idx] ? 1'b0 : 1'b1; // 1=release(high),0=drive low -> invert",
                   "sda_oe <= cur_byte[7 - bit_idx] ? 1'b0 : 1'b1; // MUTANT: LSB first"), "address_ack"),
    "third_byte": (("if (byte_idx == 0) begin\n                        cur_byte <= DATA_BYTE;",
                    "if (byte_idx < 2) begin\n                        cur_byte <= DATA_BYTE;"), "byte_count"),
    # Dropping the address ACK clock does not clear the testbench's
    # ``saw_ack_addr`` arm (it is set at the byte boundary), but the missing
    # ninth clock shifts every later bit cell, so the data ACK and byte count
    # fail. The oracle still rejects the design; it is diagnosed downstream.
    "skip_ack": (("if (bit_idx == 0) begin\n                            sda_oe <= 0; // release SDA for slave to drive ACK\n                            state  <= ACK_LOW;",
                  "if (bit_idx == 0) begin\n                            sda_oe <= 0; // MUTANT: skip the ACK clock entirely\n                            state  <= NEXT_BYTE;"), "data_ack"),
    "sda_glitch": (("BIT_HIGH: begin\n                    scl_oe <= 0;",
                    "BIT_HIGH: begin\n                    scl_oe <= 0;\n                    if (half_cnt == 1) sda_oe <= ~sda_oe; // MUTANT: change SDA while SCL high"), "sda_stable"),
}


def test_golden_reference_passes(tmp_path):
    verdict, stdout = _simulate(GOLD, tmp_path)
    assert verdict["pass"], verdict
    assert "EVENT:TIMEOUT" not in stdout, "a passing design must not print a timeout"


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_rejected(name, tmp_path):
    (old, new), expected = MUTANTS[name]
    verdict, _ = _simulate(_mutate(GOLD, old, new), tmp_path)
    assert not verdict["pass"], f"oracle accepted mutant {name}"
    assert expected in verdict["failed_checks"], verdict["failed_checks"]


@pytest.mark.parametrize("addr,data", [("44", "a5"), ("12", "7e")])
def test_plusargs_select_expected_transaction(addr, data, tmp_path):
    # The testbench must honour +I2C_ADDR/+I2C_DATA rather than only its
    # hard-coded defaults. A parameterised copy of the golden master sends a
    # different address/data pair; the oracle must accept it.
    src = _mutate(GOLD, "localparam ADDR_BYTE = 8'hA0;",
                  f"localparam ADDR_BYTE = 8'h{addr.upper()};")
    src = _mutate(src, "localparam DATA_BYTE = 8'h3C;",
                  f"localparam DATA_BYTE = 8'h{data.upper()};")
    verdict, stdout = _simulate(
        src,
        tmp_path,
        plusargs=[f"+I2C_ADDR={addr}", f"+I2C_DATA={data}"],
        expected_addr=addr,
        expected_data=data,
    )
    assert verdict["pass"], (verdict, stdout[-1500:])


def test_plusarg_mismatch_is_rejected(tmp_path):
    # The golden master still sends A0/3C, but the oracle is told to expect a
    # different address. The BFM will not ACK 0xA0, so the judge must fail it.
    verdict, _ = _simulate(
        GOLD,
        tmp_path,
        plusargs=["+I2C_ADDR=44", "+I2C_DATA=3c"],
        expected_addr="44",
        expected_data="3c",
    )
    assert not verdict["pass"]
    assert "address_ack" in verdict["failed_checks"], verdict["failed_checks"]


def test_repeated_start_is_rejected(tmp_path):
    # After the address byte, go back to START instead of sending data directly.
    src = _mutate(GOLD, "cur_byte <= DATA_BYTE;\n                        byte_idx <= 1;\n                        bit_idx  <= 3'd7;\n                        state    <= BIT_LOW;",
                  "cur_byte <= DATA_BYTE;\n                        byte_idx <= 1;\n                        bit_idx  <= 3'd7;\n                        half_cnt <= 0;\n                        state    <= START;")
    verdict, _ = _simulate(src, tmp_path)
    assert not verdict["pass"]
    assert "single_start" in verdict["failed_checks"], verdict["failed_checks"]
