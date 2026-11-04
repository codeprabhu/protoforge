"""Prompt construction and deterministic wrapping for generated I2C RTL.

The model only writes the behaviour-bearing fragment. The top-level ports,
device instances, and open-drain bus drivers are generated deterministically
from the parsed device interfaces, so a small model cannot corrupt them.

Generators return the RTL **string**; writing it to disk is optional and is
never required to pass the code to the next pipeline stage.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agent import ollama_client
from agent.rtl_cleaner import clean_rtl
from tools.file_tool import save_file
from tools.rtl_memory import retrieve_rtl_context
from tools.verilog_parser import ModuleInfo, parse_verilog_file


def generate_verilog(*args, **kwargs):
    """Indirection keeps model calls patchable in integration tests."""
    return ollama_client.generate_verilog(*args, **kwargs)


I2C_IMPLEMENTATION_RULES = """
IMPORTANT:

The testbench only observes the external SDA and SCL wires.

Driving internal device signals such as:

    a_reg_write
    a_reg_addr
    a_reg_wdata
    b_start
    b_target_addr
    b_write_data

DOES NOT perform an I2C transaction.

The generated FSM itself must directly generate:

    START
    Address byte 0xA0
    ACK cycle
    Data byte
    ACK cycle
    STOP

on the SDA and SCL wires.

The protocol spec requires:

    START -> 0xA0 -> ACK -> DATA -> ACK -> STOP

I2C is an open-drain bus.

Create:

    reg sda_drive_low;
    reg scl_drive_low;

and drive the bus using:

    assign sda = sda_drive_low ? 1'b0 : 1'bz;
    assign scl = scl_drive_low ? 1'b0 : 1'bz;

Never assign directly to SDA or SCL inside an always block.

ILLEGAL:

    sda <= 1'b0;
    scl <= 1'b1;

LEGAL:

    sda_drive_low <= 1'b1;
    scl_drive_low <= 1'b0;

Use a clocked, explicit FSM with these behaviors/states (names may differ only
when the phases are unambiguously represented):

    IDLE -> START -> START_HOLD -> address bits -> address ACK
         -> data bits -> data ACK -> STOP -> DONE

Use separate low/high clock phases for each bit and ACK. Keep SCL released
(high) while setting up the START and STOP transitions. A counter must hold
each SCL half-period long enough for the testbench to observe it. Drive both
bus lines open-drain using internal *_drive_low registers and tri-state assigns.

NEVER use # delays, wait statements, or initial blocks. Do not use SystemVerilog
syntax: target plain Verilog-2001. Never use curly braces for statement grouping.
"""


def build_instance_block(info: ModuleInfo, label: str, instance_name: str) -> dict:
    """Generate wire/reg declarations plus the instantiation for one device.

    ``label`` namespaces internal signal names (e.g. ``a_reg_write``) so two
    devices with identically named ports do not collide.

    Returns ``{"code": <verilog text>, "signals": {port: internal_name}}`` so
    the prompt can tell the model exactly which signals it may drive or read.
    """
    decls = []
    conns = []
    signals = {}

    for port in info.ports:
        if port.name in ("clk", "rst_n"):
            conns.append(f".{port.name}({port.name})")
            signals[port.name] = port.name
            continue

        internal_name = f"{label}_{port.name}"
        width = f"{port.width} " if port.width else ""
        if port.direction == "input":
            decls.append(f"    reg  {width}{internal_name};")
        else:  # output (or inout, treated as wire here -- v1 devices have none)
            decls.append(f"    wire {width}{internal_name};")

        conns.append(f".{port.name}({internal_name})")
        signals[port.name] = internal_name

    conn_str = ",\n        ".join(conns)
    code = "\n".join(decls) + f"\n\n    {info.name} {instance_name} (\n        {conn_str}\n    );"
    return {"code": code, "signals": signals}


def build_device_instance_blocks(device_paths: list[str]) -> list[dict]:
    """Parse and wire any number of contract-compliant device modules.

    Device order is part of the generated interface: it deterministically maps
    devices to labels a, b, c, ... so the model never invents instance names.
    """
    if not device_paths:
        raise ValueError("At least one device is required")
    blocks = []
    for index, path in enumerate(device_paths):
        if index >= 26:
            raise ValueError("At most 26 devices are supported in Verilog-2001 v1")
        info = parse_verilog_file(path)
        label = chr(ord("a") + index)
        instance_name = f"u_device_{label}"
        block = build_instance_block(info, label, instance_name)
        blocks.append({
            "path": path,
            "info": info,
            "label": label,
            "instance_name": instance_name,
            **block,
        })
    return blocks


def _signal_map_summary(label: str, signals: dict) -> str:
    return "\n".join(
        f"  {port} -> {sig}" for port, sig in signals.items() if port not in ("clk", "rst_n")
    )


MULTI_SMALL_MODEL_PROMPT = """You are generating only the behavior-bearing control fragment for a fixed
Verilog-2001 I2C wrapper. The harness already declares clk, rst_n, tb_start,
tb_done, the open-drain sda/scl drivers, and every device instance below.
Do not output a module header, module instances, bus assignments, or endmodule.

