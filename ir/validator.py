"""Semantic validation for FSM designs (workflow step 82).

Pydantic only proves the JSON has the right *shape*. These checks prove the
state table is actually well formed: every edge lands somewhere, every
state is reachable, and every state that can fall through says what it
does instead of silently hanging the half-period counter.

The counter checks are path sensitive on purpose. A design-wide "some
transition reloads it" test passes tables that still fail simulation: the
reload can sit on a branch that never executes before the counter is
tested. These checks ask the real question -- can the reload be reached
before the read, on *every* path from reset.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from ir.schema import Action, Cond, FsmDesign, Otherwise, Sda, State

# Signals the fixed wrapper or the compiler already declares. A design that
# reuses one of these as a state name would silently rebind it, so this is a
# hard error rather than a warning.
RESERVED_STATE_NAMES = frozenset(
    {
        "state", "bit_idx", "byte_idx", "cur_byte", "half_cnt", "half_done",
        "advance_half", "sda_drive_low", "scl_drive_low", "tb_done",
        "tb_start", "clk", "rst_n", "sda", "scl", "addr_byte", "half",
        "default",
    }
)


def validate_design(design: FsmDesign) -> List[str]:
    """Return every problem found in ``design``; empty means it compiles."""
    problems: List[str] = []
    names = design.state_names

    duplicates = _duplicates(names)
    for name in duplicates:
        problems.append(f"duplicate state name '{name}'")

    known: Set[str] = set(names)
    for name in names:
        if name.lower() in RESERVED_STATE_NAMES:
            problems.append(
                f"state name '{name}' is reserved by the wrapper or compiler"
            )

    if design.reset_state not in known:
        problems.append(f"undefined state '{design.reset_state}' (reset_state)")

    for state in design.states:
        for transition in state.transitions:
            if transition.next not in known:
                problems.append(
                    f"undefined state '{transition.next}' "
                    f"(target of state '{state.name}')"
                )

    # Unreachable states are dead weight the model usually meant to wire up.
    if design.reset_state in known:
        reachable = _reachable(design, known)
        for name in names:
            if name not in reachable:
                problems.append(f"unreachable state '{name}'")

    for state in design.states:
        problems.extend(_check_default(state))
    problems.extend(_check_counters(design))

    return problems


def _duplicates(names: List[str]) -> List[str]:
    seen: Set[str] = set()
    dupes: List[str] = []
    for name in names:
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    return dupes


def _reachable(design: FsmDesign, known: Set[str]) -> Set[str]:
    """States reachable from ``reset_state`` following transition targets."""
    by_name: Dict[str, State] = design.state_by_name
    seen: Set[str] = set()
    queue: List[str] = [design.reset_state]
    while queue:
        current = queue.pop()
        if current in seen or current not in known:
            continue
        seen.add(current)
        for transition in by_name[current].transitions:
            if transition.next not in seen:
                queue.append(transition.next)
    return seen


def _check_default(state: State) -> List[str]:
    """A state that can fall through must say what it does instead."""
    problems: List[str] = []
    has_always = any(
        transition.when == [Cond.ALWAYS] for transition in state.transitions
    )
    falls_through = bool(state.transitions) and not has_always
    if state.otherwise is None and (falls_through or state.wait is not None):
        problems.append(
            f"missing default in state '{state.name}': "
            "declare otherwise as 'tick' or 'hold'"
        )

    if state.wait is not None and not state.transitions:
        problems.append(
            f"state '{state.name}' waits on '{state.wait.value}' but has no "
            "transitions, so it can never leave"
        )

    # Waiting on the half-period tick while refusing to advance it deadlocks
    # the state: half_done can never become true.
    if state.wait is Cond.HALF_DONE and state.otherwise is Otherwise.HOLD:
        problems.append(
            f"state '{state.name}' waits on half_done but holds instead of "
            "tick, so the counter never advances"
        )

    for index, transition in enumerate(state.transitions):
        if transition.when == [Cond.ALWAYS] and index != len(state.transitions) - 1:
            problems.append(
                f"dead transition after 'always' in state '{state.name}' "
                f"(position {index + 1} of {len(state.transitions)})"
            )
            break
    return problems


_BIT_GUARDS = frozenset({Cond.BIT_LAST, Cond.BIT_NOT_LAST})
_BYTE_GUARDS = frozenset({Cond.BYTE_DONE, Cond.BYTE_NOT_LAST})


def _reads_bit_idx(state: State) -> bool:
    """True when the state's behaviour depends on ``bit_idx``."""
    if any(cond in _BIT_GUARDS for t in state.transitions for cond in t.when):
        return True
    if state.drive.sda is Sda.BIT:
        return True
    if any(t.drive.sda is Sda.BIT for t in state.transitions):
        return True
    return any(Action.NEXT_BIT in t.actions for t in state.transitions)


