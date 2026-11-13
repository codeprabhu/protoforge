"""Structured explanation contract for generation and repair attempts.

The model is asked for a small JSON summary alongside its Verilog. The two
requirements are that the RTL stays independently extractable (the existing
deterministic wrapper and verification pipeline never depend on the
explanation) and that a malformed explanation never discards an otherwise
valid candidate.

This is *not* chain-of-thought. The summary reports a task interpretation,
the evidence the model used, and the approach it took. If the model does not
report something, the field stays ``not_reported``; the harness never invents
it.

The section is delimited rather than a separate JSON document so one model call
produces both artifacts and the explanation cannot drift from the RTL it
claims to describe.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# Delimiters are chosen to be stable under markdown stripping: the code fence
# extractor in ``agent.rtl_cleaner`` never treats these lines as a fence.
SUMMARY_OPEN = "=== PROTOFORGE EXPLANATION ==="
SUMMARY_CLOSE = "=== END PROTOFORGE EXPLANATION ==="

_SECTION_RE = re.compile(
    re.escape(SUMMARY_OPEN) + r"(?P<body>[\s\S]*?)" + re.escape(SUMMARY_CLOSE),
    re.IGNORECASE,
)
# A fence-wrapped section (```json ... ```) is accepted as well as raw JSON.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(?P<body>[\s\S]*?)\s*```\s*$", re.IGNORECASE)


class GenerationSummary(BaseModel):
    """What the model says it is doing before producing RTL."""

    interpretation: str = "not_reported"
    protocol_requirements: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    fsm_structure: str = "not_reported"
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    alternatives_considered: List[str] = Field(default_factory=lambda: ["not_reported"])
    confidence: Optional[float] = None

    @field_validator("alternatives_considered", mode="before")
    @classmethod
    def _coerce_alternatives(cls, v):
        if isinstance(v, str):
            return [v]
        return v


class RepairSummary(BaseModel):
    """Diagnosis and repair plan for a failing candidate."""

    failure_evidence: List[str] = Field(default_factory=list)
    root_cause: str = "not_reported"
    minimal_change: str = "not_reported"
    must_not_change: List[str] = Field(default_factory=list)
    expected_verification: str = "not_reported"
    confidence: Optional[float] = None

    @field_validator("failure_evidence", "must_not_change", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v


EXPLANATION_INSTRUCTION = f"""
After the {SUMMARY_OPEN} line (nothing else after it), emit one JSON object
with exactly these keys. Use an empty list or the string "not_reported" when you
have nothing to report -- do not invent alternatives or confidence scores.

  "interpretation":  what this task requires, in one sentence
  "protocol_requirements": the requirements you are satisfying
  "constraints": the constraints you are following
  "fsm_structure": the states and phases you will implement
  "assumptions": assumptions or ambiguities you are resolving
  "risks": known risks in your design
  "alternatives_considered": other approaches you considered, or "not_reported"
  "confidence": your confidence from 0.0 to 1.0, or null if you cannot estimate

Example:
{SUMMARY_OPEN}
{{"interpretation": "Write two data bytes to 0x50 with an ACK after each.", "protocol_requirements": ["I2C-START", "I2C-STOP"], "constraints": ["open-drain sda/scl"], "fsm_structure": "IDLE -> START -> BIT_LOW -> ... -> DONE", "assumptions": [], "risks": [], "alternatives_considered": "not_reported", "confidence": null}}
{SUMMARY_CLOSE}
"""

REPAIR_EXPLANATION_INSTRUCTION = f"""
After the {SUMMARY_OPEN} line (nothing else after it), emit one JSON object
with exactly these keys. Use an empty list or the string "not_reported" when you
have nothing to report.

  "failure_evidence": the exact checks or diagnostics you are addressing
  "root_cause": why you think they failed
  "minimal_change": the smallest change that fixes them
  "must_not_change": phases or logic that must be preserved
  "expected_verification": what the next run should show if the repair worked
  "confidence": your confidence from 0.0 to 1.0, or null if you cannot estimate

Example:
{SUMMARY_OPEN}
{{"failure_evidence": ["stop"], "root_cause": "STOP_LOW releases SCL before SDA", "minimal_change": "hold SDA low until SCL is high", "must_not_change": ["address byte"], "expected_verification": "saw_stop becomes 1", "confidence": null}}
{SUMMARY_CLOSE}
"""


def _strip_fence(body: str) -> str:
    match = _FENCE_RE.match(body)
    return match.group("body").strip() if match else body.strip()


def split_response(raw_output: str) -> Dict[str, Any]:
    """Split a model response into its RTL part and its explanation part.

    The RTL portion is everything outside the explanation section, so the
    existing ``clean_rtl`` fence extraction keeps working unchanged. When no
    section is present the whole response is the RTL part and the explanation is
    absent -- which is a *normal*, pre-existing case, not a warning.
    """
    rtl_part = raw_output
    section: Optional[str] = None
    match = _SECTION_RE.search(raw_output)
    if match:
        section = _strip_fence(match.group("body"))
        rtl_part = raw_output[: match.start()] + raw_output[match.end() :]
    return {"rtl_part": rtl_part, "explanation_raw": section}


def parse_explanation(
    raw_output: str, *, repair: bool = False
) -> Dict[str, Any]:
    """Parse the explanation section without ever discarding the RTL.

    Returns ``{"summary", "raw", "warnings", "present"}``. ``summary`` is
    ``None`` when the section is absent or unparseable; the caller keeps the RTL
    either way. A malformed section always produces a warning and never a
    fabricated summary.
    """
    parts = split_response(raw_output)
    raw = parts["explanation_raw"]
    if raw is None:
        return {"summary": None, "raw": None, "warnings": [], "present": False}

    warnings: List[str] = []
    data: Optional[Dict[str, Any]] = None
    parse_error: Optional[str] = None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        parse_error = f"explanation is not valid JSON: {exc}"
        parsed = None
    if isinstance(parsed, dict):
        data = parsed
    elif parse_error is None:
        parse_error = f"explanation parsed as {type(parsed).__name__}, expected an object"

    model = RepairSummary if repair else GenerationSummary
    summary: Optional[BaseModel] = None
    if data is not None:
        try:
            summary = model.model_validate(data)
        except Exception as exc:  # validation keeps the raw payload for audit
            parse_error = f"explanation failed schema validation: {exc}"

    if parse_error:
        warnings.append(parse_error)
    if summary is None:
        # The section exists but is unusable: record that explicitly so an
        # absent explanation and a malformed one are never confused.
        return {
            "summary": None,
            "raw": raw,
            "warnings": warnings or ["explanation section present but unusable"],
            "present": True,
        }

    model_fields = set(model.model_fields)
    unexpected = set(data) - model_fields
    if unexpected:
        warnings.append("explanation carried unknown keys: " + ", ".join(sorted(unexpected)))

    return {
        "summary": summary, "raw": raw, "warnings": warnings, "present": True
    }


def summary_payload(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Serialise a parsed summary, or state plainly that it is absent."""
    summary = parsed.get("summary")
    if summary is None:
        return {"status": "not_reported" if not parsed.get("present") else "unparsed",
                "warnings": list(parsed.get("warnings", []))}
    payload = summary.model_dump()
    payload["status"] = "reported"
    payload["warnings"] = list(parsed.get("warnings", []))
    return payload