Devices and their generated internal signals:
{device_maps}

Implement the transaction required by this protocol specification:
{protocol_spec}

The local device ports are not the I2C bus. The FSM must directly create all
START, address, data, ACK and STOP transitions on sda/scl. Keep each device
interface correctly driven according to its port direction; never invent a
port or rename a generated signal.

Implementation constraints:
{implementation_rules}

The fragment must contain declarations for its own state and one synthesizable
clocked always block. The harness already owns sda_drive_low and scl_drive_low:
do not declare them again. Return only plain Verilog-2001 declarations and the
always block, with no markdown or prose.

Verified context:
{retrieved_context}
"""


def _assemble_multi_fragment(instance_code: str, fragment: str) -> str:
    return f'''module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

    // Generated deterministically from parsed device interfaces.
{instance_code}

    // Open-drain bus drivers are fixed by the harness.
    reg sda_drive_low;
    reg scl_drive_low;
    assign sda = sda_drive_low ? 1'b0 : 1'bz;
    assign scl = scl_drive_low ? 1'b0 : 1'bz;

    // Model-generated FSM fragment begins here.
{fragment.rstrip()}
    // Model-generated FSM fragment ends here.
endmodule
'''


def normalize_model_rtl(generated: str, device_paths: list[str] | None = None,
                        address_hex: str = "a0", data_hex: str = "3c") -> str:
    """Put model behaviour behind the deterministic wrapper used by generation.

    Repair responses frequently ignore the request for a complete module or
    return a wrapper with stale device instances. Reusing the same scaffold
    for repairs prevents those responses from changing the hardware contract
    between attempts; only the behaviour-bearing fragment is accepted.
    """
    if not device_paths:
        return generated
    blocks = build_device_instance_blocks(device_paths)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    return _assemble_multi_fragment(
        instance_code, _coerce_full_module_to_fragment(generated, address_hex, data_hex)
    )


def _remove_fixed_bus_declarations(fragment: str) -> str:
    """Remove redundant declarations for signals owned by the scaffold."""
    fragment = re.sub(
        r"(?im)^\s*(?:reg|wire|logic)\s+(?:\[[^\]]+\]\s+)?sda_drive_low\s*;\s*(?://[^\n]*)?\n?",
        "",
        fragment,
    )
    fragment = re.sub(
        r"(?im)^\s*(?:reg|wire|logic)\s+(?:\[[^\]]+\]\s+)?scl_drive_low\s*;\s*(?://[^\n]*)?\n?",
        "",
        fragment,
    )
    fragment = re.sub(
        r"(?im)^\s*(?:reg|wire|logic)\s+sda_drive_low\s*,\s*scl_drive_low\s*;\s*(?://[^\n]*)?\n?",
        "",
        fragment,
    )
    return fragment


def _normalize_fragment_scaffold(fragment: str, address_hex: str = "a0", data_hex: str = "3c") -> str:
    """Supply protocol-scaffold symbols omitted by a small model."""
    known = {
        "ADDR_BYTE": f"localparam ADDR_BYTE = 8'h{address_hex.upper()};",
        "DATA_BYTE": f"localparam DATA_BYTE = 8'h{data_hex.upper()};",
        "HALF": "localparam HALF = 4;",
        "IDLE": "localparam IDLE = 0;", "START": "localparam START = 1;",
        "START_HOLD": "localparam START_HOLD = 2;", "BIT_LOW": "localparam BIT_LOW = 3;",
        "BIT_HIGH": "localparam BIT_HIGH = 4;", "ACK_LOW": "localparam ACK_LOW = 5;",
        "ACK_HIGH": "localparam ACK_HIGH = 6;", "NEXT_BYTE": "localparam NEXT_BYTE = 7;",
        "STOP_LOW": "localparam STOP_LOW = 8;", "STOP_HIGH": "localparam STOP_HIGH = 9;",
        "DONE": "localparam DONE = 10;",
    }
    declarations = []
    for name, declaration in known.items():
        needed_by_half_done = name == "HALF" and re.search(r"\bhalf_done\b", fragment)
        if (re.search(rf"\b{name}\b", fragment) or needed_by_half_done) and not re.search(
                rf"\b(?:localparam|parameter|reg|wire|integer)\b[^;]*\b{name}\b", fragment):
            declarations.append(declaration)
    if re.search(r"\bhalf_done\b", fragment) and not re.search(
            r"\b(?:reg|wire|logic)\b[^;]*\bhalf_done\b", fragment):
        declarations.append("wire half_done;\nassign half_done = (half_cnt == HALF - 1);")
    return ("\n".join(declarations) + "\n" if declarations else "") + fragment


def _coerce_full_module_to_fragment(generated: str, address_hex: str = "a0", data_hex: str = "3c") -> str:
    """Extract behaviour from a model that ignored the fragment contract."""
    if not re.search(r"\bmodule\s+top_glue\b", generated, re.I):
        return _normalize_fragment_scaffold(
            _remove_fixed_bus_declarations(generated), address_hex, data_hex
        )
    modules = list(re.finditer(r"(?im)\bmodule\s+top_glue\b", generated))
    candidate = generated[modules[-1].start():] if modules else generated
    header = re.search(r"\)\s*;", candidate, re.S)
    end = candidate.lower().rfind("endmodule")
    if not header or end <= header.end():
        return _normalize_fragment_scaffold(
            _remove_fixed_bus_declarations(candidate), address_hex, data_hex
        )
    body = candidate[header.end():end]
    # Start after the model's wrapper/device section. The last instance close
    # before the sequential block is a stable boundary for ANSI Verilog;
    # otherwise fall back to the first declaration in a standalone response.
    always_match = re.search(r"(?im)^\s*always\b", body)
    if always_match:
        prefix = body[:always_match.start()]
        localparams = list(re.finditer(r"(?im)^\s*(?:localparam|parameter)\b", prefix))
        if localparams:
            body = body[localparams[-1].start():]
        else:
            instance_end = prefix.rfind(");")
            if instance_end >= 0 and "endmodule" not in prefix[instance_end:]:
                body = body[instance_end + 2:]
    if body == candidate[header.end():end]:
        anchor = re.search(r"(?im)^\s*(?:localparam|parameter|reg|wire)\b", body)
        if anchor:
            body = body[anchor.start():]
    # The reference wrapper declares these transaction constants before its
    # device instances; retain them when extracting after the instances.
    if not re.search(r"\blocalparam\s+ADDR_BYTE\b", body):
        body = f"localparam ADDR_BYTE = 8'h{address_hex.upper()};\n" + body
    if not re.search(r"\blocalparam\s+DATA_BYTE\b", body):
        body = f"localparam DATA_BYTE = 8'h{data_hex.upper()};\n" + body
    if not re.search(r"\blocalparam\s+HALF\b", body):
        body = "localparam HALF = 4;\n" + body
    body = re.sub(r"(?im)^\s*assign\s+(?:sda|scl)\s*=.*?;\s*$\n?", "", body)
    # The deterministic shell owns these bus-driver names and polarity.
    body = re.sub(r"\bsda_oe\b", "sda_drive_low", body)
    body = re.sub(r"\bscl_oe\b", "scl_drive_low", body)
    return _normalize_fragment_scaffold(_remove_fixed_bus_declarations(body), address_hex, data_hex)


def _behavior_context(context: str) -> str:
    """Keep retrieved examples from reintroducing arbitrary top wrappers."""
    if re.search(r"\bmodule\s+top_glue\b", context, re.I):
        extracted = _coerce_full_module_to_fragment(context)
        if not re.search(r"\bmodule\s+top_glue\b", extracted, re.I):
            return extracted
    return context


def _specialize_protocol_prompt(prompt: str, address_hex: str = "a0", data_hex: str = "3c") -> str:
    """Replace v1 literals in reusable instructions with spec values."""
    return (prompt.replace("8'hA0", f"8'h{address_hex.upper()}")
                  .replace("0xA0", f"0x{address_hex.upper()}")
                  .replace("8'h3C", f"8'h{data_hex.upper()}")
                  .replace("0x3C", f"0x{data_hex.upper()}"))


REPAIR_PROMPT = """The following top_glue module {failure_kind}.

