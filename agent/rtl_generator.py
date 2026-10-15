import re

from agent import ollama_client
from agent.rtl_cleaner import clean_rtl
from tools.file_tool import save_file
from tools.verilog_parser import parse_verilog_file, ModuleInfo
from tools.rtl_memory import retrieve_rtl_context


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
    """
    Deterministically generates the wire/reg declarations + instantiation
    for a device, from its parsed port list. This removes the single
    biggest LLM failure mode observed in practice (the model simply not
    instantiating the devices, or getting port connections wrong) by
    never asking the LLM to write this part at all.

    `label` is a short prefix (e.g. "a", "b") used to namespace internal
    signal names so two devices with identically-named ports don't collide.

    Returns {"code": <verilog text>, "signals": {port_name: internal_signal_name}}
    so the caller can tell the LLM exactly what to drive/read.
    """
    decls = []
    conns = []
    signals = {}

    for p in info.ports:
        if p.name in ("clk", "rst_n"):
            # tie straight to the top-level signal of the same name
            conns.append(f".{p.name}({p.name})")
            signals[p.name] = p.name
            continue

        internal_name = f"{label}_{p.name}"
        width = f"{p.width} " if p.width else ""
        if p.direction == "input":
            decls.append(f"    reg  {width}{internal_name};")
        else:  # output (or inout, treated as wire here -- devices in v1 have none)
            decls.append(f"    wire {width}{internal_name};")

        conns.append(f".{p.name}({internal_name})")
        signals[p.name] = internal_name

    conn_str = ",\n        ".join(conns)
    code = "\n".join(decls) + f"\n\n    {info.name} {instance_name} (\n        {conn_str}\n    );"
    return {"code": code, "signals": signals}


def build_device_instance_blocks(device_paths: list[str]) -> list[dict]:
    """Parse and wire any number of contract-compliant device modules.

    The order is part of the generated interface: it deterministically maps
    devices to labels a, b, c, ... and prevents the model from inventing
    instance names or port connections.
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
    lines = [f"  {port} -> {sig}" for port, sig in signals.items() if port not in ("clk", "rst_n")]
    return "\n".join(lines)


GLUE_PROMPT = """You are completing a partially-written Verilog module. Both
devices are ALREADY instantiated below with all wiring pre-generated --
do not remove, rename, or re-declare them or their signals. Your ONLY job
is to write the I2C master state machine in the marked TODO section,
driving/reading the internal signals listed below.

Device A ({device_a_module}) is instantiated as `u_device_a`. Its signals
you can drive/read from your FSM:
{device_a_signal_map}

Device B ({device_b_module}) is instantiated as `u_device_b`. Its signals
you can drive/read from your FSM:
{device_b_signal_map}

Protocol specification to implement in the marked section:
{protocol_spec}

Additional implementation requirements:
{implementation_rules}

Retrieved design context from verified examples and previous passing runs:
{retrieved_context}

The retrieved example is for protocol timing guidance only. Use the instance
block below, obey the current device interfaces and exact top-level contract,
and do not copy any forbidden constructs from historical examples.

Here is the module you must complete. Copy the instantiation section
EXACTLY as shown -- it is already correct and complete. Only write code
where marked TODO:

```verilog
module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

    // --- Device A: copy exactly, do not modify ---
{device_a_instance_code}

    // --- Device B: copy exactly, do not modify ---
{device_b_instance_code}

    // --- TODO: implement the I2C master state machine here ---
    // Requirements: on tb_start, issue START, write address byte 0xA0
    // (0x50 write), wait for ACK, write one data byte, wait for ACK,
    // issue STOP, then set tb_done = 1.
    //
    // RULES (violating any of these will fail to compile):
    // - Use non-blocking assignments (<=) for all sequential state. Never
    //   read a signal in the same always block on the same clock edge
    //   you just assigned it with <= in a prior statement -- that reads
    //   the OLD value, not the one you just set.
    // - NEVER use `#<number>` delays (e.g. #10). This is synthesizable
    //   hardware, not a testbench. To wait N clock cycles, use a counter
    //   register that increments every clock edge and gates a state
    //   transition on reaching a target count.
    // - NEVER use curly braces to group multiple statements (that
    //   syntax is ONLY for bit concatenation, e.g. joining two signals
    //   into one wider bus). To execute multiple statements together
    //   use begin ... end, e.g. begin a <= 0; b <= 1; end.
    // - Every `case` item with more than one statement MUST be wrapped
    //   in `begin ... end`, e.g. `START: begin a <= 1; b <= 0; end`.

