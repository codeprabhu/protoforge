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


class DecisionRecord(BaseModel):
    """Structured record of an agent's reasoning and choices."""
    agent_role: str
    model: str
    task_id: str
    objective: str
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    conclusion: str = ""
    rejected_alternatives: List[str] = Field(default_factory=list)
    next_action: str = ""


class AgentResult(BaseModel):
    """Typed output returned by a specialist agent role."""
    task_id: str
    role: str
    objective: str
    requirements: List[str] = Field(default_factory=list)
    source_references: List[SourceReference] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    proposed_changes: List[CandidatePatch] = Field(default_factory=list)
    confidence: float = 1.0
    decision: str = ""
    next_action: str = ""
    decision_record: Optional[DecisionRecord] = None
    raw_output: str = ""


class RunEvent(BaseModel):
    """Durable event logged during execution."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    event_type: str  # run_started, agent_started, model_invoked, retrieval_completed, patch_created, review_finding, tool_started, tool_finished, stage_finished, verification_result, run_finished
    timestamp: float = Field(default_factory=time.time)
    agent_role: Optional[str] = None
    model: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


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
