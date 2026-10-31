"""Tests for Phase 4 multi-agent specialist roles and orchestration."""

import pytest
from core.schemas import CandidatePatch, Requirement, Task, ValidationResult
from core.task_graph import TaskGraph, TaskState
from core.orchestrator import Orchestrator
from domains.registry import get_domain
from agents.spec_analyst import SpecificationAnalyst
from agents.codebase_mapper import CodebaseMapper
from agents.planner import PlanningAgent
from agents.implementer import ImplementationAgent
from agents.reviewer import ReviewerAgent
from agents.repair import RepairAgent


def test_task_graph_transitions_and_retry_limits():
    graph = TaskGraph(task_id="TASK-TG-001", max_retries=2)
    assert graph.current_state == TaskState.INITIALIZED.value

    # Legal transition
    graph.transition_to(TaskState.SPEC_ANALYSIS.value, "orchestrator")
    assert graph.current_state == TaskState.SPEC_ANALYSIS.value

    # Illegal transition
    with pytest.raises(ValueError):
        graph.transition_to(TaskState.COMPLETED.value, "orchestrator")

    # Retry counter
    assert graph.increment_retry() is False  # count=1
    assert graph.increment_retry() is False  # count=2
    assert graph.increment_retry() is True   # count=3 (exceeded max_retries=2)


def test_specialist_agent_roles(tmp_path):
    task = Task(
        task_id="TASK-AGENT-TEST",
        domain="i2c",
        description="Verify multi-agent roles",
        metadata={"spec_path": "protocols/i2c/spec.md"},
    )
    context = {"spec_path": "protocols/i2c/spec.md"}

    # 1. Spec Analyst
    analyst = SpecificationAnalyst()
    res_analyst = analyst.execute(task, context)
    assert res_analyst.role == "specification_analyst"
    assert len(res_analyst.requirements) >= 4
    assert res_analyst.decision_record is not None
    assert res_analyst.decision_record.conclusion != ""

    # 2. Codebase Mapper
    mapper = CodebaseMapper()
    res_mapper = mapper.execute(task, context)
    assert res_mapper.role == "codebase_mapper"
    assert len(res_mapper.source_references) >= 1

    # 3. Planner
    planner = PlanningAgent()
    res_planner = planner.execute(task, context)
    assert res_planner.role == "planning_agent"
    assert len(res_planner.findings) == 2

    # 4. Reviewer catching procedural delay defect
    broken_code = """
    module top_glue (input clk, input rst_n, inout sda, inout scl, input tb_start, output tb_done);
        always @(posedge clk) begin
            #10 sda <= 1'b0;
        end
    endmodule
    """
    task.candidate_patch = CandidatePatch(target_file="test.v", full_content=broken_code)
    reviewer = ReviewerAgent()
    res_review = reviewer.execute(task, context)
    assert res_review.decision == "rejected"
    assert any(f.get("severity") == "blocker" for f in res_review.findings)


def test_orchestrator_multi_agent_workflow(tmp_path):
    adapter = get_domain("i2c")
    orchestrator = Orchestrator(adapter)

    good_rtl = open("protocols/i2c/reference/good_master_reset_safe.v").read()
    broken_rtl = open("protocols/i2c/reference/broken_master_no_stop_reset_safe.v").read()
    target_top = tmp_path / "top_glue.v"

    task = Task(
        task_id="TASK-MA-WORKFLOW",
        domain="i2c",
        description="Multi-agent repair workflow test",
        metadata={
            "target_file": str(target_top),
            "sim_out": str(tmp_path / "sim.out"),
            "spec_path": "protocols/i2c/spec.md",
        },
    )

    # Fake generator returns broken on attempt 1, fake repair returns good
    def fake_generate(devices, spec_path, out_path, model=None, temperature=0.0, seed=None):
        with open(out_path, "w") as f:
            f.write(broken_rtl)

    def fake_repair(code, summary, detail, out_path, model=None, temperature=0.3, seed=None, **kwargs):
        with open(out_path, "w") as f:
            f.write(good_rtl)

    sim_call_count = {"n": 0}

    def fake_sim(*args, **kwargs):
        sim_call_count["n"] += 1
        if sim_call_count["n"] == 1:
            return {
                "success": True,
                "stdout": "RESULT:saw_start=1 saw_stop=0 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
                "stderr": "",
            }
        return {
            "success": True,
            "stdout": "RESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
            "stderr": "",
        }

    res = orchestrator.execute_multi_agent_workflow(
        task,
        max_repairs=2,
        model="stub-model",
        generate_fn=fake_generate,
        repair_fn=fake_repair,
        compile_fn=lambda *args, **kwargs: {"success": True, "stdout": "", "stderr": "", "returncode": 0},
        hardware_check_fn=lambda *args, **kwargs: {"success": True, "stage": "hardware_pass"},
        sim_fn=fake_sim,
    )

    assert res["final"] == "pass"
    assert res["passed"] is True
    assert res["retry_count"] >= 1
    assert len(res["transitions"]) >= 5
    assert len(res["agent_results"]) >= 4

    roles_run = [r["role"] for r in res["agent_results"]]
    assert "specification_analyst" in roles_run
    assert "codebase_mapper" in roles_run
    assert "planning_agent" in roles_run
    assert "implementation_agent" in roles_run
    assert "reviewer_agent" in roles_run
    assert "repair_agent" in roles_run
