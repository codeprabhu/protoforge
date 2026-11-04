"""Tests for Phase 1 domain-neutral core, schemas, registry, and orchestrator."""

import pytest
from core.schemas import (
    CandidatePatch,
    Requirement,
    RunEvent,
    SourceReference,
    Task,
    ValidationResult,
)
from domains.registry import get_domain, list_domains
from domains.i2c.adapter import I2CDomainAdapter
from core.orchestrator import Orchestrator


def test_core_schemas():
    ref = SourceReference(file_path="devices/sensor.v", symbol="sensor", line_start=1, line_end=50)
    assert ref.file_path == "devices/sensor.v"

    req = Requirement(
        req_id="I2C-001",
        domain="i2c",
        title="Start condition",
        description="Must generate START condition",
        source_reference=ref,
    )
    assert req.req_id == "I2C-001"

    patch = CandidatePatch(
        target_file="generated/top.v",
        diff_content="+ assign sda = 1;",
        full_content="module top; endmodule",
    )
    assert patch.target_file == "generated/top.v"

    val = ValidationResult(
        stage="compile",
        passed=True,
        stdout="compiled successfully",
    )
    assert val.passed is True
    assert val.tool_missing is False

    task = Task(
        task_id="TASK-TEST-1",
        domain="i2c",
        description="Verify I2C core",
        requirements=[req],
    )
    assert task.status == "pending"
    assert len(task.requirements) == 1


def test_domain_registry():
    domains = list_domains()
    assert "i2c" in domains
    adapter = get_domain("i2c")
    assert isinstance(adapter, I2CDomainAdapter)
    assert adapter.name == "i2c"

    with pytest.raises(KeyError):
        get_domain("non_existent_domain_xyz")


def test_i2c_adapter_requirements_and_mutations():
    adapter = get_domain("i2c")
    reqs = adapter.parse_requirements("protocols/i2c/spec.md")
    req_ids = [r.req_id for r in reqs]
    assert "I2C-ADDR" in req_ids
    assert "I2C-DATA" in req_ids
    assert "I2C-START" in req_ids
    assert "I2C-STOP" in req_ids

    mutations = adapter.get_mutations()
    assert len(mutations) >= 5
    mutation_names = [m["name"] for m in mutations]
    assert "no_start" in mutation_names
    assert "no_stop" in mutation_names


def test_orchestrator_mocked_execution(tmp_path):
    adapter = get_domain("i2c")
    orchestrator = Orchestrator(adapter)

    good = open("protocols/i2c/reference/good_master_reset_safe.v").read()
    target_top = tmp_path / "top_glue.v"

    task = Task(
        task_id="TASK-ORCH-001",
        domain="i2c",
        description="Orchestrator test",
        metadata={
            "target_file": str(target_top),
            "sim_out": str(tmp_path / "sim.out"),
        },
    )

    def fake_generate(devices, spec_path, model=None, temperature=0.0, seed=None, **kwargs):
        return good

    result = orchestrator.execute_task(
        task,
        max_repairs=1,
        model="stub-model",
        generate_fn=fake_generate,
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
