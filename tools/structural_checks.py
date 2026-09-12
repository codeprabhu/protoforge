"""
Cheap, fast, string-level checks run on generated RTL *before* it's ever
handed to iverilog. Catches the failure mode where the model produces
syntactically-fine Verilog that completely ignores the actual task (e.g.
a standalone I2C FSM with no device instantiation at all) -- something
a compiler will never flag, since "compiles" and "does the assigned task"
are unrelated properties.
"""

import re


def structural_checks(rtl: str, required_instances: list) -> dict:
    """
    required_instances: e.g. ["device_sensor", "device_controller"] --
    the module names that MUST appear as instantiated instances (not just
    mentioned in a comment) in the generated top-level module.
    """
    # Strip line comments before pattern-matching so a "#10" or "{ ... }"
    # mentioned only in a comment doesn't false-positive.
    code_only = re.sub(r"//.*", "", rtl)

    checks = {
        "has_top_glue": bool(re.search(r"\bmodule\s+top_glue\b", rtl)),
        "has_always_block": "always @" in rtl or "always@" in rtl,
        "no_markdown_escapes": "\\_" not in rtl and "\\*" not in rtl,
        # `#10` etc is testbench-style delay control -- meaningless/illegal
        # inside a clocked always block meant for synthesizable hardware.
        # Small local models reach for this constantly when they don't
        # know how to implement clock-divided timing with a counter.
        "no_delay_statements": not bool(re.search(r"#\s*\d", code_only)),
        # `{ a <= 0, b <= 1 }` is not legal Verilog -- {} is bit
        # concatenation syntax, not a statement block. This is a distinct
        # anti-pattern from missing begin/end and needs its own message.
        "no_brace_statement_blocks": not bool(
            re.search(r"\{[^{}]*<=[^{}]*,[^{}]*<=", code_only)
        ),
    }

    for name in required_instances:
        # crude but effective: "<module_name> <instance_name> (" is the
        # universal Verilog instantiation shape. A bare mention in a
        # comment won't match this pattern.
        pattern = re.compile(rf"\b{re.escape(name)}\s+\w+\s*\(")
        checks[f"instantiates_{name}"] = bool(pattern.search(rtl))

    failed = [k for k, v in checks.items() if not v]
    return {"pass": len(failed) == 0, "failed_checks": failed, "checks": checks}


STRUCTURAL_EXPLANATIONS = {
    "has_top_glue": "The module is not named `top_glue`, or the module header is malformed.",
    "has_always_block": "No `always @` block was found -- this looks like combinational-only code with no actual state machine.",
    "no_markdown_escapes": "The code contains markdown escape artifacts like `\\_` or `\\*` which are not legal Verilog.",
    "no_delay_statements": (
        "The code uses `#<number>` delay control (e.g. `#10`) inside a clocked always block. "
        "This is a testbench-only construct and is not synthesizable hardware -- it will not "
        "behave the way you expect inside `always @(posedge clk)`. To wait N clock cycles, "
        "use a counter register: increment it every clock edge, and only advance to the next "
        "state when the counter reaches the target value. Remove every `#` delay from the design."
    ),
    "no_brace_statement_blocks": (
        "The code uses curly braces `{ ... }` to group multiple statements, e.g. "
        "`{ a <= 0, b <= 1 }`. This is illegal -- curly braces in Verilog are ONLY for bit "
        "concatenation (e.g. `{a, b}` to join two signals), never for grouping statements. "
        "To execute multiple statements together, use `begin ... end` with a semicolon after "
        "each individual statement, e.g. `begin a <= 0; b <= 1; end`."
    ),
}


def explain_structural_failures(failed_checks, required_instances) -> str:
    parts = []
    for c in failed_checks:
        if c.startswith("instantiates_"):
            name = c[len("instantiates_"):]
            parts.append(
                f"The module {name} was NOT instantiated anywhere in the code. "
                f"You MUST add an instance of {name}, e.g. `{name} u_{name} (.clk(clk), ...);`, "
                f"using its exact port names."
            )
        else:
            parts.append(STRUCTURAL_EXPLANATIONS.get(c, c))
    return " ".join(parts)
