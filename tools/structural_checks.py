"""Fast pre-compile checks for generated synthesizable I2C master RTL."""

import re


def _strip_comments(rtl: str) -> str:
    rtl = re.sub(r"/\*[\s\S]*?\*/", "", rtl)
    return re.sub(r"//[^\n]*", "", rtl)


def _open_drain(code: str, net: str) -> bool:
    """Require exactly one tri-state open-drain continuous driver for ``net``.

    The expression must be ``condition ? 0 : z`` or ``condition ? z : 0``.
    Parse the top-level ternary rather than relying on a regex that can be
    fooled by a second, active-high driver or by a nested condition.
    """
    assignments = re.findall(
        rf"\bassign\s+{re.escape(net)}\s*=\s*([^;]+);", code, re.I
    )
    if len(assignments) != 1:
        return False
    expr = assignments[0].strip()

    # Locate the top-level '?' and its matching ':' while ignoring nested
    # parentheses and ternaries in the condition.
    depth = 0
    question = None
    ternary_depth = 0
    colon = None
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and ch == "?":
            if question is None:
                question = i
            ternary_depth += 1
        elif depth == 0 and ch == ":" and question is not None:
            ternary_depth -= 1
            if ternary_depth == 0:
                colon = i
                break
    if question is None or colon is None or not expr[:question].strip():
        return False
    true_branch = re.sub(r"\s+", "", expr[question + 1:colon]).lower()
    false_branch = re.sub(r"\s+", "", expr[colon + 1:]).lower()
    low = {"1'b0", "1'h0", "1'd0", "1'b0"}
    high_z = {"1'bz", "1'hz", "1'dz"}
    return (true_branch in low and false_branch in high_z) or (true_branch in high_z and false_branch in low)


def structural_checks(rtl: str, required_instances: list) -> dict:
    """Catch structural/protocol anti-patterns before invoking a compiler."""
    code_only = _strip_comments(rtl)
    header_match = re.search(r"\bmodule\s+top_glue\s*\((.*?)\)\s*;", code_only, re.I | re.S)
    header = header_match.group(1) if header_match else ""
    port_names = re.findall(r"\b(?:input|output|inout)\b\s+(?:(?:wire|reg)\s+)?(?:\[[^\]]+\]\s*)?(\w+)", header, re.I)
    required_ports = ["clk", "rst_n", "sda", "scl", "tb_start", "tb_done"]
    normalized_ports = [p.lower() for p in port_names]
    port_directions = [d.lower() for d in re.findall(r"\b(input|output|inout)\b", header, re.I)]
    required_directions = ["input", "input", "inout", "inout", "input", "output"]

    # Legal open-drain continuous assignments are excluded from the
    # procedural-write scan below. Direct procedural writes to SDA/SCL are
    # a semantic bug even where the compiler allows them.
    no_bus_assign_lines = re.sub(
        r"\bassign\s+(?:sda|scl)\s*=\s*[^;]*;", "", code_only, flags=re.I
    )

    checks = {
        "has_top_glue": header_match is not None,
        "has_exact_top_ports": normalized_ports == required_ports,
        "has_correct_top_port_directions": port_directions == required_directions,
        "has_always_block": bool(re.search(r"\balways\s*@", code_only, re.I)),
        "no_markdown_escapes": "\\_" not in rtl and "\\*" not in rtl,
        "no_delay_statements": not bool(re.search(r"#\s*\d", code_only)),
        "no_wait_statements": not bool(re.search(r"\bwait\s*\(", code_only, re.I)),
        "no_initial_blocks": not bool(re.search(r"\binitial\b", code_only, re.I)),
        "no_brace_statement_blocks": not bool(
            re.search(r"\{[^{}]*<=[^{}]*,[^{}]*<=", code_only)
        ),
        "open_drain_sda": _open_drain(code_only, "sda"),
        "open_drain_scl": _open_drain(code_only, "scl"),
        "no_procedural_bus_writes": not bool(re.search(r"\b(?:sda|scl)\s*(?:<=|=(?!=))", no_bus_assign_lines, re.I)),
        "has_tb_done_logic": bool(re.search(r"\btb_done\s*<=", code_only)),
    }

    for name in required_instances:
        pattern = re.compile(rf"\b{re.escape(name)}\s+\w+\s*\(", re.I)
        checks[f"instantiates_{name}"] = len(pattern.findall(code_only)) == 1

    failed = [name for name, passed in checks.items() if not passed]
    return {"pass": not failed, "failed_checks": failed, "checks": checks}


STRUCTURAL_EXPLANATIONS = {
    "has_top_glue": "The module is not named top_glue or its module header is malformed.",
    "has_exact_top_ports": "top_glue must expose exactly clk, rst_n, sda, scl, tb_start, and tb_done in that order.",
    "has_correct_top_port_directions": "Use input clk, input rst_n, inout SDA/SCL, input tb_start, and output tb_done.",
    "has_always_block": "No clocked always block was found; implement a sequential FSM.",
    "no_markdown_escapes": "The code contains markdown escape artifacts such as \\_ or \\*.",
    "no_delay_statements": "The RTL uses #<number> delay controls. Use a cycle counter instead.",
    "no_wait_statements": "The RTL uses wait, which is forbidden in this synthesizable Verilog task.",
    "no_initial_blocks": "The RTL uses an initial block. Initialize sequential state through the active-low reset instead.",
    "no_brace_statement_blocks": "Curly braces cannot group statements in Verilog. Use begin ... end.",
    "open_drain_sda": "SDA must be driven open-drain: assign sda = sda_drive_low ? 1'b0 : 1'bz (with an internal drive-low signal).",
    "open_drain_scl": "SCL must be driven open-drain: assign scl = scl_drive_low ? 1'b0 : 1'bz (with an internal drive-low signal).",
    "no_procedural_bus_writes": "Never assign SDA or SCL inside an always block; only drive them through open-drain continuous assigns.",
    "has_tb_done_logic": "tb_done must be assigned by the sequential control logic after STOP.",
}


def explain_structural_failures(failed_checks, required_instances) -> str:
    parts = []
    for check in failed_checks:
        if check.startswith("instantiates_"):
            name = check[len("instantiates_"): ]
            parts.append(
                f"Instantiate {name} exactly once using its exact port names and the pre-generated signal map."
            )
        else:
            parts.append(STRUCTURAL_EXPLANATIONS.get(check, check))
    return " ".join(parts)
