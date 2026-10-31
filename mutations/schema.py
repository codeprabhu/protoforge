"""Contracts and schemas for mutation testing, fault injection, and oracle scoring."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Mutation(BaseModel):
    """A formal definition of a seeded defect."""
    mutation_id: str
    domain: str
    title: str
    description: str
    fault_type: str  # missing_cleanup, wrong_offset, wrong_constant, missing_condition, ordering_bug, etc.
    target_pattern: str = ""
    replacement: str = ""
    expected_failed_stage: str  # structural, compile, hardware, simulation, judge, kunit
    expected_failed_check: str
    severity: str = "high"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MutantCandidate(BaseModel):
    """An injected mutant candidate."""
    mutant_id: str
    mutation: Mutation
    original_file: str
    mutated_content: str
    original_hash: str
    mutated_hash: str


class MutationResult(BaseModel):
    """Evaluation result of running a verification suite against a mutant."""
    mutant_id: str
    mutation_id: str
    detected: bool
    actual_failed_stage: Optional[str] = None
    actual_failed_checks: List[str] = Field(default_factory=list)
    diagnostic_output: str = ""
    oracle_matched_expected: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MutationCatalogue(BaseModel):
    """A versioned collection of mutations for a specific domain."""
    domain: str
    version: str
    mutations: List[Mutation] = Field(default_factory=list)
