from mcp.server.fastmcp import FastMCP

from tools.compile_tool import compile_verilog
from tools.verilator_tool import lint_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from agent_core.agent import run_once
from core.event_store import EventStore
from core.trace_schema import RunTrace

mcp = FastMCP("ProtoForge")
_event_store = EventStore()


@mcp.tool()
def compile_rtl(filepaths):
    """Compile one or more Verilog files together using Icarus Verilog."""
    return compile_verilog(filepaths)


@mcp.tool()
def lint_rtl(filepath: str):
    """Lint a Verilog file using Verilator."""
    return lint_verilog(filepath)


@mcp.tool()
def simulate_and_judge(executable: str):
    """Run a compiled simulation and judge it against the I2C protocol checks."""
    sim = run_simulation(executable)
    result = parse_result_line(sim["stdout"])
    verdict = judge(result)
    if not verdict["pass"]:
        verdict["explanation"] = explain_failures(verdict["failed_checks"])
    return verdict


@mcp.tool()
def run_pipeline(model: str = None):
    """Run the full generate -> compile -> repair -> simulate -> judge pipeline once."""
    return run_once(model=model)


@mcp.tool()
def get_run(run_id: str):
    """Retrieve the full trace and verdict for a run by its run_id.

    Returns the timeline, gate verdicts, and attempt records.
    """
    from core.event_store import EventStore
    store = EventStore()
    trace = store.get_trace(run_id)
    if not trace:
        return {"error": f"Run {run_id} not found"}
    return {
        "run_id": trace.run_id,
        "task_id": trace.task_id,
        "domain": trace.domain,
        "status": trace.status,
        "start_time": trace.start_time,
        "end_time": trace.end_time,
        "timeline": [
            {
                "elapsed_sec": ev.timestamp - trace.start_time,
                "event_type": ev.event_type,
                "agent_role": ev.agent_role,
                "model": ev.model,
                "data": ev.data,
            }
            for ev in trace.events
        ],
        "verification_summary": _verification_summary(trace),
        "attempts": [
            {
                "attempt": rec.attempt,
                "outcome": rec.outcome,
                "stages": [
                    {"stage": s.stage, "passed": s.passed, "failed_checks": s.failed_checks}
                    for s in rec.stages
                ],
            }
            for rec in trace.decision_records
        ],
    }


def _verification_summary(trace: RunTrace) -> dict:
    gates = []
    for ev in trace.events:
        if ev.event_type in ("stage_finished", "verification_result"):
            gates.append({
                "stage": ev.data.get("stage"),
                "passed": ev.data.get("passed"),
                "failed_checks": ev.data.get("failed_checks", []),
                "attempt": ev.data.get("attempt"),
            })
    return {
        "run_id": trace.run_id,
        "total_verifications": len(gates),
        "final_status": trace.status,
        "gates": gates,
    }


if __name__ == "__main__":
    print("Starting ProtoForge MCP Server...")
    mcp.run()
