"""Phase 7 steps 86-89: JSON-only generation, constrained output, repair.

The model never writes Verilog on this path. It writes a table that is
validated against the pydantic schema, the path-sensitive validator and the
spec values, then compiled by the deterministic compiler. These tests pin the
feedback loop that makes that viable for a small model:

* the hand-written Ollama schema agrees with the pydantic model;
* the schema is actually passed through ``format=``;
* a rejected answer gets exactly one retry carrying the validator's words;
* a second bad answer raises instead of compiling;
* repair edits the embedded table using the judge's sentences and refuses to
  change the transaction values; and
* a crashing repair model is a recorded outcome, not a runner crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import ir_generator, ollama_client
from core import outcomes
from core.orchestrator import Orchestrator
from core.schemas import Task
from domains.registry import get_domain
from ir import FsmError, parse_design
from ir.schema import (
    Action,
    Cond,
    Done,
    Drive,
    FsmDesign,
    Otherwise,
    Scl,
    Sda,
    State,
    Transition,
)
from tools.structural_checks import structural_checks
from tools.i2c_spec import parse_i2c_spec
from tools.compile_tool import compile_verilog
from tools.hardware_checks import run_hardware_checks
from tools.judge import judge
from tools.simulation_tool import parse_result_line, run_simulation

ROOT = Path(__file__).resolve().parent.parent
SPEC_1BYTE = ROOT / "protocols" / "i2c" / "spec.md"
GOLDEN_1BYTE = ROOT / "ir" / "designs" / "one_byte.json"
DEVICES = [
    str(ROOT / "devices" / "device_sensor.v"),
    str(ROOT / "devices" / "device_controller.v"),
]
GOLDEN_TEXT = GOLDEN_1BYTE.read_text(encoding="utf-8")


def _golden() -> dict:
    return json.loads(GOLDEN_TEXT)


def _fake_model(responses):
    """A patchable stand-in for ``ir_generator._model_call``."""
    calls = []

    def fake(prompt, *, model, temperature, seed):
        calls.append(
            {
                "prompt": prompt,
                "model": model,
                "temperature": temperature,
                "seed": seed,
            }
        )
        content = responses[min(len(calls) - 1, len(responses) - 1)]
        return content, {
            "content": content,
            "prompt_eval_count": 10 * len(calls),
            "eval_count": 5 * len(calls),
            "model": model,
        }

    return fake, calls


def _mutate(**changes) -> str:
    payload = _golden()
    payload.update(changes)
    return json.dumps(payload)


def _design_with(mutator) -> str:
    """Apply a structural edit to the golden table and return its JSON."""
    payload = _golden()
    mutator(payload)
    return json.dumps(payload)


def _drop_load_address(payload) -> None:
    idle = next(state for state in payload["states"] if state["name"] == "IDLE")
    idle["transitions"][0]["actions"] = []


def _drop_started_wait(payload) -> None:
    idle = next(state for state in payload["states"] if state["name"] == "IDLE")
    idle["wait"] = None
    idle["otherwise"] = None


def _drop_bit_drive(payload) -> None:
    for state in payload["states"]:
        for drive in [state.get("drive", {})] + [
            transition.get("drive", {}) for transition in state["transitions"]
        ]:
            if drive.get("sda") == "bit":
                drive["sda"] = "high"


def _drop_done(payload) -> None:
    for state in payload["states"]:
        for transition in state["transitions"]:
            transition["actions"] = [
                action for action in transition.get("actions", []) if action != "finish"
            ]
    done = next(state for state in payload["states"] if state["name"] == "DONE")
    done["drive"]["done"] = "hold"


# ---------------------------------------------------------------------------
# Step 86/87: prompt and schema


def test_hand_written_schema_matches_pydantic_and_stays_flat():
    text = json.dumps(ir_generator.FSM_JSON_SCHEMA)
    # Ollama turns this into a grammar; refs and unions break that path.
    assert "$ref" not in text and "$defs" not in text and "anyOf" not in text

    top = ir_generator.FSM_JSON_SCHEMA
    assert set(top["properties"]) == set(FsmDesign.model_fields)
    assert set(top["required"]) == set(FsmDesign.model_fields)

    state = top["properties"]["states"]["items"]
    assert set(state["properties"]) == set(State.model_fields)
    assert set(state["required"]) == {"name", "transitions"}

    drive = state["properties"]["drive"]
    assert set(drive["properties"]) == set(Drive.model_fields)
    assert set(drive["properties"]["sda"]["enum"]) == {item.value for item in Sda}
    assert set(drive["properties"]["scl"]["enum"]) == {item.value for item in Scl}
    assert set(drive["properties"]["done"]["enum"]) == {item.value for item in Done}

    transition = state["properties"]["transitions"]["items"]
    assert set(transition["properties"]) == set(Transition.model_fields)
    assert set(transition["required"]) == {"when", "next"}
    assert set(transition["properties"]["when"]["items"]["enum"]) == {
        item.value for item in Cond
    }
    assert set(transition["properties"]["actions"]["items"]["enum"]) == {
        item.value for item in Action
    }
    assert set(state["properties"]["otherwise"]["enum"]) == {
        item.value for item in Otherwise
    }


def test_json_client_sends_the_schema_as_ollama_format(monkeypatch):
    seen = {}

    def fake_chat(**kwargs):
        seen.update(kwargs)
        return {"message": {"content": '{"ok": true}'}}

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(chat=fake_chat))
    schema = {"type": "object", "properties": {}}

    ollama_client.generate_json_with_metadata("hello", json_schema=schema)

    assert seen["format"] == schema
    assert seen["messages"][0]["content"] == ollama_client.JSON_SYSTEM_PROMPT
    assert seen["options"] == {
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "num_predict": 4096,
    }

    ollama_client.generate_json("hello")
    assert seen["format"] == "json"


def test_generation_prompt_is_json_only_and_names_the_spec_values():
    spec = parse_i2c_spec(str(SPEC_1BYTE))
    prompt = ir_generator.build_generation_prompt(spec, retrieval=False)

    assert 'address_byte: "a0"' in prompt
    assert 'data_bytes: ["3c"]' in prompt
    assert ir_generator._SCHEMA_TEXT in prompt
    assert "Verilog" not in prompt  # the model is not asked for RTL
    assert "```" not in prompt


def test_retrieval_example_does_not_leak_the_answer():
    text = ir_generator._retrieval_text()
    assert '"address_byte":"00"' in text
    assert '"3c"' not in text
    assert "structure" in text.lower()


# ---------------------------------------------------------------------------
# Step 88: generate, validate, one retry, then fail


def test_generate_compiles_the_table_and_embeds_it(monkeypatch):
    fake, calls = _fake_model([GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    trace = {}
    rtl = ir_generator.generate_fsm_rtl(
        DEVICES, str(SPEC_1BYTE), model="qwen-test", trace=trace
    )

    assert len(calls) == 1
    assert "module top_glue" in rtl
    assert ir_generator.JSON_BEGIN in rtl and ir_generator.JSON_END in rtl
    embedded, problems = parse_design(ir_generator.extract_design_json(rtl))
    assert problems == []
    assert embedded.address_byte == "a0"
    assert embedded.data_bytes == ["3c"]

    # Comment markers must not confuse the structural gate.
    struct = structural_checks(rtl, ["device_sensor", "device_controller"])
    assert struct["pass"], struct["failed_checks"]

    assert trace["raw_output"] == GOLDEN_TEXT
    assert trace["model_metadata"]["eval_count"] == 5
    assert trace["fsm_json"] and trace["prompt"]
    assert 'address_byte: "a0"' in trace["prompt"]


def test_bad_json_is_retried_once_with_the_validator_feedback(monkeypatch):
    fake, calls = _fake_model(["this is not json", GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    trace = {}
    rtl = ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE), trace=trace)

    assert len(calls) == 2
    assert "invalid JSON" in calls[1]["prompt"]
    assert "Previous answer" in calls[1]["prompt"]
    assert ir_generator.extract_design_json(rtl) is not None
    # Token accounting must include both the rejected call and the retry.
    assert trace["model_metadata"]["eval_count"] == 15
    assert trace["model_calls"][0]["eval_count"] == 5
    assert trace["model_calls"][1]["eval_count"] == 10
    assert len(trace["model_calls"]) == 2


def test_two_bad_answers_raise_instead_of_compiling(monkeypatch):
    fake, calls = _fake_model(["nope", "still nope"])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    with pytest.raises(FsmError) as excinfo:
        ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))

    assert len(calls) == 2
    assert "invalid JSON" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_design,fragment",
    [
        (_mutate(address_byte="a1"), 'address_byte must be "a0"'),
        (_mutate(data_bytes=["ff"]), 'data_bytes must be ["3c"]'),
    ],
    ids=["address", "data"],
)
def test_spec_mismatch_is_retried_with_the_expected_values(
    monkeypatch, bad_design, fragment
):
    fake, calls = _fake_model([bad_design, GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))

    assert len(calls) == 2
    assert fragment in calls[1]["prompt"]


def test_generated_ir_output_passes_all_five_gates(monkeypatch, tmp_path):
    """The JSON path must produce RTL the real oracle accepts, not just text."""
    fake, _ = _fake_model([GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)
    rtl = ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))

    struct = structural_checks(rtl, ["device_sensor", "device_controller"])
    assert struct["pass"], struct["failed_checks"]

    top = tmp_path / "top_glue.v"
    top.write_text(rtl, encoding="utf-8")
    vvp = tmp_path / "sim.vvp"
    compiled = compile_verilog(
        [*DEVICES, str(top), str(ROOT / "protocols/i2c/testbench_i2c.v")],
        out=str(vvp),
    )
    assert compiled["success"], compiled["stderr"]

    hardware = run_hardware_checks([*DEVICES, str(top)])
    assert hardware["success"], hardware

    spec = parse_i2c_spec(str(SPEC_1BYTE))
    simulated = run_simulation(
        str(vvp),
        plusargs=[
            f"+I2C_ADDR={spec.address_hex}",
            f"+I2C_DATA={spec.data_hex}",
            f"+I2C_NBYTES={spec.nbytes}",
        ],
    )
    assert simulated["success"], simulated["stderr"]
    parsed = parse_result_line(simulated["stdout"])
    verdict = judge(
        parsed,
        expected_addr=spec.address_hex,
        expected_data=spec.data_hex,
        expected_data_bytes=spec.nbytes,
        expected_data_list=spec.data_hexes,
    )
    assert verdict["pass"], verdict
    assert parsed["sda_glitches"] == 0
    assert parsed["tb_done"] == 1


def test_spec_mismatch_twice_is_fatal(monkeypatch):
    fake, _ = _fake_model([_mutate(data_bytes=["ff"]), _mutate(data_bytes=["ee"])])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    with pytest.raises(FsmError, match='data_bytes must be'):
        ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))


# ---------------------------------------------------------------------------
# Generation-time protocol shape checks
#
# The first real model table compiled and simulated but had no address load,
# no tb_start wait, no sda="bit" and no rising tb_done. Rejecting those at
# generation time turns a ~3 minute simulate+repair failure into one retry.


@pytest.mark.parametrize(
    "mutator,fragment",
    [
        (_drop_load_address, "never loads the address byte"),
        (_drop_started_wait, "never waits for started"),
        (_drop_done, "tb_done never rises"),
    ],
    ids=["load_address", "started", "done"],
)
def test_protocol_guard_still_reports_what_enrichment_owns(mutator, fragment):
    """The guard is defensive: it names the omission if enrichment cannot run."""
    design, problems = parse_design(_design_with(mutator))
    assert problems == []

    assert any(fragment in problem for problem in ir_generator._protocol_problems(design))


def test_protocol_guard_rejects_a_table_that_never_drives_data_bits():
    design, problems, _ = ir_generator._evaluate_design(
        _design_with(_drop_bit_drive), expected_addr="a0", expected_data=["3c"]
    )

    assert design is None
    assert any("never drives SDA from the data bits" in problem for problem in problems)


def test_golden_table_has_no_protocol_shape_problems():
    design, problems, notes = ir_generator._evaluate_design(
        GOLDEN_TEXT, expected_addr="a0", expected_data=["3c"]
    )

    assert design is not None
    assert problems == []
    assert notes == []


def test_protocol_shape_problem_costs_one_retry_not_a_simulation(monkeypatch):
    bad = _design_with(_drop_bit_drive)
    fake, calls = _fake_model([bad, GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))

    assert len(calls) == 2
    assert "never drives SDA from the data bits" in calls[1]["prompt"]


# ---------------------------------------------------------------------------
# Generation-time enrichment: deterministic protocol boilerplate
#
# The real model tables omit otherwise/load_address/restart_bits/done while
# getting the state order and bit drive right. Those four are the same in
# every I2C write, so the generator fills them and records what it filled.


def _drop_boilerplate(payload) -> None:
    _drop_started_wait(payload)
    _drop_load_address(payload)
    _drop_done(payload)
    for state in payload["states"]:
        if state["name"] in {"BIT_HIGH", "NEXT_BYTE", "STOP_HIGH"}:
            state.pop("otherwise", None)
        for transition in state["transitions"]:
            transition["actions"] = [
                action
                for action in transition.get("actions", [])
                if action != "restart_bits"
            ]


def test_enrichment_fills_the_mechanical_protocol_defaults():
    design, problems, notes = ir_generator._evaluate_design(
        _design_with(_drop_boilerplate),
        expected_addr="a0",
        expected_data=["3c"],
    )

    assert design is not None
    assert problems == []

    idle = design.state_by_name["IDLE"]
    assert idle.wait is ir_generator.Cond.STARTED
    assert any(
        ir_generator.Action.LOAD_ADDRESS in transition.actions
        for transition in idle.transitions
    )

    assert design.state_by_name["BIT_HIGH"].otherwise is ir_generator.Otherwise.TICK
    assert design.state_by_name["NEXT_BYTE"].otherwise is ir_generator.Otherwise.HOLD
    assert any(
        ir_generator.Action.RESTART_BITS in transition.actions
        for state in design.states
        for transition in state.transitions
    )
    assert design.state_by_name["DONE"].drive.done is ir_generator.Done.HIGH
    assert notes


def test_enrichment_leaves_an_explicit_table_alone():
    design, problems = parse_design(GOLDEN_TEXT)
    assert problems == []

    enriched, notes = ir_generator._enrich_design(design)

    assert notes == []
    assert enriched.model_dump() == design.model_dump()


def test_enrichment_adds_the_started_wait_only_when_it_is_missing():
    design, problems = parse_design(_design_with(_drop_started_wait))
    assert problems == []

    enriched, notes = ir_generator._enrich_design(design)

    assert enriched.state_by_name["IDLE"].wait is ir_generator.Cond.STARTED
    assert any("wait=started" in note for note in notes)


def test_enrichment_notes_reach_the_attempt_trace(monkeypatch):
    fake, _ = _fake_model([_design_with(_drop_boilerplate)])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    trace = {}
    rtl = ir_generator.generate_fsm_rtl(
        DEVICES, str(SPEC_1BYTE), trace=trace
    )

    assert trace["enrichment"]
    embedded, problems = parse_design(ir_generator.extract_design_json(rtl))
    assert problems == []
    assert embedded.state_by_name["DONE"].drive.done is ir_generator.Done.HIGH


def test_ir_table_and_enrichment_are_persisted_in_the_attempt_record(monkeypatch):
    from core.attempt_record import AttemptRecord
    from core.schemas import CandidatePatch

    fake, _ = _fake_model([_design_with(_drop_boilerplate)])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    trace = {}
    rtl = ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE), trace=trace)
    record = AttemptRecord.from_candidate(
        CandidatePatch(target_file="top_glue.v", full_content=rtl, metadata=trace),
        run_id="r1",
        attempt=0,
        model="qwen-test",
    )

    assert record.fsm_json == trace["fsm_json"]
    assert record.enrichment == trace["enrichment"]
    payload = json.loads(record.model_dump_json())
    assert payload["fsm_json"]
    assert payload["enrichment"]


def test_repair_prompt_requires_a_default_for_every_wait():
    design, problems = parse_design(GOLDEN_TEXT)
    assert problems == []

    prompt = ir_generator.build_repair_prompt(
        design, "failed simulation checks", "SDA never fell"
    )

    assert "Every state that declares wait must also declare otherwise" in prompt


# ---------------------------------------------------------------------------
# Step 89: repair edits the JSON, not the wrapper


def _generated_code(monkeypatch) -> str:
    fake, _ = _fake_model([GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)
    return ir_generator.generate_fsm_rtl(DEVICES, str(SPEC_1BYTE))


def test_repair_prompt_uses_the_judge_sentences_and_keeps_values(monkeypatch):
    code = _generated_code(monkeypatch)
    fake, calls = _fake_model([GOLDEN_TEXT])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    detail = (
        "tb_done was never asserted -- the spec requires it to go high once "
        "the STOP condition has been issued."
    )
    trace = {}
    repaired = ir_generator.repair_fsm_rtl(
        code,
        "failed simulation checks",
        detail,
        model="qwen-test",
        device_paths=DEVICES,
        trace=trace,
    )

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    assert "failed simulation checks" in prompt
    assert "tb_done was never asserted" in prompt
    assert 'Keep address_byte "a0" and data_bytes ["3c"] unchanged' in prompt
    assert ir_generator.JSON_BEGIN in repaired
    embedded, problems = parse_design(ir_generator.extract_design_json(repaired))
    assert problems == []
    assert embedded.address_byte == "a0"
    assert embedded.data_bytes == ["3c"]
    assert trace["fsm_json"]


def test_repair_refuses_to_change_the_transaction_values(monkeypatch):
    code = _generated_code(monkeypatch)
    changed = _mutate(data_bytes=["99"])
    fake, calls = _fake_model([changed, changed])
    monkeypatch.setattr(ir_generator, "_model_call", fake)

    with pytest.raises(FsmError, match=r'data_bytes must be \["3c"\]'):
        ir_generator.repair_fsm_rtl(
            code,
            "failed simulation checks",
            "wrong data",
            device_paths=DEVICES,
        )

    assert len(calls) == 2


def test_repair_without_embedded_json_says_so():
    with pytest.raises(FsmError, match="markers"):
        ir_generator.repair_fsm_rtl(
            "module top_glue; endmodule",
            "failed structural checks",
            "missing instance",
        )


# ---------------------------------------------------------------------------
# A crashing repair model is an outcome, not a runner crash


def _structural_failure(*args, **kwargs) -> str:
    return "module top_glue; endmodule"


def test_orchestrator_records_repair_crash_at_the_triggering_stage(tmp_path):
    adapter = get_domain("i2c")
    task = Task(
        task_id="TASK-REPAIR-CRASH",
        domain="i2c",
        description="repair backend crash",
        metadata={
            "target_file": str(tmp_path / "top_glue.v"),
            "sim_out": str(tmp_path / "sim.out"),
        },
    )

    def boom(*args, **kwargs):
        raise RuntimeError("repair backend down")

    result = Orchestrator(adapter).execute_task(
        task,
        max_repairs=1,
        generate_fn=_structural_failure,
        repair_fn=boom,
    )

    assert result["final"] == outcomes.STRUCTURAL_FAILED
    assert result["final"] in outcomes.KNOWN_FINALS
    assert "repair backend down" in result["repair_error"]
    assert result["repair_error"] == "RuntimeError: repair backend down"


def test_repair_error_reaches_run_header_and_index(tmp_path, monkeypatch):
    from agent_core.agent import run_once
    from agent_core.config import Config

    cfg = Config(
        output_root=str(tmp_path / "runs"),
        results_log=str(tmp_path / "runs.jsonl"),
        trace_dir=str(tmp_path / "traces"),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("repair backend down")

    log = run_once(
        config=cfg,
        max_repairs=1,
        generate_fn=_structural_failure,
        repair_fn=boom,
    )

    assert log["final"] == outcomes.STRUCTURAL_FAILED
    header = json.loads(
        (Path(log["run_dir"]) / "run.json").read_text(encoding="utf-8")
    )
    assert header["repair_error"] == "RuntimeError: repair backend down"

    entry = json.loads(
        (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert entry["final"] == outcomes.STRUCTURAL_FAILED
    assert "repair backend down" in entry["error"]