endmodule
```

Output ONLY the complete module (instantiations + your state machine) in
a single ```verilog code block. Do not use markdown escaping (no
backslash before underscores or asterisks) -- this is a plain text
Verilog file, not markdown prose.
"""


SMALL_MODEL_PROMPT = """You are generating the control fragment for a fixed Verilog-2001 I2C wrapper.
Do NOT generate a module header, device declarations/instances, bus assignments,
or endmodule. Those are generated deterministically by the harness. Return only
RTL declarations needed by your FSM and one clocked always block.

Fixed signals already declared by the harness:
- clk, rst_n, tb_start, tb_done
- sda_drive_low and scl_drive_low are regs; 1 means pull the line low, 0 means release it
- SDA/SCL are already wired as open-drain: assign line = drive_low ? 1'b0 : 1'bz
- Device A is instantiated as u_device_a; ports/signals: {device_a_signal_map}
- Device B is instantiated as u_device_b; ports/signals: {device_b_signal_map}

Task: implement exactly one transaction after tb_start: START, address byte 8'hA0
(MSB first), slave ACK clock, one data byte 8'h3C (MSB first), slave ACK clock,
STOP, then assert tb_done. The testbench observes only external SDA/SCL; writing
internal device registers is not a substitute for an I2C transaction.

Rules:
- START is SDA falling while SCL is released/high; STOP is SDA rising while SCL is high.
- Each data bit has a low phase to set SDA and a high phase to sample/clock it.
- Release SDA during each ACK so the slave can pull it low.
- Keep SCL released during START/STOP transitions. Use a counter so phases last
  several clk cycles (the testbench must observe the edges).
- Use an explicit FSM and nonblocking assignments in always @(posedge clk or negedge rst_n).
- Reset state, counters, drive registers and tb_done in the reset branch.
- No initial, wait, #delay, SystemVerilog-only syntax, curly-brace statement blocks,
  or procedural assignments to SDA/SCL. Use plain Verilog-2001.
- Every multi-statement case arm must use begin/end. Do not omit default recovery.

Protocol spec:
{protocol_spec}

Implementation constraints:
{implementation_rules}

Retrieved verified RTL (reference only; do not copy module/interface/instance wrappers):
{retrieved_context}

