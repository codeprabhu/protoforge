"""Fast (no simulator needed) tests for the extended RESULT fields and open-drain gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.judge import judge, explain_failures
from tools.simulation_tool import parse_result_line
from tools.structural_checks import _open_drain

BASE = "RESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c"


def test_legacy_result_line_still_parses_and_passes():
    parsed = parse_result_line(BASE)
    assert parsed["parsed"] and "tb_done" not in parsed
    assert judge(parsed)["pass"]


def test_extended_result_line_passes_when_clean():
    parsed = parse_result_line(BASE + " tb_done=1 starts=1 stops=1")
    assert (parsed["tb_done"], parsed["starts"], parsed["stops"]) == (1, 1, 1)
    assert judge(parsed)["pass"]


def test_missing_tb_done_fails():
    v = judge(parse_result_line(BASE + " tb_done=0 starts=1 stops=1"))
    assert not v["pass"] and v["failed_checks"] == ["tb_done"]
    assert "tb_done" in explain_failures(v["failed_checks"])


def test_repeated_start_and_extra_stop_fail():
    v = judge(parse_result_line(BASE + " tb_done=1 starts=2 stops=2"))
    assert set(v["failed_checks"]) == {"single_start", "single_stop"}


def test_open_drain_accepts_both_polarities_and_expressions():
    assert _open_drain("assign sda = oe ? 1'b0 : 1'bz;", "sda")
    assert _open_drain("assign sda = ~oe ? 1'bz : 1'b0;", "sda")
    assert _open_drain("assign scl = (a && !b) ? 1'b0 : 1'bz;", "scl")


def test_open_drain_rejects_active_high_and_wrong_net():
    assert not _open_drain("assign sda = oe ? 1'b1 : 1'bz;", "sda")
    assert not _open_drain("assign sda = oe;", "sda")
    assert not _open_drain("assign scl = oe ? 1'b0 : 1'bz;", "sda")
