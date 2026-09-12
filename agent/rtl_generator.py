from agent.ollama_client import generate_verilog
from agent.rtl_cleaner import clean_rtl
from tools.file_tool import save_file
from tools.verilog_parser import parse_verilog_file, ModuleInfo

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

The generated FSM must contain:

    IDLE
    START
    SEND_ADDR
    ACK_ADDR
    SEND_DATA
    ACK_DATA
    STOP
    DONE

Use counters and state transitions.

NEVER use # delays.
NEVER use wait statements.
NEVER use initial blocks.
NEVER use curly braces for statement grouping.
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
"""


def generate_glue_rtl(device_a_path, device_b_path, protocol_spec_path,
                       out_path="generated/top_glue.v", model=None, debug_prompt_path="generated/last_prompt.txt"):
    a = parse_verilog_file(device_a_path)
    b = parse_verilog_file(device_b_path)
    spec = open(protocol_spec_path).read()

    a_inst = build_instance_block(a, "a", "u_device_a")
    b_inst = build_instance_block(b, "b", "u_device_b")

    prompt = GLUE_PROMPT.format(
        device_a_module=a.name, device_a_signal_map=_signal_map_summary("a", a_inst["signals"]),
        device_a_instance_code=a_inst["code"],
        device_b_module=b.name, device_b_signal_map=_signal_map_summary("b", b_inst["signals"]),
        device_b_instance_code=b_inst["code"],
        protocol_spec=spec,
        implementation_rules=I2C_IMPLEMENTATION_RULES,
    )

    if debug_prompt_path:
        save_file(debug_prompt_path, prompt)

    kwargs = {"model": model} if model else {}
    rtl = clean_rtl(generate_verilog(prompt, **kwargs))
    save_file(out_path, rtl)
    return out_path, prompt


def repair_rtl(code, failure_kind, failure_detail, out_path="generated/top_glue.v", model=None, temperature=None):
    prompt = REPAIR_PROMPT.format(code=code, failure_kind=failure_kind, failure_detail=failure_detail)
    kwargs = {}
    if model:
        kwargs["model"] = model
    if temperature is not None:
        kwargs["temperature"] = temperature
    fixed = clean_rtl(generate_verilog(prompt, **kwargs))
    save_file(out_path, fixed)
    return fixed, prompt
