"""Pydantic schema for the FSM JSON intermediate representation.

The design is deliberately small: an ordered list of states, each with
open-drain drive levels, an optional wait condition, an explicit fallback
(``otherwise``) and a list of guarded transitions. Everything the fixed
wrapper already owns (ports, device instances, open-drain assigns, the
half-period counter, ``cur_byte``/``bit_idx``/``byte_idx``) is *not*
part of the JSON.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, List, Optional, Union

from pydantic import BaseModel, Field, field_validator


class Sda(str, Enum):
    """How the FSM drives SDA during a state."""

    LOW = "low"      # sda_drive_low <= 1  (actively pulled down)
    HIGH = "high"    # sda_drive_low <= 0  (released, bus pulls up)
    HOLD = "hold"    # no assignment; keep the previous drive level
    BIT = "bit"      # drive cur_byte[bit_idx] inverted onto the bus


class Scl(str, Enum):
    """How the FSM drives SCL during a state."""

    LOW = "low"
    HIGH = "high"
    HOLD = "hold"


class Done(str, Enum):
    """How the FSM drives ``tb_done`` during a state."""

    LOW = "low"
    HIGH = "high"
    HOLD = "hold"


class Cond(str, Enum):
    """Boolean guards available to a transition.

    ``half_done`` is the fixed wrapper's half-period tick, so a state that
    waits on it also needs ``otherwise: "tick"`` or the counter freezes.
    """

    ALWAYS = "always"
    STARTED = "started"            # tb_start is high
    HALF_DONE = "half_done"        # half-period counter reached its terminal count
    BIT_LAST = "bit_last"          # bit_idx == 0
    BIT_NOT_LAST = "bit_not_last"  # bit_idx != 0
    BYTE_DONE = "byte_done"        # byte_idx >= number of payload bytes
    BYTE_NOT_LAST = "byte_not_last"  # byte_idx < number of payload bytes


class Action(str, Enum):
    """Datapath updates a transition may request."""

    LOAD_ADDRESS = "load_address"    # cur_byte <= ADDR_BYTE; byte_idx <= 0
    ADVANCE_BYTE = "advance_byte"    # cur_byte <= payload[byte_idx]; byte_idx++
    RESTART_BITS = "restart_bits"    # bit_idx <= 7
    NEXT_BIT = "next_bit"            # bit_idx <= bit_idx - 1
    FINISH = "finish"                # tb_done <= 1
    CLEAR_DONE = "clear_done"        # tb_done <= 0


class Otherwise(str, Enum):
    """What a state does when no transition guard is true."""

    TICK = "tick"    # advance the half-period counter
    HOLD = "hold"    # do nothing and stay


class Drive(BaseModel):
    """Drive levels applied unconditionally while the state is active."""

    sda: Sda = Sda.HOLD
    scl: Scl = Scl.HOLD
    done: Done = Done.HOLD


class Transition(BaseModel):
    """One guarded edge out of a state."""

    when: List[Cond]
    next: str
    drive: Drive = Field(default_factory=Drive)
    actions: List[Action] = Field(default_factory=list)

    @field_validator("when", mode="before")
    @classmethod
    def _accept_bare_string(cls, value: Any) -> Any:
        """Let ``"when": "started"`` stand in for ``["started"]``."""
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("when")
    @classmethod
    def _non_empty(cls, value: List[Cond]) -> List[Cond]:
        if not value:
            raise ValueError("a transition needs at least one condition")
        return value


class State(BaseModel):
    """One FSM state: unconditional drives plus guarded transitions."""

    name: str
    drive: Drive = Field(default_factory=Drive)
    wait: Optional[Cond] = None
    otherwise: Optional[Otherwise] = None
    transitions: List[Transition] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"state name {value!r} is not a Verilog identifier")
        return value


class FsmDesign(BaseModel):
    """A complete FSM description plus the transaction it must perform."""

    version: int = 1
    name: str = "fsm"
    address_byte: str  # hex string, e.g. "a0"
    data_bytes: List[str]  # hex strings, e.g. ["3c"] or ["11", "22"]
    half: int = 4
    reset_state: str = "IDLE"
    states: List[State]

    @field_validator("version")
    @classmethod
    def _supported(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported FSM design version {value}")
        return value

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"design name {value!r} is not a Verilog identifier")
        return value

    @field_validator("address_byte")
    @classmethod
    def _hex_byte(cls, value: str) -> str:
        return _check_hex_byte(value, "address_byte")

    @field_validator("data_bytes")
    @classmethod
    def _hex_bytes(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("data_bytes must contain at least one byte")
        if len(value) > 255:
            raise ValueError("data_bytes supports at most 255 bytes")
        return [_check_hex_byte(item, "data_bytes[]") for item in value]

    @field_validator("half")
    @classmethod
    def _half_range(cls, value: int) -> int:
        if not 1 <= value <= 255:
            raise ValueError("half must be between 1 and 255 clock cycles")
        return value

    @field_validator("reset_state")
    @classmethod
    def _reset_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"reset_state {value!r} is not a Verilog identifier")
        return value

    @field_validator("states")
    @classmethod
    def _at_least_one(cls, value: List[State]) -> List[State]:
        if not value:
            raise ValueError("a design needs at least one state")
        return value

    @property
    def state_names(self) -> List[str]:
        return [state.name for state in self.states]

    @property
    def state_by_name(self) -> dict:
        return {state.name: state for state in self.states}

    @property
    def nbytes(self) -> int:
        return len(self.data_bytes)


def _check_hex_byte(value: str, field: str) -> str:
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not re.fullmatch(r"[0-9a-f]{1,2}", text):
        raise ValueError(f"{field} must be a 1-2 digit hex byte, got {value!r}")
    return text


class FsmError(Exception):
    """Raised when a design cannot be parsed or compiled."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def parse_design(payload: Union[str, bytes, dict]) -> tuple[Optional[FsmDesign], List[str]]:
    """Parse JSON text or a dict into a design.

    Returns ``(design, problems)``. ``design`` is ``None`` when the payload
    could not be read at all; schema errors come back as strings so a
    repair loop can show them to the model verbatim.
    """
    if isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return None, [f"invalid JSON: {exc}"]
    elif isinstance(payload, dict):
        data = payload
    else:
        return None, [f"expected a JSON object, got {type(payload).__name__}"]

    if not isinstance(data, dict):
        return None, [f"expected a JSON object, got {type(data).__name__}"]

    try:
        return FsmDesign.model_validate(data), []
    except Exception as exc:  # pydantic ValidationError (and defensive fallback)
        return None, _schema_problems(exc)


def _schema_problems(exc: Exception) -> List[str]:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        problems = []
        for item in errors():
            location = ".".join(str(part) for part in item.get("loc", ()))
            message = item.get("msg", str(exc))
            problems.append(f"{location}: {message}" if location else message)
        return problems or [str(exc)]
    return [str(exc)]
