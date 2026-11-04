"""Task state machine, lifecycle transitions, and execution graph."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from core.schemas import AgentResult


class TaskState(str, Enum):
    INITIALIZED = "initialized"
    SPEC_ANALYSIS = "spec_analysis"
    CODE_MAPPING = "code_mapping"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    VERIFICATION = "verification"
    REPAIR = "repair"
    COMPLETED = "completed"
    FAILED = "failed"


class StateTransition(BaseModel):
    from_state: str
    to_state: str
    agent_role: str
    timestamp: float = Field(default_factory=time.time)
    result: Optional[AgentResult] = None
    notes: str = ""


class TaskGraph(BaseModel):
    """Enforces valid role transitions, tracks execution timeline, and bounds retries."""
    task_id: str
    current_state: str = TaskState.INITIALIZED.value
    transitions: List[StateTransition] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3

    # Legal state transitions
    VALID_TRANSITIONS: Dict[str, List[str]] = {
        TaskState.INITIALIZED.value: [TaskState.SPEC_ANALYSIS.value, TaskState.FAILED.value],
        TaskState.SPEC_ANALYSIS.value: [TaskState.CODE_MAPPING.value, TaskState.FAILED.value],
        TaskState.CODE_MAPPING.value: [TaskState.PLANNING.value, TaskState.FAILED.value],
        TaskState.PLANNING.value: [TaskState.IMPLEMENTATION.value, TaskState.FAILED.value],
        TaskState.IMPLEMENTATION.value: [TaskState.REVIEW.value, TaskState.VERIFICATION.value, TaskState.FAILED.value],
        TaskState.REVIEW.value: [TaskState.VERIFICATION.value, TaskState.REPAIR.value, TaskState.FAILED.value],
        TaskState.VERIFICATION.value: [TaskState.COMPLETED.value, TaskState.REPAIR.value, TaskState.FAILED.value],
        TaskState.REPAIR.value: [TaskState.REVIEW.value, TaskState.VERIFICATION.value, TaskState.FAILED.value],
        TaskState.COMPLETED.value: [],
        TaskState.FAILED.value: [],
    }

    def can_transition_to(self, target_state: str) -> bool:
        """Check if transition to target_state is allowed from current state."""
        allowed = self.VALID_TRANSITIONS.get(self.current_state, [])
        return target_state in allowed

    def transition_to(
        self,
        target_state: str,
        agent_role: str,
        result: Optional[AgentResult] = None,
        notes: str = "",
    ) -> None:
        """Perform state transition and log it."""
        if not self.can_transition_to(target_state):
            raise ValueError(
                f"Illegal state transition from {self.current_state} to {target_state}. "
                f"Valid targets: {self.VALID_TRANSITIONS.get(self.current_state, [])}"
            )
        transition = StateTransition(
            from_state=self.current_state,
            to_state=target_state,
            agent_role=agent_role,
            result=result,
            notes=notes,
        )
        self.transitions.append(transition)
        self.current_state = target_state

    def increment_retry(self) -> bool:
        """Increment retry counter; return True if retry limit reached."""
        self.retry_count += 1
        return self.retry_count > self.max_retries

    @property
    def is_terminal(self) -> bool:
        return self.current_state in (TaskState.COMPLETED.value, TaskState.FAILED.value)
