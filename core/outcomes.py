"""Canonical names for terminal run outcomes.

One name per outcome, shared by the orchestrator and the batch runner so a
failure cannot silently land in the "other" bucket (workflow Phase 1, step 17).
"""

from __future__ import annotations

PASS = "pass"
GENERATION_ERROR = "generation_error"
STRUCTURAL_FAILED = "structural_failed"
COMPILE_FAILED = "compile_failed"
HARDWARE_FAILED = "hardware_failed"
SIM_CHECKS_FAILED = "sim_checks_failed"
TOOL_MISSING = "tool_missing"

# Every value the orchestrator may put in ``log["final"]``.
KNOWN_FINALS = frozenset({
    PASS,
    GENERATION_ERROR,
    STRUCTURAL_FAILED,
    COMPILE_FAILED,
    HARDWARE_FAILED,
    SIM_CHECKS_FAILED,
    TOOL_MISSING,
})
