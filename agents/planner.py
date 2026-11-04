"""Planning specialist agent."""

from __future__ import annotations

from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, Task


class PlanningAgent(BaseAgent):
    """Generates a scoped, traceable implementation and verification plan."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="planning_agent", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        req_ids = [r.req_id for r in task.requirements]
        mapped_sources = context.get("mapped_sources", [])

        steps = [
            {"step": 1, "action": "Generate minimal candidate patch addressing requirements", "reqs": req_ids},
            {"step": 2, "action": "Validate candidate structure, interface, and bus rules", "gate": "structural"},
            {"step": 3, "action": "Compile and run deterministic protocol or driver tests", "gate": "simulation"},
            {"step": 4, "action": "Judge outputs and submit for independent review", "gate": "review"},
        ]

        risks = [
            "Candidate might violate bus tri-state rules or leak kernel resources",
            "Regression tests might be missed if scope expands beyond target module",
        ]

        findings = [
            {"type": "plan_step", "steps": steps},
            {"type": "identified_risks", "risks": risks},
        ]

        dec = self.build_decision_record(
            task=task,
            objective=f"Plan minimal implementation for {task.task_id}",
            assumptions=["Scope is constrained to a single targeted patch"],
            evidence=[{"req_count": len(req_ids)}],
            findings=findings,
            conclusion="Plan is bounded and traceable to requirements.",
            rejected_alternatives=["Broad rewrite of multiple files simultaneously"],
            next_action="implementation",
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Formulate execution plan",
            requirements=req_ids,
            assumptions=["Scope is constrained to patch-sized changes"],
            findings=findings,
            confidence=0.95,
            decision="plan_approved",
            next_action="implementation",
            decision_record=dec,
            raw_output="Plan formulated with 4 bounded steps.",
        )
