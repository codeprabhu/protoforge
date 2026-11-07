"""FSM JSON intermediate representation for ProtoForge.

A small model emits a flat JSON state table instead of Verilog. A
deterministic compiler turns that table into the behaviour fragment that
sits inside the fixed ``top_glue`` wrapper, so the only thing the model
has to get right is the protocol state machine.
"""

from ir.schema import FsmDesign, FsmError, parse_design
from ir.validator import validate_design

__all__ = ["FsmDesign", "FsmError", "parse_design", "validate_design"]