def _reads_byte_idx(state: State) -> bool:
    """True when the state's behaviour depends on ``byte_idx``."""
    if any(cond in _BYTE_GUARDS for t in state.transitions for cond in t.when):
        return True
    return any(Action.ADVANCE_BYTE in t.actions for t in state.transitions)


def _reloaded_on_every_path(design: FsmDesign, action: Action) -> Dict[str, bool]:
    """Greatest fixpoint: has ``action`` run on *every* path from reset?

    Power-on never performs an action, so ``reset_state`` is pinned false
    and every other state starts optimistic, then weakens until stable.
    A cycle whose only entries all reload stays true; a single reload-free
    entry path drags the state to false.
    """
    good: Dict[str, bool] = {name: True for name in design.state_names}
    good[design.reset_state] = False
    changed = True
    while changed:
        changed = False
        for state in design.states:
            if state.name == design.reset_state:
                continue  # pinned false by the power-on entry
            value = True
            for other in design.states:
                for transition in other.transitions:
                    if transition.next != state.name:
                        continue
                    if action in transition.actions:
                        continue  # this edge establishes the value
                    if not good[other.name]:
                        value = False
                        break
                if not value:
                    break
            if value != good[state.name]:
                good[state.name] = value
                changed = True
    return good


def _successors(design: FsmDesign) -> Dict[str, Set[str]]:
    return {state.name: {t.next for t in state.transitions} for state in design.states}


def _reachable_from(design: FsmDesign, start: str) -> Set[str]:
    succ = _successors(design)
    seen: Set[str] = set()
    stack: List[str] = [start]
    while stack:
        current = stack.pop()
        for nxt in succ.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _reaches_itself(design: FsmDesign, name: str) -> bool:
    """Can the machine come back to ``name``? (i.e. it sits on a cycle)"""
    succ = _successors(design)
    seen: Set[str] = set()
    stack: List[str] = list(succ.get(name, ()))
    while stack:
        current = stack.pop()
        if current == name:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(succ.get(current, ()))
    return False


def _advance_edges(design: FsmDesign) -> List[Tuple[str, str]]:
    return [
        (state.name, transition.next)
        for state in design.states
        for transition in state.transitions
        if Action.ADVANCE_BYTE in transition.actions
    ]


def _returns_after_advance(design: FsmDesign, reader: str) -> bool:
    """Can ``advance_byte`` run somewhere and then bring control back?

    Without a return edge the reader is re-evaluated against an unchanged
    ``byte_idx``, so a ``byte_done`` test can never flip.
    """
    edges = _advance_edges(design)
    if not edges:
        return False
    forward = _reachable_from(design, reader)
    for source, target in edges:
        if source in forward and reader in _reachable_from(design, target):
            return True
    return False


def _check_counters(design: FsmDesign) -> List[str]:
    """Counter guards must be fed by a reload that can actually execute.

    The design-wide version of this check asked only "does *some*
    transition reload the counter". That passes tables which still hang in
    simulation, because the reload can sit on a branch that never runs
    before the guard is evaluated. Both checks below are path sensitive:

    * ``bit_idx`` must have been reloaded by ``restart_bits`` on every
      path from reset to the reader. Reset leaves ``bit_idx`` at 0, so a
      reader reached without a reload clocks one bit instead of eight.
    * ``byte_idx`` readers that sit on a cycle need an ``advance_byte``
      edge they can reach and come back from, otherwise ``byte_done`` can
      never become true and the data loop spins forever.
    """
    problems: List[str] = []
    reachable = _reachable(design, set(design.state_names))

    bit_readers = [s for s in design.states if s.name in reachable and _reads_bit_idx(s)]
    if bit_readers:
        reloaded = _reloaded_on_every_path(design, Action.RESTART_BITS)
        for state in bit_readers:
            if not reloaded[state.name]:
                problems.append(
                    f"state '{state.name}' reads bit_idx but some path from "
                    "reset never performs restart_bits, so bit_idx is still 0 "
                    "and only one bit would be clocked"
                )

    byte_readers = [s for s in design.states if s.name in reachable and _reads_byte_idx(s)]
    for state in byte_readers:
        if _reaches_itself(design, state.name) and not _returns_after_advance(
            design, state.name
        ):
            problems.append(
                f"state '{state.name}' tests byte_idx in a loop but no "
                "advance_byte can run and return to it, so byte_done can "
                "never become true while the state keeps testing it"
            )
    return problems
