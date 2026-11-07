"""Deterministic compiler from an FSM design to ``top_glue`` RTL.

The compiler owns every part of the module a small model reliably gets
wrong: the port list, the device instances, the open-drain drivers, the
half-period counter and the datapath registers. The design JSON only
describes the state table, so a model error can at worst produce a wrong
protocol sequence -- never a module that fails to parse.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from agent.rtl_generator import (
    _assemble_multi_fragment,
    _device_input_defaults,
    build_device_instance_blocks,
)
from ir.schema import (
    Action,
    Cond,
    Done,
    Drive,
    FsmDesign,
    FsmError,
    Otherwise,
    Scl,
    Sda,
    State,
    Transition,
)
from ir.validator import validate_design

DEFAULT_DEVICE_PATHS: Sequence[str] = (
    "devices/device_sensor.v",
    "devices/device_controller.v",
)


def compile_design(
    design: FsmDesign, device_paths: Optional[Iterable[str]] = None
) -> str:
    """Compile ``design`` into a complete ``top_glue`` module."""
    problems = validate_design(design)
    if problems:
        raise FsmError(problems)
    paths = list(device_paths) if device_paths else list(DEFAULT_DEVICE_PATHS)
    blocks = build_device_instance_blocks(paths)
    instance_code = "\n\n".join(block["code"] for block in blocks)
    return _assemble_multi_fragment(
        instance_code, compile_fragment(design, blocks)
    )


def compile_fragment(design: FsmDesign, blocks: List[dict]) -> str:
    """Emit the declarations and always block that sit in the wrapper."""
    # The half-period counter is live whenever the design ticks it, waits on
    # it, or tests it: otherwise a ``tick`` state would advance a counter no
    # transition ever reloads.
    uses_timer = any(
        state.wait is Cond.HALF_DONE
        or state.otherwise is Otherwise.TICK
        or any(Cond.HALF_DONE in transition.when for transition in state.transitions)
        for state in design.states
    )
    lines: List[str] = [f"    localparam ADDR_BYTE = 8'h{design.address_byte.upper()};"]
    lines.extend(
        f"    localparam DATA_BYTE_{index} = 8'h{byte.upper()};"
        for index, byte in enumerate(design.data_bytes)
    )
    lines.append(f"    localparam HALF = {design.half};")
    lines.append("")
    lines.extend(
        f"    localparam {state.name} = {index};"
        for index, state in enumerate(design.states)
    )
    lines.extend(
        [
            "",
            f"    reg [{_state_width(design) - 1}:0] state;",
            "    reg [2:0]  bit_idx;",
            "    reg [7:0]  byte_idx;",
            "    reg [7:0]  cur_byte;",
            "    reg [7:0]  half_cnt;",
            "",
            "    task automatic advance_half;",
            "        begin",
            "            if (half_cnt == HALF - 1) half_cnt <= 0;",
            "            else half_cnt <= half_cnt + 1;",
            "        end",
            "    endtask",
            "",
            "    wire half_done = (half_cnt == HALF - 1);",
            "",
            "    always @(posedge clk or negedge rst_n) begin",
            "        if (!rst_n) begin",
            f"            state <= {design.reset_state};",
            "            sda_drive_low <= 0;",
            "            scl_drive_low <= 0;",
            "            tb_done <= 0;",
            "            half_cnt <= 0;",
            "            bit_idx <= 0;",
            "            byte_idx <= 0;",
            "            cur_byte <= 8'h00;",
            *_device_input_defaults(blocks),
            "        end else begin",
            "            case (state)",
            "",
        ]
    )
    for state in design.states:
        lines.extend(_state_block(state, design, uses_timer))
        lines.append("")
    lines.extend(
        [
            f"                default: state <= {design.reset_state};",
            "            endcase",
            "        end",
            "    end",
        ]
    )
    return "\n".join(lines) + "\n"


def _state_width(design: FsmDesign) -> int:
    """Bits needed to encode every state index."""
    return max(1, (len(design.states) - 1).bit_length())


def _state_block(state: State, design: FsmDesign, uses_timer: bool) -> List[str]:
    indent = " " * 16
    body = " " * 20
    lines = [f"{indent}{state.name}: begin"]
    lines.extend(
        f"{body}{expression}"
        for expression in _drive_expressions(state.drive)
    )
    chain_indent = body + ("    " if state.wait is not None else "")
    chain = _chain_lines(
        state, design, chain_indent, uses_timer and state.wait is None
    )
    if state.wait is not None:
        lines.append(f"{body}if ({_condition(state.wait, design)}) begin")
        # Restart the period as soon as the wait condition fires, so the
        # next entry into this state starts counting from zero again.
        if uses_timer:
            lines.append(f"{chain_indent}half_cnt <= 8'd0;")
        lines.extend(chain)
        if state.otherwise is Otherwise.TICK:
            lines.append(f"{body}end else begin")
            lines.append(f"{body}    advance_half;")
            lines.append(f"{body}end")
        else:
            lines.append(f"{body}end")
    else:
        lines.extend(chain)
    lines.append(f"{indent}end")
    return lines


def _chain_lines(
    state: State,
    design: FsmDesign,
    indent: str,
    reset_timer: bool,
) -> List[str]:
    """Emit the guarded transition chain for ``state``.

    When the state has no ``wait``, the chain's trailing ``else`` is the
    state's ``otherwise``: that is the only place "nothing matched" can
    be observed. States that do wait handle ``otherwise`` themselves, on
    the far side of the ``if (wait)``.
    """
    if not state.transitions:
        # A terminal state that declares ``tick`` still owns the counter:
        # dropping the advance would silently freeze it even though the
        # design asked for a running period.
        if state.wait is None and state.otherwise is Otherwise.TICK:
            return [f"{indent}advance_half;"]
        return []
    lines: List[str] = []
    last = len(state.transitions) - 1
    for index, transition in enumerate(state.transitions):
        keyword = "if" if index == 0 else "else if"
        lines.append(f"{indent}{keyword} ({_guard(transition, design)}) begin")
        lines.extend(
            f"{indent}    {expression}"
            for expression in _transition_body(transition, design, reset_timer)
        )
        if index == last and state.wait is None and state.otherwise is Otherwise.TICK:
            lines.append(f"{indent}end else begin")
            lines.append(f"{indent}    advance_half;")
            lines.append(f"{indent}end")
        else:
            lines.append(f"{indent}end")
    return lines


def _transition_body(
    transition: Transition,
    design: FsmDesign,
    uses_timer: bool,
) -> List[str]:
    lines: List[str] = []
    if uses_timer:
        lines.append("half_cnt <= 8'd0;")
    lines.extend(_drive_expressions(transition.drive))
    for action in transition.actions:
        lines.extend(_action_lines(action, design))
    lines.append(f"state <= {transition.next};")
    return lines


def _drive_expressions(drive: Drive) -> List[str]:
    expressions: List[str] = []
    if drive.sda is Sda.LOW:
        expressions.append("sda_drive_low <= 1'b1;")
    elif drive.sda is Sda.HIGH:
        expressions.append("sda_drive_low <= 1'b0;")
    elif drive.sda is Sda.BIT:
        expressions.append("sda_drive_low <= ~cur_byte[bit_idx];")
    if drive.scl is Scl.LOW:
        expressions.append("scl_drive_low <= 1'b1;")
    elif drive.scl is Scl.HIGH:
        expressions.append("scl_drive_low <= 1'b0;")
    if drive.done is Done.LOW:
        expressions.append("tb_done <= 1'b0;")
    elif drive.done is Done.HIGH:
        expressions.append("tb_done <= 1'b1;")
    return expressions


def _action_lines(action: Action, design: FsmDesign) -> List[str]:
    if action is Action.LOAD_ADDRESS:
        return ["cur_byte <= ADDR_BYTE;", "byte_idx <= 8'd0;"]
    if action is Action.ADVANCE_BYTE:
        lines: List[str] = []
        for index in range(design.nbytes):
            keyword = "if" if index == 0 else "else if"
            lines.append(f"{keyword} (byte_idx == 8'd{index}) begin")
            lines.append(f"    cur_byte <= DATA_BYTE_{index};")
            lines.append(f"    byte_idx <= 8'd{index + 1};")
            lines.append("end")
        return lines
    if action is Action.RESTART_BITS:
        return ["bit_idx <= 3'd7;"]
    if action is Action.NEXT_BIT:
        return ["bit_idx <= bit_idx - 3'd1;"]
    if action is Action.FINISH:
        return ["tb_done <= 1'b1;"]
    if action is Action.CLEAR_DONE:
        return ["tb_done <= 1'b0;"]
    raise FsmError([f"unsupported action {action!r}"])


def _guard(transition: Transition, design: FsmDesign) -> str:
    parts = [_condition(cond, design) for cond in transition.when]
    if len(parts) == 1:
        return parts[0]
    return "(" + ") && (".join(parts) + ")"


def _condition(cond: Cond, design: FsmDesign) -> str:
    if cond is Cond.ALWAYS:
        return "1'b1"
    if cond is Cond.STARTED:
        return "tb_start"
    if cond is Cond.HALF_DONE:
        return "half_done"
    if cond is Cond.BIT_LAST:
        return "bit_idx == 3'd0"
    if cond is Cond.BIT_NOT_LAST:
        return "bit_idx != 3'd0"
    if cond is Cond.BYTE_DONE:
        return f"byte_idx >= 8'd{design.nbytes}"
    if cond is Cond.BYTE_NOT_LAST:
        return f"byte_idx < 8'd{design.nbytes}"
    raise FsmError([f"unsupported condition {cond!r}"])
