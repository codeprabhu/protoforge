"""Domain-neutral schema definitions and shared contracts for ProtoForge."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """Reference to a specific file, symbol, or line range in a codebase or document."""
    file_path: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    symbol: Optional[str] = None
    version_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Requirement(BaseModel):
    """A single requirement or specification clause with provenance."""
    req_id: str
    domain: str
    title: str
    description: str
    source_reference: Optional[SourceReference] = None
    severity: str = "must"  # "must", "should", "may"
    status: str = "active"


class CandidatePatch(BaseModel):
    """A generated code patch or full source candidate."""
    patch_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    target_file: str
    diff_content: str = ""
    full_content: Optional[str] = None
    requirements_addressed: List[str] = Field(default_factory=list)
    author_role: str = "implementer"
    patch_hash: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ValidationResult(BaseModel):
    """Structured result of a verification or validation step."""
    stage: str  # "structural", "compile", "hardware", "simulation", "kunit", etc.
    passed: bool
    tool_missing: bool = False
    failed_checks: List[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    diagnostics: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


# A model is not required to invent alternatives or confidence scores, so every
# optional field defaults to the NOT_REPORTED marker. ``not_reported`` is
# deliberately distinct from an empty list (a genuine "I considered nothing")
# and from a negative finding (a genuine "this alternative does not work").
NOT_REPORTED = "not_reported"


def _not_reported_str() -> str:
    return NOT_REPORTED


def _not_reported_list() -> List[str]:
    return [NOT_REPORTED]


class EvidenceItem(BaseModel):
    """A pointer to a concrete artifact that supports a claim in a decision."""

    kind: str  # "requirement" | "retrieval" | "previous_attempt" | "tool_result" | "protocol_constraint"
    ref: str  # stable identifier: req_id, retrieval label, "attempt_<n>", "stage:<name>"
    detail: str = ""
    origin: str = ""  # "harness" | "model_reported"
    sha256: str = ""


class DecisionRecord(BaseModel):
    """Structured, evidence-grounded record of an agent's reasoning and choices.

    The record is *not* chain-of-thought. It captures the task interpretation,
    the evidence the agent was actually shown, the approach it selected and what
    it expected the effect to be. Every ``evidence`` entry points at a real
    artifact, so a finding can be followed back to its source.
    """

    decision_id: str = Field(default_factory=lambda: f"D-{uuid.uuid4().hex[:12]}")
    run_id: str = ""
    attempt: int = 0
    agent_role: str = "rtl_implementer"  # "rtl_implementer" | "failure_analyst"
    model: str = ""
    objective: str = ""
    input_summary: str = ""
    evidence: List[EvidenceItem] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    alternatives_considered: List[str] = Field(default_factory=lambda: [NOT_REPORTED])
    selected_approach: str = NOT_REPORTED
    rejected_alternatives: List[str] = Field(default_factory=lambda: [NOT_REPORTED])
    expected_effect: str = NOT_REPORTED
    # Optional and explicitly model-reported. Not calibrated; never present this
    # as an objective probability.
    confidence: Optional[float] = None
    confidence_basis: str = NOT_REPORTED
    next_action: str = ""
    output_artifact_refs: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    # Retained fields from the original record so older consumers still work.
    task_id: str = ""
    conclusion: str = ""


class Task(BaseModel):
    """The canonical task contract shared across orchestrator and agents."""
    task_id: str
    domain: str
    description: str
    requirements: List[Requirement] = Field(default_factory=list)
    source_chunks: List[str] = Field(default_factory=list)
    tests: List[str] = Field(default_factory=list)
    candidate_patch: Optional[CandidatePatch] = None
    status: str = "pending"  # pending, analyzing, mapping, planning, implementing, reviewing, verifying, repairing, completed, failed
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
