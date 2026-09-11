from agent.ollama_client import generate_verilog
from agent.rtl_cleaner import clean_rtl
from tools.file_tool import save_file
from tools.verilog_parser import parse_verilog_file

GLUE_PROMPT = """You are an expert RTL designer.

Device A source:
```verilog
{device_a_source}
```
Device A interface (authoritative -- use these EXACT names/widths):
{device_a_interface}

Device B source:
```verilog
{device_b_source}
```
Device B interface (authoritative -- use these EXACT names/widths):
{device_b_interface}

Protocol specification you must implement:
{protocol_spec}

Task: generate ONLY a new top-level Verilog module named `top_glue` with
EXACTLY this port list (do not add, remove, or rename ports):

module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output wire tb_done
);

It must implement the I2C transaction described in the spec (real state
machine, not a stub): a START condition, the address byte 0x50 write
(0xA0), waiting for ACK, one data byte, waiting for ACK, then STOP.
tb_done must go high once STOP has been issued.

Output ONLY the module in a single ```verilog code block. Verilog-2001,
synthesizable, no delays used for functional timing, no testbench
constructs (no $display/$finish/initial blocks driving logic).
"""

REPAIR_PROMPT = """The following top_glue module {failure_kind}.

Code:
```verilog
{code}
```

{failure_detail}

Fix the code. Keep the module name `top_glue` and its exact port list
unchanged:

module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output wire tb_done
);

Output ONLY the corrected module in a single ```verilog code block,
nothing else.
"""


def generate_glue_rtl(device_a_path, device_b_path, protocol_spec_path,
                       out_path="generated/top_glue.v", model=None):
    a = parse_verilog_file(device_a_path)
    b = parse_verilog_file(device_b_path)
    spec = open(protocol_spec_path).read()

    prompt = GLUE_PROMPT.format(
        device_a_source=a.raw_source, device_a_interface=a.interface_summary(),
        device_b_source=b.raw_source, device_b_interface=b.interface_summary(),
        protocol_spec=spec,
    )
    kwargs = {"model": model} if model else {}
    rtl = clean_rtl(generate_verilog(prompt, **kwargs))
    save_file(out_path, rtl)
    return out_path, prompt


def repair_rtl(code, failure_kind, failure_detail, out_path="generated/top_glue.v", model=None):
    prompt = REPAIR_PROMPT.format(code=code, failure_kind=failure_kind, failure_detail=failure_detail)
    kwargs = {"model": model} if model else {}
    fixed = clean_rtl(generate_verilog(prompt, **kwargs))
    save_file(out_path, fixed)
    return fixed, prompt
