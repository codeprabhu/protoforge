"""Observability views and terminal/report formatting for execution traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from core.event_store import EventStore
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
    def load_run(run_id: str, runs_root: str = "results/runs") -> RunTrace | None:
        """Load a RunTrace from the run directory."""
        run_dir = Path(runs_root) / run_id
        trace_file = run_dir / "trace.json"
        if not trace_file.is_file():
            # Fallback: try to reconstruct from event_store
            store = EventStore()
            return store.get_trace(run_id)
        try:
            content = trace_file.read_text(encoding="utf-8")
            from core.trace_serializer import trace_from_json
            return trace_from_json(content)
        except (json.JSONDecodeError, OSError):
            return None

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
    def render_full_report(trace: RunTrace) -> str:
        """Generate a comprehensive text report for a run."""
        lines = [TraceDashboard.render_terminal_timeline(trace)]
        lines.append("\n=== VERIFICATION SUMMARY ===")
        summary = TraceDashboard.render_verification_summary(trace)
        lines.append(f"Total gates: {summary['total_verifications']}")
        lines.append(f"Final status: {summary['final_status']}")
        for g in summary["gates"]:
            status = "PASS" if g["passed"] else "FAIL"
            lines.append(f"  [{g['attempt']}] {g['stage']}: {status} {g['failed_checks'] or '—'}")

        for i in range(len(trace.decision_records)):
            details = TraceDashboard.render_attempt_details(trace, i)
            lines.append(f"\n=== ATTEMPT {i} DETAILS ===")
            if "error" in details:
                lines.append(f"  {details['error']}")
                continue
            if details["prompt"]:
                lines.append(f"Prompt (first 500 chars):\n  {details['prompt'][:500]}...")
            if details["retrieved_example"]["enabled"]:
                lines.append(f"Retrieved example: {details['retrieved_example']['example'][:200]}...")
            if details["raw_output"]:
                lines.append(f"Raw output (first 500 chars):\n  {details['raw_output'][:500]}...")
            for v in details["verdicts"]:
                status = "PASS" if v["passed"] else "FAIL"
                lines.append(f"  {v['stage']}: {status} {v['failed_checks'] or '—'}")

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

    @staticmethod
    def render_attempt_details(trace: RunTrace, attempt_idx: int) -> Dict[str, Any]:
        """Extract prompt, retrieved example, raw output, and verdicts for an attempt."""
        return {"error": "Not yet implemented: requires attempt record lookup"}
