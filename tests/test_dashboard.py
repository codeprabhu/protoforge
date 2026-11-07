"""Trace rendering tests.

``ui/dashboard`` previously aggregated ``verification_result`` events, a name
the live loop never emits, so ``render_verification_summary`` silently returned
zero gates for every real run. These tests pin it to the events the
orchestrator actually produces.
"""

from core.trace_schema import EventType, RunTrace
from ui.dashboard import TraceDashboard


def _trace_with_gates() -> RunTrace:
    trace = RunTrace(run_id="run-1", task_id="i2c-run-1", domain="i2c")
    trace.add_event(EventType.RUN_STARTED, model="qwen2.5-coder:7b", data={"config": {}})
    for attempt, (stage, passed) in enumerate(
        [("structural", True), ("compile", True), ("judge", False)]
    ):
        trace.add_event(
            EventType.STAGE_FINISHED,
            model="qwen2.5-coder:7b",
            data={
                "stage": stage,
                "passed": passed,
                "attempt": attempt,
                "failed_checks": [] if passed else ["stop"],
            },
        )
    trace.add_event(EventType.RUN_FINISHED, model="qwen2.5-coder:7b", data={"final": "sim_checks_failed"})
    trace.status = "failed"
    return trace


def test_verification_summary_sees_the_events_the_orchestrator_emits():
    summary = TraceDashboard.render_verification_summary(_trace_with_gates())

    assert summary["run_id"] == "run-1"
    assert summary["final_status"] == "failed"
    assert summary["total_verifications"] == 3
    assert [g["stage"] for g in summary["gates"]] == ["structural", "compile", "judge"]
    assert summary["gates"][-1]["failed_checks"] == ["stop"]
    assert summary["gates"][0]["attempt"] == 0


def test_verification_summary_accepts_legacy_verification_result_events():
    trace = RunTrace(run_id="old", task_id="t", domain="i2c")
    trace.add_event("verification_result", data={"stage": "judge", "passed": False})
    summary = TraceDashboard.render_verification_summary(trace)
    assert summary["total_verifications"] == 1
    assert summary["gates"][0]["stage"] == "judge"


def test_terminal_timeline_renders_every_event_with_a_timestamp():
    text = TraceDashboard.render_terminal_timeline(_trace_with_gates())

    assert "PROTOFORGE RUN TIMELINE: run-1 (I2C)" in text
    assert "Status: FAILED" in text
    assert "Run started for task" in text
    assert text.count("Event stage_finished") == 3
    assert "Run completed with verdict" in text

    # Every timeline row is "<elapsed> | <role> | <summary>".
    rows = [
        ln for ln in text.splitlines()
        if " | " in ln and ln[:1].isdigit()
    ]
    assert len(rows) == 5  # run_started + 3 stage_finished + run_finished
    for row in rows:
        elapsed = row.split("|", 1)[0].strip()
        float(elapsed)  # must parse as seconds
