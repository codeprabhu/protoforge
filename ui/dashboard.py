"""Observability views and terminal/report formatting for execution traces."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from core.trace_schema import RunTrace
from core.trace_serializer import export_timeline


class TraceDashboard:
    """Provides formatted text and structured views for run analysis and agent audit."""

    @staticmethod
    def render_terminal_timeline(trace: RunTrace) -> str:
        """Render a human-readable ASCII timeline suitable for console inspection."""
        lines = [
            f"================================================================================",
            f" PROTOFORGE RUN TIMELINE: {trace.run_id} ({trace.domain.upper()})",
            f" Status: {trace.status.upper()} | Task: {trace.task_id}",
            f"================================================================================",
            f"{'TIME (s)':<10} | {'ROLE':<22} | {'EVENT / SUMMARY'}",
            f"--------------------------------------------------------------------------------",
        ]

        timeline = export_timeline(trace)
        for item in timeline:
            lines.append(f"{item['elapsed_sec']:<10.3f} | {item['agent_role']:<22} | {item['summary']}")

        lines.append(f"--------------------------------------------------------------------------------")
        if trace.decision_records:
            lines.append(f"\n--- INSPECTABLE DECISION RECORDS ({len(trace.decision_records)}) ---")
            for idx, rec in enumerate(trace.decision_records, 1):
                lines.append(f"\n[{idx}] Agent: {rec.agent_role} | Model: {rec.model}")
                lines.append(f"    Objective: {rec.objective}")
                lines.append(f"    Assumptions: {', '.join(rec.assumptions) if rec.assumptions else 'None'}")
                lines.append(f"    Conclusion: {rec.conclusion}")
                lines.append(f"    Next Action: {rec.next_action}")

        lines.append(f"================================================================================")
        return "\n".join(lines)

    @staticmethod
    def render_verification_summary(trace: RunTrace) -> Dict[str, Any]:
        """Aggregate verification gate outcomes across attempts."""
        gates = []
        for ev in trace.events:
            if ev.event_type == "verification_result":
                gates.append({
                    "stage": ev.data.get("stage"),
                    "passed": ev.data.get("passed"),
                    "failed_checks": ev.data.get("failed_checks", []),
                    "timestamp": ev.timestamp,
                })
        return {
            "run_id": trace.run_id,
            "total_verifications": len(gates),
            "final_status": trace.status,
            "gates": gates,
        }
