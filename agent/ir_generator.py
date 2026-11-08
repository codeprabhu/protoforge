"""Generate and repair the FSM JSON intermediate representation.

Workflow steps 86-89. Instead of asking a small model for Verilog, this asks
for a flat JSON state table, validates it (pydantic + path-sensitive
validator + spec consistency), then compiles it with the deterministic
compiler from :mod:`ir.compiler`.

The compiled source carries the original JSON inside comment markers. A
repair call extracts that table, shows the judge's sentences to the model,
and recompiles the corrected table -- the model never edits the fixed
wrapper or the open-drain drivers.

Generation is one model call plus at most one retry. The retry appends the
exact validator/spec errors, which is the one feedback channel a small model
uses reliably. A second bad answer raises :class:`ir.schema.FsmError` so the
orchestrator records a failed attempt instead of compiling garbage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent import ollama_client
from agent.rtl_generator import _assemble_multi_fragment, build_device_instance_blocks
from ir.compiler import DEFAULT_DEVICE_PATHS, compile_fragment
from ir.schema import (
    Action,
    Cond,
    Done,
    FsmDesign,
    FsmError,
    Otherwise,
    Scl,
    Sda,
    parse_design,
)
from ir.validator import validate_design
from tools.i2c_spec import parse_i2c_spec

JSON_BEGIN = "// fsm-json-begin"
JSON_END = "// fsm-json-end"

REPO_ROOT = Path(__file__).resolve().parent.parent
RETRIEVAL_DESIGN = REPO_ROOT / "ir" / "designs" / "one_byte.json"


def _enum_values(enum_type) -> List[str]:
    return [item.value for item in enum_type]


# Hand-written, flat JSON schema (no $ref/$defs) because Ollama turns it into
# a grammar. Building the value lists from the pydantic enums keeps the two
# definitions from drifting; ``tests/test_ir_generator.py`` pins the shape.
_DRIVE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sda": {"type": "string", "enum": _enum_values(Sda)},
        "scl": {"type": "string", "enum": _enum_values(Scl)},
        "done": {"type": "string", "enum": _enum_values(Done)},
    },
    "additionalProperties": False,
}

_TRANSITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["when", "next"],
    "properties": {
        "when": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": _enum_values(Cond)},
        },
        "next": {"type": "string"},
        "drive": _DRIVE_SCHEMA,
        "actions": {
            "type": "array",
            "items": {"type": "string", "enum": _enum_values(Action)},
        },
    },
    "additionalProperties": False,
}

_STATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["name", "transitions"],
    "properties": {
        "name": {"type": "string"},
        "drive": _DRIVE_SCHEMA,
        "wait": {"type": "string", "enum": _enum_values(Cond)},
        "otherwise": {"type": "string", "enum": _enum_values(Otherwise)},
        "transitions": {"type": "array", "items": _TRANSITION_SCHEMA},
    },
    "additionalProperties": False,
}

FSM_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "version",
        "name",
        "address_byte",
        "data_bytes",
        "half",
        "reset_state",
        "states",
    ],
    "properties": {
        "version": {"type": "integer", "enum": [1]},
        "name": {"type": "string"},
        "address_byte": {"type": "string"},
        "data_bytes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "half": {"type": "integer", "enum": [4]},
        "reset_state": {"type": "string"},
        "states": {"type": "array", "minItems": 1, "items": _STATE_SCHEMA},
    },
    "additionalProperties": False,
}

_SCHEMA_TEXT = json.dumps(FSM_JSON_SCHEMA, separators=(",", ":"))

_CONDITION_HELP = (
    "Condition meanings: started = tb_start is high; half_done = the "
    "half-period counter reached HALF; bit_last = bit_idx == 0; bit_not_last = "
    "bit_idx != 0; byte_done = byte_idx == the number of data bytes; "
    "byte_not_last = byte_idx < the number of data bytes; always = "
    "unconditional."
)

_SEQUENCE_HELP = (
    "Required sequence (one slave, no clock stretching):\n"
    "1. IDLE: release SDA/SCL, wait for started, then load_address.\n"
    "2. START: SDA falls while SCL is high, then SCL falls.\n"
    "3. For 8 bits, MSB first: drive sda=bit while SCL is low, raise SCL for "
    "half a period, lower SCL; use next_bit after each non-final bit.\n"
    "4. ACK: release SDA, raise SCL for half a period, lower SCL.\n"
    "5. If byte_not_last, advance_byte and send the next byte; if byte_done, "
    "go to STOP.\n"
    "6. STOP: drive SDA low, then release it high while SCL is high, then "
    "finish (tb_done rises and stays high).\n"
    "Time the clock phases with wait = half_done and otherwise = tick."
)


def _model_call(
    prompt: str,
    *,
    model: Optional[str],
    temperature: float,
    seed: Optional[int],
) -> Tuple[str, Dict[str, Any]]:
    """Indirection keeps model calls patchable in tests."""
    result = ollama_client.generate_json_with_metadata(
        prompt,
        model=model or ollama_client.DEFAULT_MODEL,
        temperature=temperature,
        seed=ollama_client.DEFAULT_SEED if seed is None else seed,
        json_schema=FSM_JSON_SCHEMA,
    )
    return result["content"], result


# ---------------------------------------------------------------------------
# Prompt construction


def _spec_facts(spec) -> List[str]:
    return [
        f'- address_byte: "{spec.address_hex}" '
        f"(7-bit slave address 0x{spec.slave_address:02x} shifted left, write bit 0)",
        f"- data_bytes: {json.dumps(spec.data_hexes)}",
    ]


def _retrieval_text() -> str:
    """A verified table with placeholder values, shown for its shape only."""
    try:
        payload = json.loads(RETRIEVAL_DESIGN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    payload["name"] = "example"
    # Never leak this task's answer: the model must take values from the spec.
    payload["address_byte"] = "00"
    payload["data_bytes"] = ["00"]
    return (
        "A verified state table for a *different* transaction. Copy only its "
        "structure; the address and data values above must come from the spec.\n"
        + json.dumps(payload, separators=(",", ":"))
    )


def build_generation_prompt(spec, *, retrieval: bool = True) -> str:
    """Short JSON-only prompt that names the schema and the protocol rules."""
    sections = [
        "Write ONE JSON object describing the finite-state machine for this "
        "I2C write transaction.",
        "",
        "Transaction facts (values MUST match exactly):",
        *_spec_facts(spec),
        "",
        "Protocol rules the testbench checks:",
        "- START: SDA falls while SCL is high.",
        "- Send each byte MSB first; SDA may only change while SCL is low.",
        "- After 8 bits, release SDA for the ACK bit and clock SCL high.",
        "- STOP: SDA rises while SCL is high, then tb_done rises and stays high.",
        "",
        _SEQUENCE_HELP,
        "",
        "JSON semantics:",
        '- state.drive is applied on every clock: sda "low"/"high"/"hold"/"bit" '
        '("bit" drives cur_byte[bit_idx]), scl "low"/"high"/"hold", '
        'done "low"/"high"/"hold".',
        "- state.wait gates the transitions. While it is false the state follows "
        'otherwise: "tick" advances the half-period counter, "hold" does nothing.',
        "- A state that declares wait MUST also declare otherwise.",
        "- actions: load_address (cur_byte<=ADDR_BYTE), advance_byte "
        "(cur_byte<=next data byte), restart_bits (bit_idx<=7), next_bit "
        "(bit_idx--), finish (tb_done<=1), clear_done.",
        _CONDITION_HELP,
        "",
        "Return exactly one JSON object matching this schema (no prose):",
        _SCHEMA_TEXT,
    ]
    if retrieval:
        example = _retrieval_text()
        if example:
            sections.extend(["", example])
    return "\n".join(sections)


def build_repair_prompt(
    design: FsmDesign,
    summary: str,
    detail: str,
    *,
    retrieval: bool = True,
) -> str:
    """Ask the model to edit the table using the judge's failure sentences."""
    sections = [
        "The compiled Verilog below failed verification. Fix the FSM JSON; "
        "do not rewrite the Verilog wrapper.",
        f"Failure: {summary}",
    ]
    if detail.strip():
        sections.extend(["Failure detail:", detail.strip()])
    sections.extend(
        [
            "",
            f'Keep address_byte "{design.address_byte}" and data_bytes '
            f"{json.dumps(design.data_bytes)} unchanged: they come from the spec.",
            "Keep the state names meaningful and the table valid.",
            'Every state that declares wait must also declare otherwise ("tick" '
            "for half_done waits); a missing default makes the table invalid.",
            "",
            _SEQUENCE_HELP,
            _CONDITION_HELP,
            "",
            "Current JSON:",
            json.dumps(design.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            "",
            "Return one corrected JSON object matching this schema (no prose):",
            _SCHEMA_TEXT,
        ]
    )
    if retrieval:
        example = _retrieval_text()
        if example:
            sections.extend(["", example])
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Parsing, validation and compilation


def extract_json_text(text: str) -> str:
    """Pull the JSON object out of a possibly chatty model response."""
    if not text:
        return text
    fenced = None
    marker = text.find("```")
    if marker != -1:
        after = text.find("\n", marker)
        if after != -1:
            end = text.find("```", after)
            if end != -1:
                fenced = text[after + 1:end]
    candidate = fenced if fenced is not None else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        return candidate[start:end + 1]
    return candidate.strip()


def _canon_hex(value: Any) -> str:
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return f"{int(text, 16):02x}"


def _protocol_problems(design: FsmDesign) -> List[str]:
    """Reject tables that cannot perform the transaction at all.

    These are not style preferences and they are not part of the structural
    validator: each one is a guaranteed protocol failure that the first real
    model table exhibited (compile and simulation pass, then no address, no
    data bits, no STOP). Catching them here costs one retry instead of a full
    simulation plus repair cycle.
    """
    problems: List[str] = []
    actions = {
        action
        for state in design.states
        for transition in state.transitions
        for action in transition.actions
    }
    drives = [
        drive
        for state in design.states
        for drive in (state.drive, *(t.drive for t in state.transitions))
    ]
    conditions = {
        condition
        for state in design.states
        for condition in (
            *([state.wait] if state.wait is not None else []),
            *(c for t in state.transitions for c in t.when),
        )
    }

    if Action.LOAD_ADDRESS not in actions:
        problems.append(
            "the design never loads the address byte; add the load_address action"
        )
    if Cond.STARTED not in conditions:
        problems.append(
            "the design never waits for started (tb_start); wait for it before "
            "sending the address byte"
        )
    if not any(drive.sda is Sda.BIT for drive in drives):
        problems.append(
            'the design never drives SDA from the data bits; use drive sda="bit" '
            "in the bit states"
        )
    if Action.FINISH not in actions and not any(
        drive.done is Done.HIGH for drive in drives
    ):
        problems.append(
            'tb_done never rises; add the finish action or drive done="high" '
            "after STOP"
        )
    return problems


def _evaluate_design(
    raw: str,
    *,
    expected_addr: Optional[str],
    expected_data: Optional[Sequence[str]],
) -> Tuple[Optional[FsmDesign], List[str]]:
    design, problems = parse_design(extract_json_text(raw))
    if design is None:
        return None, problems

    problems = validate_design(design)
    problems.extend(_protocol_problems(design))
    if expected_addr is not None and _canon_hex(design.address_byte) != _canon_hex(expected_addr):
        problems.append(
            f'address_byte must be "{expected_addr}" from the spec, '
            f'got "{design.address_byte}"'
        )
    if expected_data is not None:
        expected = [_canon_hex(item) for item in expected_data]
        actual = [_canon_hex(item) for item in design.data_bytes]
        if actual != expected:
            problems.append(
                f"data_bytes must be {json.dumps(expected)} from the spec, "
                f"got {json.dumps(actual)}"
            )
    if problems:
        return None, problems
    return design, []


def compile_design_with_json(
    design: FsmDesign, device_paths: Optional[Sequence[str]] = None
) -> str:
    """Compile a validated design and embed its source table in comments."""
    problems = validate_design(design)
    if problems:
        raise FsmError(problems)

    paths = list(device_paths) if device_paths else list(DEFAULT_DEVICE_PATHS)
    blocks = build_device_instance_blocks(paths)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    payload = json.dumps(
        design.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    embedded = f"{JSON_BEGIN}\n// {payload}\n{JSON_END}"
    fragment = embedded + "\n" + compile_fragment(design, blocks)
    return _assemble_multi_fragment(instance_code, fragment)


def extract_design_json(code: str) -> Optional[str]:
    """Return the JSON table embedded by :func:`compile_design_with_json`."""
    begin = code.find(JSON_BEGIN)
    if begin == -1:
        return None
    end = code.find(JSON_END, begin)
    if end == -1:
        return None
    for line in code[begin + len(JSON_BEGIN):end].splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            stripped = stripped[2:].strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped
    return None


def _correction_block(raw: str, problems: List[str]) -> str:
    lines = ["The previous answer was rejected:"]
    lines.extend(f"- {problem}" for problem in problems)
    lines.extend(
        [
            "",
            "Previous answer:",
            raw.strip(),
            "",
            "Fix those problems and return one corrected JSON object only.",
        ]
    )
    return "\n".join(lines)


def _request_design(
    prompt: str,
    *,
    model: Optional[str],
    temperature: float,
    seed: Optional[int],
    expected_addr: Optional[str],
    expected_data: Optional[Sequence[str]],
) -> Tuple[FsmDesign, str, str, List[Dict[str, Any]]]:
    """One model call, one retry on bad output, then ``FsmError``."""
    raw, metadata = _model_call(
        prompt, model=model, temperature=temperature, seed=seed
    )
    calls = [metadata]
    design, problems = _evaluate_design(
        raw, expected_addr=expected_addr, expected_data=expected_data
    )
    prompt_used = prompt
    if design is None:
        prompt_used = prompt + "\n\n" + _correction_block(raw, problems)
        raw, metadata = _model_call(
            prompt_used, model=model, temperature=temperature, seed=seed
        )
        calls.append(metadata)
        design, problems = _evaluate_design(
            raw, expected_addr=expected_addr, expected_data=expected_data
        )
        if design is None:
            raise FsmError(problems)
    return design, raw, prompt_used, calls


def _record_trace(
    trace: Optional[Dict[str, Any]],
    *,
    prompt: str,
    raw_output: str,
    rtl: str,
    model_calls: List[Dict[str, Any]],
    retrieval: bool,
    device_paths: Sequence[str],
    design_json: str,
) -> None:
    if trace is None:
        return
    # ``model_metadata`` must be a flat dict so the benchmark's token reader
    # sees this attempt. When a retry ran, sum both calls' token counts and
    # keep the last call's timings; the full per-call records stay auditable.
    last = dict(model_calls[-1]) if model_calls else {}
    last["prompt_eval_count"] = sum(
        int(call.get("prompt_eval_count") or 0) for call in model_calls
    )
    last["eval_count"] = sum(int(call.get("eval_count") or 0) for call in model_calls)
    trace.update(
        {
            "prompt": prompt,
            "raw_output": raw_output,
            "cleaned_code": rtl,
            "model_metadata": last,
            "model_calls": model_calls,
            "retrieval": {
                "enabled": retrieval,
                "example": str(RETRIEVAL_DESIGN.relative_to(REPO_ROOT)) if retrieval else "",
            },
            "device_paths": list(device_paths),
            "fsm_json": design_json,
        }
    )


# ---------------------------------------------------------------------------
# Pipeline hooks


def generate_fsm_rtl(
    device_paths: list[str],
    protocol_spec_path: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    seed: Optional[int] = None,
    retrieval: bool = True,
    patcher: bool = True,
    out_path: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a design via the JSON IR and return the compiled RTL string.

    ``patcher`` is accepted for hook compatibility; the IR path has no regex
    patcher because the compiler owns the wrapper.
    """
    del patcher  # IR output never goes through the regex patcher.
    spec = parse_i2c_spec(str(protocol_spec_path))
    prompt = build_generation_prompt(spec, retrieval=retrieval)
    design, raw, prompt_used, calls = _request_design(
        prompt,
        model=model,
        temperature=temperature,
        seed=seed,
        expected_addr=spec.address_hex,
        expected_data=spec.data_hexes,
    )
    rtl = compile_design_with_json(design, device_paths)
    design_json = json.dumps(
        design.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    _record_trace(
        trace,
        prompt=prompt_used,
        raw_output=raw,
        rtl=rtl,
        model_calls=calls,
        retrieval=retrieval,
        device_paths=device_paths,
        design_json=design_json,
    )
    if out_path:
        Path(out_path).write_text(rtl, encoding="utf-8")
    return rtl


def repair_fsm_rtl(
    code,
    failure_kind,
    failure_detail,
    model=None,
    temperature=0.3,
    seed=None,
    expected_addr=None,
    expected_data=None,
    device_paths=None,
    retrieval: bool = True,
    patcher: bool = True,
    out_path: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> str:
    """Edit the embedded FSM table using the judge's failure sentences."""
    del patcher  # IR output never goes through the regex patcher.
    embedded = extract_design_json(code or "")
    if embedded is None:
        raise FsmError(
            ["cannot locate the FSM JSON markers in the candidate Verilog"]
        )
    original, problems = parse_design(embedded)
    if original is None:
        raise FsmError([f"embedded FSM JSON is invalid: {problem}" for problem in problems])

    prompt = build_repair_prompt(
        original, failure_kind, failure_detail, retrieval=retrieval
    )
    # The embedded table is the transaction of record; the adapter's
    # expected_addr/expected_data (first byte only) are a secondary check.
    expected_addr = original.address_byte
    expected_data = list(original.data_bytes)
    design, raw, prompt_used, calls = _request_design(
        prompt,
        model=model,
        temperature=temperature,
        seed=seed,
        expected_addr=expected_addr,
        expected_data=expected_data,
    )
    device_paths = list(device_paths) if device_paths else list(DEFAULT_DEVICE_PATHS)
    rtl = compile_design_with_json(design, device_paths)
    design_json = json.dumps(
        design.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    _record_trace(
        trace,
        prompt=prompt_used,
        raw_output=raw,
        rtl=rtl,
        model_calls=calls,
        retrieval=retrieval,
        device_paths=device_paths,
        design_json=design_json,
    )
    if out_path:
        Path(out_path).write_text(rtl, encoding="utf-8")
    return rtl
