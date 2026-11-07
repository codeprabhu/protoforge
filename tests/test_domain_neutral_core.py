"""Tests for the domain-neutral core: schemas, registry and orchestrator routing.

The schema checks here pin the *contract the orchestrator depends on* (defaults,
required fields, status transitions) rather than restating pydantic field names.
Domain discovery lives in ``tests/test_domain_registry.py``.
"""

import pytest
from core.schemas import CandidatePatch, Requirement, Task, ValidationResult
from domains.registry import get_domain
from core.orchestrator import Orchestrator

GOOD_RTL = "protocols/i2c/reference/good_master_reset_safe.v"


def test_requirement_requires_a_provenance_and_description():
    with pytest.raises(Exception):  # pydantic ValidationError
        Requirement(req_id="I2C-001", domain="i2c", title="Start condition")

    req = Requirement(
        req_id="I2C-001",
        domain="i2c",
        title="Start condition",
        description="Must generate START condition",
    )
    assert req.severity == "must"
    assert req.status == "active"


def test_validation_result_separates_tool_absence_from_failure():
    failed = ValidationResult(stage="compile", passed=False, stderr="syntax error")
    assert failed.tool_missing is False

    missing = ValidationResult(stage="compile", passed=False, tool_missing=True)
    # The orchestrator branches on this flag; a tool outage must not look like
    # a model defect.
    assert missing.tool_missing is True
    assert missing.failed_checks == []


def test_candidate_patch_hash_is_filled_in_by_the_caller_not_the_schema():
    patch = CandidatePatch(target_file="generated/top.v", full_content="module top; endmodule")
    assert patch.patch_hash == ""
    assert patch.patch_id  # generated, non-empty


def test_task_status_starts_pending_and_metadata_is_kept():
    task = Task(task_id="TASK-1", domain="i2c", description="verify")
    assert task.status == "pending"
    task.metadata["target_file"] = "x.v"
    assert task.metadata["target_file"] == "x.v"


def test_i2c_adapter_exposes_the_required_protocol_requirements():
    adapter = get_domain("i2c")
    reqs = adapter.parse_requirements("protocols/i2c/spec.md")
    req_ids = {r.req_id for r in reqs}
    assert {"I2C-ADDR", "I2C-DATA", "I2C-START", "I2C-STOP", "I2C-ACK"} <= req_ids
    assert all(r.severity == "must" for r in reqs)


def test_orchestrator_routes_a_structuring_failure_to_structural_failed(tmp_path):
    """No repair budget + a gate that rejects the candidate must be reported."""
    adapter = get_domain("i2c")
    orchestrator = Orchestrator(adapter)

    task = Task(
        task_id="TASK-STRUCT-001",
        domain="i2c",
        description="structural failure routing",
        metadata={
            "target_file": str(tmp_path / "top_glue.v"),
            "sim_out": str(tmp_path / "sim.out"),
        },
    )

    def reject_structure(candidate, required):
        return {"pass": False, "failed_checks": ["open_drain_sda"]}

    result = orchestrator.execute_task(
        task,
        max_repairs=0,
        model="stub-model",
        generate_fn=lambda *a, **k: "module top_glue; endmodule",
        struct_check_fn=reject_structure,
    )

    assert result["final"] == "structural_failed"
    assert result["first_attempt_pass"] is False
    assert result["attempts"][0]["failed_checks"] == ["open_drain_sda"]


def test_orchestrator_reports_generation_error_when_generate_fn_raises(tmp_path):
    """A raising generate_fn must land on a known outcome, not 'other'."""
    from core import outcomes

    adapter = get_domain("i2c")
    task = Task(
        task_id="TASK-GEN-001",
        domain="i2c",
        description="generation failure routing",
        metadata={
            "target_file": str(tmp_path / "top_glue.v"),
            "sim_out": str(tmp_path / "sim.out"),
        },
    )

    def boom(*args, **kwargs):
        raise RuntimeError("model service unavailable")

    result = Orchestrator(adapter).execute_task(task, max_repairs=2, generate_fn=boom)

    assert result["final"] == outcomes.GENERATION_ERROR
    assert "model service unavailable" in result["generation_error"]
    assert result["final"] in outcomes.KNOWN_FINALS
    assert result["attempts"] == []


def test_orchestrator_passes_when_every_gate_accepts_the_candidate(tmp_path):
    """The happy path still reaches 'pass' with a real judge on a stubbed RESULT."""
    adapter = get_domain("i2c")
    good = open(GOOD_RTL, encoding="utf-8").read()
    task = Task(
        task_id="TASK-ORCH-001",
        domain="i2c",
        description="Orchestrator happy path",
        metadata={
            "target_file": str(tmp_path / "top_glue.v"),
            "sim_out": str(tmp_path / "sim.out"),
        },
    )

    result = Orchestrator(adapter).execute_task(
        task,
        max_repairs=1,
        model="stub-model",
        generate_fn=lambda *args, **kwargs: good,
        compile_fn=lambda *args, **kwargs: {"success": True, "stdout": "", "stderr": "", "returncode": 0},
        hardware_check_fn=lambda *args, **kwargs: {"success": True, "stage": "hardware_pass"},
        sim_fn=lambda *args, **kwargs: {
            "success": True,
            "stdout": "RESULT:saw_start=1 saw_stop=1 saw_ack_addr=1 saw_ack_data=1 addr=a0 data=3c",
            "stderr": "",
        },
    )

    assert result["final"] == "pass"
    assert result["first_attempt_pass"] is True
    assert len(result["attempts"]) == 1
