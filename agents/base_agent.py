"""Base specialist agent class and shared execution contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from core.schemas import AgentResult, CandidatePatch, DecisionRecord, SourceReference, Task


class BaseAgent(ABC):
    """Abstract base class for all specialist agents."""

    def __init__(self, role: str, model_name: str = "default"):
        self.role = role
        self.model_name = model_name

    @abstractmethod
    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        """Execute the agent's specialist role on the task."""
        raise NotImplementedError

    def build_decision_record(
        self,
        task: Task,
        objective: str,
        assumptions: List[str],
        evidence: List[Dict[str, Any]],
        findings: List[Dict[str, Any]],
        conclusion: str,
        rejected_alternatives: List[str],
        next_action: str,
    ) -> DecisionRecord:
        """Create a durable, auditable decision record for this invocation."""
        return DecisionRecord(
            agent_role=self.role,
            model=self.model_name,
            task_id=task.task_id,
            objective=objective,
            assumptions=assumptions,
            evidence=evidence,
            findings=findings,
            conclusion=conclusion,
            rejected_alternatives=rejected_alternatives,
            next_action=next_action,
        )
