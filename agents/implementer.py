"""Implementation specialist agent."""

from __future__ import annotations

import os
from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, CandidatePatch, Task
from domains.registry import get_domain


class ImplementationAgent(BaseAgent):
    """Produces patch-sized candidate implementations constrained to requirements."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="implementation_agent", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        domain = get_domain(task.domain)
        target_file = task.metadata.get("target_file", "protocols/i2c/generated/top_glue.v")

        # Delegate candidate generation to domain adapter
        candidate = domain.generate_candidate(
            task,
            context,
            out_path=target_file,
            model=self.model_name,
            temperature=context.get("temperature", 0.0),
            seed=context.get("seed"),
            generate_fn=context.get("generate_fn"),
        )
        task.candidate_patch = candidate

        req_ids = [r.req_id for r in task.requirements]
        findings = [
            {"type": "candidate_created", "file": target_file, "size_chars": len(candidate.full_content or "")}
        ]

        dec = self.build_decision_record(
            task=task,
            objective=f"Generate minimal implementation patch for {task.task_id}",
            assumptions=["Generated RTL/C candidate matches target interface"],
            evidence=[{"target_file": target_file}],
            findings=findings,
            conclusion="Candidate patch generated successfully.",
            rejected_alternatives=["Unconstrained code generation outside module template"],
            next_action="review",
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Generate candidate patch",
            requirements=req_ids,
            proposed_changes=[candidate],
            findings=findings,
            confidence=0.90,
            decision="candidate_ready",
            next_action="review",
            decision_record=dec,
            raw_output=f"Candidate generated at {target_file}",
        )
