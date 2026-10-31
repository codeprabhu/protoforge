"""Base domain interface defining domain-specific operations for ProtoForge."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from core.schemas import CandidatePatch, Requirement, SourceReference, Task, ValidationResult


class BaseDomainAdapter(ABC):
    """Abstract base class that all protocol and hardware/software domain adapters implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier of the domain (e.g. 'i2c', 'pcie_linux', 'spi', 'uart')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this domain handles."""
        raise NotImplementedError

    @abstractmethod
    def parse_requirements(self, spec_input: Any) -> List[Requirement]:
        """Parse domain-specific specification, register map or datasheet into canonical Requirements."""
        raise NotImplementedError

    @abstractmethod
    def index_sources(self, source_paths: List[str]) -> List[SourceReference]:
        """Index symbols, files, module headers, or AST elements from target source files."""
        raise NotImplementedError

    @abstractmethod
    def build_context(self, task: Task) -> Dict[str, Any]:
        """Assemble domain-specific context bundle (specifications, templates, device contracts)."""
        raise NotImplementedError

    @abstractmethod
    def generate_candidate(self, task: Task, context: Dict[str, Any], **kwargs) -> CandidatePatch:
        """Generate an initial candidate implementation or patch for the task."""
        raise NotImplementedError

    @abstractmethod
    def validate_structure(self, candidate: CandidatePatch) -> ValidationResult:
        """Perform cheap structural, syntax, and interface compliance checks on candidate."""
        raise NotImplementedError

    @abstractmethod
    def build_and_test(self, candidate: CandidatePatch, **kwargs) -> List[ValidationResult]:
        """Compile, lint, synthesize or run unit/simulation tests on candidate."""
        raise NotImplementedError

    @abstractmethod
    def judge(self, validation_results: List[ValidationResult], **kwargs) -> ValidationResult:
        """Evaluate verification results against protocol/domain oracles to produce final pass/fail."""
        raise NotImplementedError

    @abstractmethod
    def repair_candidate(
        self,
        candidate: CandidatePatch,
        failed_results: List[ValidationResult],
        **kwargs
    ) -> CandidatePatch:
        """Apply targeted domain-specific repair to candidate using failure diagnostics."""
        raise NotImplementedError

    @abstractmethod
    def get_mutations(self) -> List[Dict[str, Any]]:
        """Return the versioned catalogue of known defect mutations for this domain."""
        raise NotImplementedError
