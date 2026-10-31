"""Specification Analyst specialist agent."""

from __future__ import annotations

import os
from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, Requirement, SourceReference, Task
from domains.registry import get_domain


class SpecificationAnalyst(BaseAgent):
    """Parses specifications, datasheets, and task goals into formal requirements with provenance."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="specification_analyst", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        domain_name = task.domain
        domain = get_domain(domain_name)

        spec_path = context.get("spec_path") or task.metadata.get("spec_path")
        extracted_reqs: List[Requirement] = []
        if spec_path and os.path.exists(spec_path):
            extracted_reqs = domain.parse_requirements(spec_path)
        elif task.requirements:
            extracted_reqs = task.requirements
        else:
            extracted_reqs = domain.parse_requirements(None)

        task.requirements = extracted_reqs

        req_ids = [r.req_id for r in extracted_reqs]
        evidence = [
            {"req_id": r.req_id, "title": r.title, "severity": r.severity}
            for r in extracted_reqs
        ]
        assumptions = [
            f"Target domain is '{domain_name}'",
            "Specification constraints are canonical and authoritative",
        ]
        findings = [
            {"type": "extracted_requirement", "id": r.req_id, "description": r.description}
            for r in extracted_reqs
        ]

        dec = self.build_decision_record(
            task=task,
            objective=f"Extract and normalize requirements for {task.task_id}",
            assumptions=assumptions,
            evidence=evidence,
            findings=findings,
            conclusion=f"Extracted {len(extracted_reqs)} formal requirements with citations.",
            rejected_alternatives=["Inferring undocumented register or timing values"],
            next_action="codebase_mapping",
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Extract specification requirements",
            requirements=req_ids,
            assumptions=assumptions,
            findings=findings,
            confidence=1.0,
            decision="requirements_confirmed",
            next_action="codebase_mapping",
            decision_record=dec,
            raw_output=f"Extracted {len(extracted_reqs)} requirements.",
        )
