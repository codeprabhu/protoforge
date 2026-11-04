"""Structured schemas for execution tracing and audit timelines."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from core.schemas import DecisionRecord


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    AGENT_STARTED = "agent_started"
    MODEL_INVOKED = "model_invoked"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    PATCH_CREATED = "patch_created"
    REVIEW_FINDING = "review_finding"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    STAGE_FINISHED = "stage_finished"
    VERIFICATION_RESULT = "verification_result"
    RUN_FINISHED = "run_finished"


class TraceEvent(BaseModel):
    """An atomic timestamped event in an execution run."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    timestamp: float = Field(default_factory=time.time)
    event_type: str  # from EventType
    agent_role: Optional[str] = None
    model: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class RunTrace(BaseModel):
    """Complete, durable trace of an execution run."""
    run_id: str
    task_id: str
    domain: str
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "running"  # running, completed, failed
    events: List[TraceEvent] = Field(default_factory=list)
    decision_records: List[DecisionRecord] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)

    def add_event(
        self,
        event_type: str | EventType,
        agent_role: Optional[str] = None,
        model: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> TraceEvent:
        """Append a trace event to this run."""
        ev_type = event_type.value if isinstance(event_type, EventType) else str(event_type)
        ev = TraceEvent(
            run_id=self.run_id,
            event_type=ev_type,
            agent_role=agent_role,
            model=model,
            data=data or {},
        )
        self.events.append(ev)
        return ev
