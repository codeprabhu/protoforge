"""Repair specialist agent."""

from __future__ import annotations

from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, CandidatePatch, Task, ValidationResult
from domains.registry import get_domain


class RepairAgent(BaseAgent):
    """Generates targeted corrective patches conditioned on reviewer findings and tool diagnostics."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="repair_agent", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        domain = get_domain(task.domain)
        candidate = task.candidate_patch

        if not candidate:
            return AgentResult(
                task_id=task.task_id,
                role=self.role,
                objective="Repair broken candidate",
                confidence=0.0,
                decision="failed",
                next_action="abort",
                raw_output="No candidate patch found to repair.",
            )

        failed_results = context.get("failed_results", [])
        repaired_candidate = domain.repair_candidate(
            candidate,
            failed_results,
            model=self.model_name,
            temperature=context.get("temperature", 0.3),
            seed=context.get("seed"),
            repair_fn=context.get("repair_fn"),
        )
        task.candidate_patch = repaired_candidate

        findings = [
            {"type": "repair_applied", "target_file": repaired_candidate.target_file}
        ]

        dec = self.build_decision_record(
            task=task,
            objective=f"Apply targeted repair to {task.task_id}",
            assumptions=["Repair is conditioned strictly on named compiler/test failures"],
            evidence=[{"failure_count": len(failed_results)}],
            findings=findings,
            conclusion="Targeted repair generated and submitted for re-review.",
            rejected_alternatives=["Regenerating entire module from scratch"],
            next_action="review",
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Targeted repair",
            requirements=[r.req_id for r in task.requirements],
            proposed_changes=[repaired_candidate],
            findings=findings,
            confidence=0.90,
            decision="repaired",
            next_action="review",
            decision_record=dec,
            raw_output="Corrective repair applied.",
        )
