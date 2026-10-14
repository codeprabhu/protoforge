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


def _simulate(src: str, tmp_path: Path) -> dict:
    top = tmp_path / "top_glue.v"
    top.write_text(src)
    out = tmp_path / "sim.out"
    comp = subprocess.run(
        ["iverilog", "-g2001", "-o", str(out), *map(str, DEVICES), str(top), str(TB)],
        capture_output=True, text=True, timeout=30,
    )
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run(["vvp", str(out)], capture_output=True, text=True, timeout=30, cwd=tmp_path)
    return judge(parse_result_line(run.stdout)), run.stdout


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
    "no_start": (("sda_oe   <= 1; // drive SDA low: START condition",
                  "sda_oe   <= 0; // MUTANT: no START"), "start"),
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


def test_repeated_start_is_rejected(tmp_path):
    # After the address byte, go back to START instead of sending data directly.
    src = _mutate(GOLD, "cur_byte <= DATA_BYTE;\n                        byte_idx <= 1;\n                        bit_idx  <= 3'd7;\n                        state    <= BIT_LOW;",
                  "cur_byte <= DATA_BYTE;\n                        byte_idx <= 1;\n                        bit_idx  <= 3'd7;\n                        half_cnt <= 0;\n                        state    <= START;")
    verdict, _ = _simulate(src, tmp_path)
    assert not verdict["pass"]
    assert "single_start" in verdict["failed_checks"], verdict["failed_checks"]