Full code:
```verilog
{code}
```

{failure_detail}

Common mistakes to check for specifically:
- Curly braces `{{ ... }}` are ONLY for bit concatenation, never for
  grouping statements. `{{ a <= 0, b <= 1 }}` is ILLEGAL. Use
  `begin a <= 0; b <= 1; end` instead.
- `#<number>` delays (e.g. `#10`) do not belong inside a clocked
  `always @(posedge clk)` block -- that is testbench-only syntax and is
  not synthesizable. To wait N clock cycles, use a counter register that
  increments every clock edge and gates a state transition on reaching
  a target value.
- Every `case` item with more than one statement MUST be wrapped in
  `begin ... end`, e.g. `START: begin a <= 1; b <= 0; end`. Without it,
  only the first statement belongs to that case item and everything
  after causes a parse error.
- Do not read a signal in the same always block on the same clock edge
  you just assigned it with `<=` in a prior statement -- that reads the
  OLD value, not the one you just set.

Verified reference context (use as a timing pattern; output only the repaired design):
{verified_context}

Fix the code. Keep the module name `top_glue` and its exact port list
unchanged:

module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg tb_done
);

Output ONLY the corrected module in a single ```verilog code block,
nothing else.
Additional I2C-specific checks:

- SDA and SCL are open-drain inout wires.
- Never assign directly to SDA or SCL in an always block.
- Use internal drive signals and continuous assigns.
- START = SDA falling while SCL high.
- STOP = SDA rising while SCL high.
- Address byte must be 8'hA0.
- Two ACK phases must exist.
- Do not use device_controller as an I2C master.
- Do not use # delays.

Repair discipline:
- Preserve every protocol phase that is not named in the failure diagnosis.
- If address_ack, data_ack, correct_address, and correct_data already pass,
  do not rewrite the address/data bit FSM. Repair only the missing termination:
  hold SDA low with SCL low, release SCL, release SDA while SCL is high, then
  assert tb_done after STOP.
- If start/address/data checks fail, do not assert tb_done early. Keep the
  transaction active until both ACK clocks and STOP have completed.
- Return the complete corrected module, but make the smallest behavioral edit
  that fixes the reported checks.
"""


def _repair_discipline(failure_detail: str) -> str:
    """Turn observed checks into an explicit minimal-change repair contract."""
    detail = failure_detail.lower()
    preserved = []
    for name, label in (
        ("start", "START"), ("address_ack", "address ACK"),
        ("data_ack", "data ACK"), ("correct_address", "address byte"),
        ("correct_data", "data byte"), ("stop", "STOP"),
        ("tb_done", "tb_done"),
    ):
        if name not in detail or f"{name} passed" in detail:
            preserved.append(label)
    if preserved:
        return "Already-valid phases that MUST remain unchanged: " + ", ".join(preserved) + "."
    return "No phase is known valid; implement the full required sequence and do not shortcut to tb_done."


def _record_trace(
    trace: Optional[Dict[str, Any]],
    *,
    prompt: str,
    raw_output: str,
    retrieved_context: str,
    device_paths: List[str],
) -> None:
    if trace is None:
        return
    trace.update({
        "prompt": prompt,
        "raw_output": raw_output,
        "retrieved_context": retrieved_context,
        "device_paths": list(device_paths),
    })


def generate_multi_device_glue_rtl(
    device_paths: list[str],
    protocol_spec_path: str,
    model: Optional[str] = None,
    temperature: float = 0.0,
    seed: Optional[int] = None,
    retrieval: bool = True,
    out_path: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a deterministic wrapper around a model-written FSM fragment.

    Returns the complete ``top_glue`` source as a string. If ``out_path`` is
    given the same source is also written there (convenience only; the live
    pipeline keeps the string and writes its own per-attempt file).
    """
    from tools.i2c_spec import parse_i2c_spec

    blocks = build_device_instance_blocks(device_paths)
    spec = open(protocol_spec_path, encoding="utf-8").read()
    parsed_spec = parse_i2c_spec(protocol_spec_path)
    maps = []
    for block in blocks:
        maps.append(
            f"Device {block['label'].upper()} ({block['info'].name}) is "
            f"instantiated as `{block['instance_name']}`:\n"
            + _signal_map_summary(block["label"], block["signals"])
        )
    retrieved_context = _behavior_context(retrieve_rtl_context(
        "I2C open-drain master FSM START address ACK data STOP Verilog-2001",
        max_examples=1, max_chars=10000, enabled=retrieval,
    ))
    prompt = MULTI_SMALL_MODEL_PROMPT.format(
        device_maps="\n\n".join(maps), protocol_spec=spec,
        implementation_rules=I2C_IMPLEMENTATION_RULES,
        retrieved_context=retrieved_context,
    )
    prompt = _specialize_protocol_prompt(prompt, parsed_spec.address_hex, parsed_spec.data_hex)
    kwargs: Dict[str, Any] = {"temperature": temperature}
    if seed is not None:
        kwargs["seed"] = seed
    if model:
        kwargs["model"] = model
    raw_output = generate_verilog(prompt, **kwargs)
    rtl = normalize_model_rtl(
        clean_rtl(raw_output), device_paths, parsed_spec.address_hex, parsed_spec.data_hex
    )
    if out_path:
        save_file(out_path, rtl)
    _record_trace(
        trace, prompt=prompt, raw_output=raw_output,
        retrieved_context=retrieved_context, device_paths=device_paths,
    )
    return rtl


def generate_glue_rtl(device_a_path, device_b_path, protocol_spec_path, **kwargs) -> str:
    """Two-device convenience wrapper kept for older callers and tests."""
    return generate_multi_device_glue_rtl(
        [device_a_path, device_b_path], protocol_spec_path, **kwargs
    )


def repair_rtl(
    code,
    failure_kind,
    failure_detail,
    model=None,
    temperature=None,
    seed=None,
    expected_addr="a0",
    expected_data="3c",
    device_paths=None,
    retrieval: bool = True,
    out_path: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> str:
    """Return repaired ``top_glue`` source for the given failure diagnosis."""
    repair_context = _behavior_context(retrieve_rtl_context(
        "I2C master repair open drain START STOP ACK address 0xA0 data byte clock phases Verilog-2001",
        max_examples=1,
        max_chars=8000,
        enabled=retrieval,
    ))
    prompt = REPAIR_PROMPT.format(
        code=code,
        failure_kind=failure_kind,
        failure_detail=failure_detail,
        verified_context=repair_context,
    )
    prompt = _specialize_protocol_prompt(prompt, expected_addr, expected_data)
    prompt += "\nObserved-failure preservation contract:\n" + _repair_discipline(failure_detail) + "\n"
    kwargs = {}
    if model:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature
    if seed is not None:
        kwargs["seed"] = seed
    raw_output = generate_verilog(prompt, **kwargs)
    fixed = normalize_model_rtl(
        clean_rtl(raw_output), device_paths, expected_addr, expected_data
    )
    if out_path:
        save_file(out_path, fixed)
    _record_trace(
        trace, prompt=prompt, raw_output=raw_output,
        retrieved_context=repair_context, device_paths=list(device_paths or []),
    )
    return fixed