Return only the declarations and always block, no markdown fences and no prose.
"""


def _assemble_fragment(a_instance_code: str, b_instance_code: str, fragment: str) -> str:
    """Wrap a model-produced FSM fragment in a deterministic module shell.

    Full-module output remains accepted for backwards compatibility, but the
    small-model path asks for only the behavior-bearing fragment so the model
    cannot corrupt the port list or device instantiations.
    """
    return f'''module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output reg  tb_done
);

    // Generated deterministically from parsed device interfaces.
{a_instance_code}

{b_instance_code}

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
    """Put model behavior behind the deterministic wrapper used by generation.

    Repair responses frequently ignore the request for a complete module or
    return a wrapper with stale device instances.  Reusing the same scaffold
    for repairs prevents those responses from changing the hardware contract
    between attempts; only the behavior-bearing fragment is accepted.
    """
    if not device_paths:
        return generated
    blocks = build_device_instance_blocks(device_paths)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    return _assemble_multi_fragment(
        instance_code, _coerce_full_module_to_fragment(generated, address_hex, data_hex)
    )


def _remove_fixed_bus_declarations(fragment: str) -> str:
    """Remove redundant declarations for signals owned by the scaffold.

    The fragment prompt says these registers already exist, but a model may
    still echo their declarations. Removing only standalone declarations is a
    safe normalization that avoids spending a repair on a deterministic name
    collision; assignments and declarations of all model-owned signals remain
    untouched.
    """
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
    """Extract behavior from a model that ignored the fragment contract."""
    if not re.search(r"\bmodule\s+top_glue\b", generated, re.I):
        return _normalize_fragment_scaffold(
            _remove_fixed_bus_declarations(generated), address_hex, data_hex
        )
    modules = list(re.finditer(r"(?im)\bmodule\s+top_glue\b", generated))
    candidate = generated[modules[-1].start():] if modules else generated
    header = re.search(r"\)\s*;", candidate, re.S)
    end = candidate.lower().rfind("endmodule")
    if not header or end <= header.end():
        return _normalize_fragment_scaffold(_remove_fixed_bus_declarations(candidate), address_hex, data_hex)
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
    if body == generated[header.end():end]:
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


def generate_glue_rtl(device_a_path, device_b_path, protocol_spec_path,
                       out_path="generated/top_glue.v", model=None, debug_prompt_path="generated/last_prompt.txt",
                       temperature=0.0, seed=None):
    a = parse_verilog_file(device_a_path)
    b = parse_verilog_file(device_b_path)
    spec = open(protocol_spec_path).read()

    a_inst = build_instance_block(a, "a", "u_device_a")
    b_inst = build_instance_block(b, "b", "u_device_b")

    retrieval_query = (
        "I2C open-drain master FSM START START_HOLD address 0xA0 MSB-first "
        "ACK data byte ACK STOP low high clock phases counter tb_done synthesizable Verilog-2001"
    )
    retrieved_context = _behavior_context(
        retrieve_rtl_context(retrieval_query, max_examples=1, max_chars=10000)
    )

    signal_map_a = _signal_map_summary("a", a_inst["signals"])
    signal_map_b = _signal_map_summary("b", b_inst["signals"])
    # Ask even small models for only the behavior-bearing fragment. The
    # wrapper/ports/instances/open-drain drivers are deterministic scaffolding.
    prompt = SMALL_MODEL_PROMPT.format(
        device_a_signal_map=signal_map_a,
        device_b_signal_map=signal_map_b,
        protocol_spec=spec,
        implementation_rules=I2C_IMPLEMENTATION_RULES,
        retrieved_context=retrieved_context,
    )

    if debug_prompt_path:
        save_file(debug_prompt_path, prompt)

    kwargs = {"temperature": temperature}
    if seed is not None:
        kwargs["seed"] = seed
    if model:
        kwargs["model"] = model
    generated = clean_rtl(generate_verilog(prompt, **kwargs))
    # Preserve compatibility with full-module responses from older prompts or
    # models that ignore the fragment contract; those still go through gates.
    rtl = _assemble_fragment(
        a_inst["code"], b_inst["code"], _coerce_full_module_to_fragment(generated)
    )
    save_file(out_path, rtl)
    return out_path, prompt


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


def generate_multi_device_glue_rtl(
    device_paths: list[str], protocol_spec_path: str,
    out_path: str = "generated/top_glue.v", model=None,
    debug_prompt_path: str | None = "generated/last_prompt.txt",
    temperature: float = 0.0, seed=None,
):
    """Generate a fixed wrapper for an arbitrary set of device modules."""
    blocks = build_device_instance_blocks(device_paths)
    spec = open(protocol_spec_path, encoding="utf-8").read()
    from tools.i2c_spec import parse_i2c_spec
    parsed_spec = parse_i2c_spec(protocol_spec_path)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    maps = []
    for block in blocks:
        maps.append(
            f"Device {block['label'].upper()} ({block['info'].name}) is "
            f"instantiated as `{block['instance_name']}`:\n"
            + _signal_map_summary(block["label"], block["signals"])
        )
    retrieved_context = _behavior_context(retrieve_rtl_context(
        "I2C open-drain master FSM START address ACK data STOP Verilog-2001",
        max_examples=1, max_chars=10000,
    ))
    prompt = MULTI_SMALL_MODEL_PROMPT.format(
        device_maps="\n\n".join(maps), protocol_spec=spec,
        implementation_rules=I2C_IMPLEMENTATION_RULES,
        retrieved_context=retrieved_context,
    )
    prompt = _specialize_protocol_prompt(prompt, parsed_spec.address_hex, parsed_spec.data_hex)
    if debug_prompt_path:
        save_file(debug_prompt_path, prompt)
    kwargs = {"temperature": temperature}
    if seed is not None:
        kwargs["seed"] = seed
    if model:
        kwargs["model"] = model
    generated = clean_rtl(generate_verilog(prompt, **kwargs))
    rtl = normalize_model_rtl(generated, device_paths, parsed_spec.address_hex,
                              parsed_spec.data_hex)
    save_file(out_path, rtl)
    return out_path, prompt


def repair_rtl(code, failure_kind, failure_detail, out_path="generated/top_glue.v", model=None,
               temperature=None, seed=None, expected_addr="a0", expected_data="3c",
               device_paths=None):
    repair_context = _behavior_context(retrieve_rtl_context(
        "I2C master repair open drain START STOP ACK address 0xA0 data byte clock phases Verilog-2001",
        max_examples=1,
        max_chars=8000,
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
    fixed = clean_rtl(generate_verilog(prompt, **kwargs))
    fixed = normalize_model_rtl(fixed, device_paths, expected_addr, expected_data)
    save_file(out_path, fixed)
    return fixed, prompt
