"""Reviewer specialist agent."""

from __future__ import annotations

import re
from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, CandidatePatch, Task
from domains.registry import get_domain


class ReviewerAgent(BaseAgent):
    """Independently evaluates patches against original specifications and independent checks."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="reviewer_agent", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        domain = get_domain(task.domain)
        candidate = task.candidate_patch

        if not candidate or not candidate.full_content:
            return AgentResult(
                task_id=task.task_id,
                role=self.role,
                objective="Review candidate patch",
                confidence=0.0,
                decision="rejected",
                next_action="repair",
                raw_output="No candidate patch available for review.",
            )

        code = candidate.full_content
        findings: List[Dict[str, Any]] = []
        blocking = False

        # 1. Structural domain verification
        struct_res = domain.validate_structure(candidate)
        if not struct_res.passed:
            blocking = True
            for chk in struct_res.failed_checks:
                findings.append({
                    "severity": "blocker",
                    "check": chk,
                    "requirement": "I2C-OPEN-DRAIN" if "drain" in chk else "STRUCTURAL-001",
                    "evidence": struct_res.diagnostics,
                    "action": "Correct structural/open-drain drive syntax",
                })

        # 2. Check for common anti-patterns (e.g. non-synthesizable delays)
        if "#" in code and re.search(r"#\s*\d+", code):
            blocking = True
            findings.append({
                "severity": "blocker",
                "check": "no_delays",
                "requirement": "RTL-SYNTHESIS",
                "evidence": "Found procedural delay statement (#) in clocked block",
                "action": "Remove procedural delays and use synchronous state machine transitions",
            })

        decision = "rejected" if blocking else "approved"
        next_action = "repair" if blocking else "verification"

        dec = self.build_decision_record(
            task=task,
            objective=f"Adversarially review candidate patch for {task.task_id}",
            assumptions=["Review is performed against authoritative specifications, not author claims"],
            evidence=[{"candidate_chars": len(code)}],
            findings=findings,
            conclusion="Candidate rejected due to structural or contract violations." if blocking else "Candidate approved for verification.",
            rejected_alternatives=["Rubber-stamping without running structural gates"],
            next_action=next_action,
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Adversarial patch review",
            requirements=[r.req_id for r in task.requirements],
            findings=findings,
            confidence=0.95 if blocking else 0.85,
            decision=decision,
            next_action=next_action,
            decision_record=dec,
            raw_output=f"Review completed with verdict: {decision}",
        )
