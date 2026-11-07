"""Phase 7: the FSM JSON IR must reject bad tables and compile good ones.

The point of the IR is that a small model can only produce a wrong
*protocol sequence*, never a module that fails to parse. These tests pin
both halves of that claim: the validator catches the state-table mistakes
that would otherwise hang in simulation, and the compiler's output for the
hand-written golden designs passes every gate the model output must pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ir import FsmDesign, FsmError, parse_design, validate_design
from ir.compiler import compile_design, compile_fragment
from ir.schema import Cond
from ir.validator import RESERVED_STATE_NAMES
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
SPEC_1BYTE = ROOT / "protocols/i2c/spec.md"
SPEC_2BYTE = ROOT / "protocols/i2c/spec_2byte.md"
GOLDEN_1BYTE = ROOT / "ir/designs/one_byte.json"
GOLDEN_2BYTE = ROOT / "ir/designs/two_byte.json"

pytestmark = pytest.mark.skipif(
    shutil.which("iverilog") is None
    or shutil.which("vvp") is None
    or shutil.which("verilator") is None
    or shutil.which("yosys") is None,
    reason="Icarus Verilog, Verilator and Yosys are required",
)


def _design(**overrides) -> FsmDesign:
    """A minimal two-state design that satisfies every validator rule."""
    payload = {
        "version": 1,
        "name": "probe",
        "address_byte": "a0",
        "data_bytes": ["3c"],
        "reset_state": "IDLE",
        "states": [
            {
                "name": "IDLE",
                "wait": "started",
                "otherwise": "hold",
                "transitions": [
                    {"when": ["always"], "next": "RUN", "actions": ["load_address"]}
                ],
            },
            {"name": "RUN", "transitions": []},
        ],
    }
    payload.update(overrides)
    design, problems = parse_design(json.dumps(payload))
    assert not problems, problems
    return design


def _payload(design: FsmDesign) -> dict:
    return json.loads(design.model_dump_json())


# --------------------------------------------------------------------------
# Schema / parsing
# --------------------------------------------------------------------------


def test_parse_design_accepts_json_text_and_dict():
    design, problems = parse_design(GOLDEN_1BYTE.read_text(encoding="utf-8"))
    assert problems == []
    assert design is not None and design.name == "one_byte_write"
    again, problems = parse_design(json.loads(GOLDEN_1BYTE.read_text(encoding="utf-8")))
    assert problems == [] and again is not None


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[1, 2, 3]",
        '"just a string"',
        '{"address_byte": "zz"}',
        '{"address_byte": "a0", "data_bytes": []}',
        '{"address_byte": "a0", "data_bytes": ["3c"], "states": []}',
        '{"address_byte": "a0", "data_bytes": ["3c"], "states": [{"name": "A B"}]}',
        '{"address_byte": "a0", "data_bytes": ["3c"], "version": 99, "states": []}',
    ],
)
def test_parse_design_returns_schema_problems(payload):
    design, problems = parse_design(payload)
    assert design is None
    assert problems, "schema failures must be reported to the repair loop"


def test_bare_string_condition_is_accepted():
    design = _design()
    assert design.states[0].transitions[0].when == [Cond.ALWAYS]


def test_empty_condition_list_is_rejected():
    payload = _payload(_design())
    payload["states"][0]["transitions"][0]["when"] = []
    design, problems = parse_design(json.dumps(payload))
    assert design is None and problems


def test_hex_byte_is_normalised():
    design, problems = parse_design(
        '{"address_byte": "0xA0", "data_bytes": ["0X3C"], '
        '"states": [{"name": "A", "transitions": []}]}'
    )
    assert problems == []
    assert design.address_byte == "a0" and design.data_bytes == ["3c"]


# --------------------------------------------------------------------------
# Validator: the mistakes that compile but hang in simulation
# --------------------------------------------------------------------------


def test_golden_designs_are_valid():
    for path in (GOLDEN_1BYTE, GOLDEN_2BYTE):
        design, problems = parse_design(path.read_text(encoding="utf-8"))
        assert problems == [], (path, problems)
        assert validate_design(design) == [], (path, validate_design(design))


def test_valid_minimal_design_has_no_problems():
    assert validate_design(_design()) == []


def test_duplicate_state_name_is_rejected():
    payload = _payload(_design())
    payload["states"].append({"name": "IDLE", "transitions": []})
    design, problems = parse_design(json.dumps(payload))
    assert problems == []  # shape is fine; the semantics are not
    assert any("duplicate state name 'IDLE'" in p for p in validate_design(design))


def test_undefined_transition_target_is_rejected():
    payload = _payload(_design())
    payload["states"][0]["transitions"][0]["next"] = "MISSING"
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("undefined state 'MISSING'" in p for p in validate_design(design))


def test_undefined_reset_state_is_rejected():
    payload = _payload(_design())
    payload["reset_state"] = "NOWHERE"
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("reset_state" in p for p in validate_design(design))


def test_unreachable_state_is_rejected():
    payload = _payload(_design())
    payload["states"].append({"name": "ORPHAN", "transitions": []})
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("unreachable state 'ORPHAN'" in p for p in validate_design(design))


def test_missing_default_is_rejected():
    payload = _payload(_design())
    payload["states"][0]["otherwise"] = None
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("missing default in state 'IDLE'" in p for p in validate_design(design))


def test_dead_transition_after_always_is_rejected():
    payload = _payload(_design())
    payload["states"][0]["transitions"].append(
        {"when": ["started"], "next": "RUN"}
    )
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("dead transition after 'always'" in p for p in validate_design(design))


def test_wait_without_transitions_is_rejected():
    payload = _payload(_design())
    payload["states"][1] = {"name": "RUN", "wait": "half_done", "transitions": []}
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("can never leave" in p for p in validate_design(design))


def test_half_done_with_hold_deadlocks_and_is_rejected():
    payload = _payload(_design())
    payload["states"][1] = {
        "name": "RUN",
        "wait": "half_done",
        "otherwise": "hold",
        "transitions": [{"when": ["always"], "next": "IDLE"}],
    }
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("never advances" in p for p in validate_design(design))


def test_reserved_state_names_are_rejected():
    for name in sorted(RESERVED_STATE_NAMES):
        payload = _payload(_design())
        payload["reset_state"] = name
        payload["states"][0]["name"] = name
        payload["states"][0]["transitions"][0]["next"] = "RUN"
        design, problems = parse_design(json.dumps(payload))
        assert problems == [], (name, problems)
        assert any("reserved" in p for p in validate_design(design)), name


def test_bit_guard_without_reload_is_rejected():
    payload = _payload(_design())
    payload["states"][1] = {
        "name": "RUN",
        "wait": "half_done",
        "otherwise": "tick",
        "transitions": [
            {"when": ["bit_last"], "next": "IDLE"},
            {"when": ["bit_not_last"], "next": "RUN"},
        ],
    }
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("restart_bits" in p for p in validate_design(design))


def test_byte_guard_without_reload_is_rejected():
    payload = _payload(_design())
    # Drop the fixture's load_address so nothing in the design ever reloads
    # byte_idx; the guard would then test a counter that never moves.
    payload["states"][0]["transitions"][0]["actions"] = []
    payload["states"][1] = {
        "name": "RUN",
        "otherwise": "hold",
        "transitions": [
            {"when": ["byte_not_last"], "next": "IDLE"},
            {"when": ["byte_done"], "next": "IDLE"},
        ],
    }
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    assert any("byte_idx" in p for p in validate_design(design))


def _golden_payload() -> dict:
    return json.loads(GOLDEN_1BYTE.read_text(encoding="utf-8"))


def _validate_mutated(mutate) -> list[str]:
    payload = _golden_payload()
    mutate(payload)
    design, problems = parse_design(json.dumps(payload))
    assert problems == [], problems
    return validate_design(design)


def test_reload_outside_the_reader_path_is_rejected():
    """``restart_bits`` still exists in the design, but not before the read.

    The design-wide check passed this table and simulation then clocked a
    single bit instead of eight: START_HOLD is the only entry into the bit
    loop for the address byte, so dropping its reload breaks every path
    that reaches BIT_LOW without going through NEXT_BYTE first.
    """
    def mutate(payload):
        for state in payload["states"]:
            if state["name"] == "START_HOLD":
                state["transitions"][0]["actions"] = []

    problems = _validate_mutated(mutate)
    assert any("BIT_LOW" in p and "restart_bits" in p for p in problems), problems


def test_advance_that_cannot_return_to_the_reader_is_rejected():
    """``advance_byte`` is present but stranded on the way to STOP.

    A state that re-tests ``byte_done`` needs the counter to change and
    control to come back. Relocating the reload off that cycle leaves the
    guard evaluating the same value forever, which is the hang the
    design-wide check failed to see.
    """
    def mutate(payload):
        for state in payload["states"]:
            if state["name"] == "NEXT_BYTE":
                state["transitions"][0]["actions"] = []
            elif state["name"] == "STOP_LOW":
                state["transitions"][0]["actions"] = ["advance_byte"]

    problems = _validate_mutated(mutate)
    assert any("NEXT_BYTE" in p and "advance_byte" in p for p in problems), problems


def test_missing_byte_advance_is_rejected():
    def mutate(payload):
        for state in payload["states"]:
            if state["name"] == "NEXT_BYTE":
                state["transitions"][0]["actions"] = []

    problems = _validate_mutated(mutate)
    assert any("NEXT_BYTE" in p and "advance_byte" in p for p in problems), problems


# --------------------------------------------------------------------------
# Compiler: structure, then the real gates
# --------------------------------------------------------------------------


def test_compile_design_wraps_in_fixed_module():
    design, _ = parse_design(GOLDEN_1BYTE.read_text(encoding="utf-8"))
    rtl = compile_design(design)
    assert rtl.startswith("module top_glue (")
    assert rtl.rstrip().endswith("endmodule")
    assert "device_sensor u_device_a" in rtl
    assert "device_controller u_device_b" in rtl
    assert "assign sda = sda_drive_low ? 1'b0 : 1'bz;" in rtl
    # The compiler owns the ports and the drivers; the fragment must not
    # re-declare what the wrapper already declares.
    assert rtl.count("reg sda_drive_low;") == 1
    assert rtl.count("module top_glue") == 1


def test_compile_design_rejects_invalid_table():
    payload = _payload(_design())
    payload["states"].append({"name": "ORPHAN", "transitions": []})
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    with pytest.raises(FsmError) as excinfo:
        compile_design(design)
    assert "ORPHAN" in str(excinfo.value)


def test_compile_fragment_uses_state_width_for_many_states():
    # A 17-state chain needs a 5-bit state register; 16 states would fit
    # in 4, so this fails if the width formula drops a state.
    payload = _payload(_design())
    states = [{"name": "IDLE", "wait": "started", "otherwise": "hold",
               "transitions": [{"when": ["always"], "next": "S1"}]}]
    for index in range(1, 17):
        states.append({
            "name": f"S{index}",
            "transitions": [{"when": ["always"], "next": f"S{index + 1}"}],
        })
    states.append({"name": "S17", "transitions": []})
    payload["states"] = states
    design, problems = parse_design(json.dumps(payload))
    assert problems == [], problems
    assert validate_design(design) == [], validate_design(design)
    fragment = compile_fragment(design, [])
    assert "reg [4:0] state;" in fragment  # 18 states need 5 bits


def test_compile_fragment_declares_every_payload_byte():
    payload = _payload(_design())
    payload["data_bytes"] = ["11", "22", "33"]
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    fragment = compile_fragment(design, [])
    assert "localparam DATA_BYTE_0 = 8'h11;" in fragment
    assert "localparam DATA_BYTE_1 = 8'h22;" in fragment
    assert "localparam DATA_BYTE_2 = 8'h33;" in fragment


def test_terminal_tick_state_still_advances_the_counter():
    """A design that declares ``tick`` must get ``advance_half`` even when
    the state has no way out, or the counter silently freezes."""
    payload = _payload(_design())
    payload["states"][1] = {
        "name": "RUN",
        "wait": "half_done",
        "otherwise": "tick",
        "transitions": [{"when": ["always"], "next": "IDLE"}],
    }
    design, problems = parse_design(json.dumps(payload))
    assert problems == []
    fragment = compile_fragment(design, [])
    assert "advance_half;" in fragment


def _run_gates(rtl: str, tmp_path: Path, spec_path: Path) -> tuple[dict, dict]:
    """Run structural, compile, hardware, simulation and judge gates."""
    struct = structural_checks(rtl, ["device_sensor", "device_controller"])
    assert struct["pass"], struct["failed_checks"]

    tmp_path.mkdir(parents=True, exist_ok=True)
    top = tmp_path / "top_glue.v"
    top.write_text(rtl, encoding="utf-8")
    vvp = tmp_path / "sim.vvp"
    compiled = compile_verilog([*map(str, DEVICES), str(top), str(TB)], out=str(vvp))
    assert compiled["success"], compiled["stderr"]

    hardware = run_hardware_checks([*map(str, DEVICES), str(top)])
    assert hardware["success"], hardware

    spec = parse_i2c_spec(str(spec_path))
    plusargs = [
        f"+I2C_ADDR={spec.address_hex}",
        f"+I2C_DATA={spec.data_hex}",
        f"+I2C_NBYTES={spec.nbytes}",
    ]
    simulated = run_simulation(str(vvp), plusargs=plusargs)
    assert simulated["success"], simulated["stderr"]
    parsed = parse_result_line(simulated["stdout"])
    verdict = judge(
        parsed,
        expected_addr=spec.address_hex,
        expected_data=spec.data_hex,
        expected_data_bytes=spec.nbytes,
        expected_data_list=spec.data_hexes,
    )
    return verdict, parsed


@pytest.mark.parametrize(
    "design_path,spec_path",
    [(GOLDEN_1BYTE, SPEC_1BYTE), (GOLDEN_2BYTE, SPEC_2BYTE)],
    ids=["one_byte", "two_byte"],
)
def test_golden_design_passes_all_five_gates(design_path, spec_path, tmp_path):
    design, problems = parse_design(design_path.read_text(encoding="utf-8"))
    assert problems == []
    assert validate_design(design) == []
    rtl = compile_design(design)
    verdict, parsed = _run_gates(rtl, tmp_path, spec_path)
    assert verdict["pass"], verdict
    assert parsed["sda_glitches"] == 0
    spec = parse_i2c_spec(str(spec_path))
    assert parsed["addr"] == spec.address_hex
    assert parsed["data_bytes"] == spec.data_hexes
    assert parsed["bytes"] == 1 + spec.nbytes  # address byte plus payload
    assert parsed["tb_done"] == 1
