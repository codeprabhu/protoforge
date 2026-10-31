"""Serialization, deserialization, and timeline formatting for execution traces."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from core.trace_schema import RunTrace, TraceEvent


def trace_to_json(trace: RunTrace, indent: int = 2) -> str:
    """Serialize a RunTrace into a JSON string."""
    return trace.model_dump_json(indent=indent)


def trace_from_json(json_str: str) -> RunTrace:
    """Deserialize a JSON string into a RunTrace object."""
    data = json.loads(json_str)
    return RunTrace.model_validate(data)


def export_timeline(trace: RunTrace) -> List[Dict[str, Any]]:
    """Generate a clean chronologically ordered timeline suitable for audit and dashboard display."""
    timeline = []
    base_time = trace.start_time

    for ev in trace.events:
        elapsed = round(ev.timestamp - base_time, 3)
        item = {
            "elapsed_sec": elapsed,
            "event_type": ev.event_type,
            "agent_role": ev.agent_role or "system",
            "model": ev.model or "n/a",
            "summary": _format_event_summary(ev),
            "details": ev.data,
        }
        timeline.append(item)

    return timeline


def _format_event_summary(ev: TraceEvent) -> str:
    et = ev.event_type
    data = ev.data
    if et == "run_started":
        return f"Run started for task {data.get('task_id', '')} in domain {data.get('domain', '')}"
    elif et == "agent_started":
        return f"Agent {ev.agent_role} invoked with objective: {data.get('objective', '')}"
    elif et == "model_invoked":
        return f"Model {ev.model} invoked ({data.get('tokens', 0)} tokens, {data.get('latency_sec', 0)}s)"
    elif et == "retrieval_completed":
        return f"Retrieved {data.get('chunk_count', 0)} evidence chunks with score threshold {data.get('score_threshold', 0)}"
    elif et == "patch_created":
        return f"Patch created targeting {data.get('file', '')} ({data.get('size_bytes', 0)} bytes)"
    elif et == "review_finding":
        return f"Review finding [{data.get('severity', 'info')}]: {data.get('check', '')} ({data.get('action', '')})"
    elif et == "tool_started":
        return f"Tool execution started: {data.get('tool_name', '')}"
    elif et == "tool_finished":
        return f"Tool execution finished: {data.get('tool_name', '')} (exit code {data.get('exit_code', 0)})"
    elif et == "verification_result":
        status = "PASSED" if data.get("passed") else "FAILED"
        return f"Verification gate {data.get('stage', '')}: {status} ({data.get('failed_checks', [])})"
    elif et == "run_finished":
        return f"Run completed with verdict: {data.get('final_verdict', '')} in {data.get('elapsed_sec', 0)}s"
    return f"Event {et}"
