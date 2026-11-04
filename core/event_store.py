"""Durable event and trace store with inspection and replay capabilities."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from core.trace_schema import RunTrace
from core.trace_serializer import trace_to_json, trace_from_json, export_timeline


class EventStore:
    """Persists and queries durable execution traces and decision records."""

    DEFAULT_STORE_DIR = "results/traces"

    def __init__(self, store_dir: str = DEFAULT_STORE_DIR):
        self.store_dir = os.path.abspath(store_dir)
        os.makedirs(self.store_dir, exist_ok=True)

    def save_trace(self, trace: RunTrace) -> str:
        """Persist a RunTrace to a JSON file."""
        file_path = os.path.join(self.store_dir, f"{trace.run_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(trace_to_json(trace, indent=2))
        return file_path

    def get_trace(self, run_id: str) -> Optional[RunTrace]:
        """Load a RunTrace by run_id."""
        file_path = os.path.join(self.store_dir, f"{run_id}.json")
        if not os.path.exists(file_path):
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return trace_from_json(f.read())

    def list_traces(self) -> List[Dict[str, Any]]:
        """List metadata for all persisted run traces."""
        traces = []
        if not os.path.exists(self.store_dir):
            return traces

        for f_name in sorted(os.listdir(self.store_dir)):
            if f_name.endswith(".json"):
                full_p = os.path.join(self.store_dir, f_name)
                try:
                    with open(full_p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    traces.append({
                        "run_id": data.get("run_id"),
                        "task_id": data.get("task_id"),
                        "domain": data.get("domain"),
                        "status": data.get("status"),
                        "start_time": data.get("start_time"),
                        "events_count": len(data.get("events", [])),
                        "decision_records_count": len(data.get("decision_records", [])),
                        "file_path": full_p,
                    })
                except Exception:
                    continue
        return traces

    def replay_trace(self, run_id: str) -> Dict[str, Any]:
        """Reconstruct the entire run history, timeline, and agent decisions."""
        trace = self.get_trace(run_id)
        if not trace:
            raise KeyError(f"Run trace '{run_id}' not found in {self.store_dir}")

        timeline = export_timeline(trace)
        return {
            "run_id": trace.run_id,
            "task_id": trace.task_id,
            "domain": trace.domain,
            "status": trace.status,
            "start_time": trace.start_time,
            "end_time": trace.end_time,
            "duration_sec": round(trace.end_time - trace.start_time, 3) if trace.end_time else None,
            "timeline": timeline,
            "decision_records": [d.model_dump() for d in trace.decision_records],
            "summary": trace.summary,
        }
