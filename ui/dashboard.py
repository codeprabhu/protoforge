"""Observability views and terminal/report formatting for execution traces."""

from __future__ import annotations

from typing import Any, Dict

from core.trace_schema import RunTrace
from core.trace_serializer import export_timeline

_RULE = "=" * 80
_THIN_RULE = "-" * 80

# The live loop emits ``stage_finished`` for every gate verdict. Older traces
# may carry the multi-agent era ``verification_result`` name; both are accepted
# so stored traces stay renderable.
_VERDICT_EVENTS = ("stage_finished", "verification_result")


class TraceDashboard:
    """Provides formatted text and structured views for run analysis and audit."""

    @staticmethod
    def render_terminal_timeline(trace: RunTrace) -> str:
        """Render a human-readable ASCII timeline suitable for console inspection."""
        lines = [
            _RULE,
            f" PROTOFORGE RUN TIMELINE: {trace.run_id} ({trace.domain.upper()})",
            f" Status: {trace.status.upper()} | Task: {trace.task_id}",
            _RULE,
            f"{'TIME (s)':<10} | {'ROLE':<22} | {'EVENT / SUMMARY'}",
            _THIN_RULE,
        ]

        for item in export_timeline(trace):
            lines.append(
                f"{item['elapsed_sec']:<10.3f} | {item['agent_role']:<22} | {item['summary']}"
            )

        lines.append(_THIN_RULE)
        for idx, rec in enumerate(trace.decision_records, 1):
            if idx == 1:
                lines.append(f"\n--- INSPECTABLE DECISION RECORDS ({len(trace.decision_records)}) ---")
            lines.append(f"\n[{idx}] Agent: {rec.agent_role} | Model: {rec.model}")
            lines.append(f"    Objective: {rec.objective}")
            lines.append(
                "    Assumptions: "
                + (", ".join(rec.assumptions) if rec.assumptions else "None")
            )
            lines.append(f"    Conclusion: {rec.conclusion}")
            lines.append(f"    Next Action: {rec.next_action}")

        lines.append(_RULE)
        return "\n".join(lines)

    @staticmethod
    def render_verification_summary(trace: RunTrace) -> Dict[str, Any]:
        """Aggregate verification gate outcomes across attempts."""
        gates = []
        for ev in trace.events:
            if ev.event_type in _VERDICT_EVENTS:
                gates.append(
                    {
                        "stage": ev.data.get("stage"),
                        "passed": ev.data.get("passed"),
                        "failed_checks": ev.data.get("failed_checks", []),
                        "attempt": ev.data.get("attempt"),
                        "timestamp": ev.timestamp,
                    }
                )
        return {
            "run_id": trace.run_id,
            "total_verifications": len(gates),
            "final_status": trace.status,
            "gates": gates,
        }
