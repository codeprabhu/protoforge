"""ProtoForge generation -> structural gate -> compile -> simulate -> repair loop."""

import hashlib
import json
import os
import time

from agent.ollama_client import DEFAULT_MODEL, DEFAULT_SEED
from agent.rtl_generator import generate_multi_device_glue_rtl, repair_rtl
from tools.compile_tool import compile_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from tools.structural_checks import structural_checks, explain_structural_failures
from tools.error_context import extract_error_context
from tools.rtl_memory import record_passing_example, record_failed_example, memory_summary
from tools.hardware_checks import run_hardware_checks
from tools.verilog_parser import parse_verilog_file
from tools.i2c_spec import parse_i2c_spec

from core.schemas import Task
from core.orchestrator import Orchestrator
from domains.i2c.adapter import I2CDomainAdapter

DEFAULT_DEVICE_PATHS = ["devices/device_sensor.v", "devices/device_controller.v"]
DEVICE_PATHS = [
    path.strip()
    for path in os.getenv("PROTOFORGE_DEVICE_PATHS", ",".join(DEFAULT_DEVICE_PATHS)).split(",")
    if path.strip()
]
if not DEVICE_PATHS:
    raise ValueError("PROTOFORGE_DEVICE_PATHS must contain at least one Verilog device")
# Compatibility aliases for callers that used the original two-device API.
DEVICE_A = DEVICE_PATHS[0]
DEVICE_B = DEVICE_PATHS[1] if len(DEVICE_PATHS) > 1 else DEVICE_PATHS[0]
SPEC = "protocols/i2c/spec.md"
PROTOCOL = parse_i2c_spec(SPEC)
TB = "protocols/i2c/testbench_i2c.v"
TOP = "generated/top_glue.v"
SIM_OUT = "generated/sim.out"
MAX_REPAIRS = int(os.getenv("PROTOFORGE_MAX_REPAIRS", "2"))
RESULTS_LOG = "results/runs.jsonl"
REQUIRED_INSTANCES = [parse_verilog_file(path).name for path in DEVICE_PATHS]


def _snapshot(code: str, stage: str, failed_checks: list[str] | None = None) -> dict:
    return {
        "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "stage": stage,
        "failed_checks": failed_checks or [],
    }


def run_once(model: str = None, max_repairs: int = MAX_REPAIRS, seed: int | None = None,
             temperature: float = 0.0,
             compile_fn=None,
             hardware_check_fn=None,
             sim_fn=None,
             judge_fn=None,
             struct_check_fn=None,
             generate_fn=None,
             repair_fn=None) -> dict:
    """Run one model attempt with bounded automatic repair and verified memory via domain orchestrator."""
    adapter = I2CDomainAdapter(
        device_paths=DEVICE_PATHS,
        tb_path=TB,
        spec_path=SPEC,
        required_instances=REQUIRED_INSTANCES,
    )
    orchestrator = Orchestrator(adapter)
    task = Task(
        task_id=f"i2c-{int(time.time()*1000)}",
        domain="i2c",
        description="Generate and verify I2C multi-device glue RTL",
        metadata={
            "target_file": TOP,
            "sim_out": SIM_OUT,
            "spec_path": SPEC,
            "address_hex": PROTOCOL.address_hex,
            "data_hex": PROTOCOL.data_hex,
            "rtl_memory_before_run": memory_summary(),
        }
    )
    return orchestrator.execute_task(
        task,
        max_repairs=max_repairs,
        model=model or DEFAULT_MODEL,
        seed=seed,
        temperature=temperature,
        results_log_path=RESULTS_LOG,
        compile_fn=compile_fn or compile_verilog,
        hardware_check_fn=hardware_check_fn or run_hardware_checks,
        sim_fn=sim_fn or run_simulation,
        judge_fn=judge_fn or judge,
        struct_check_fn=struct_check_fn or structural_checks,
        generate_fn=generate_fn or generate_multi_device_glue_rtl,
        repair_fn=repair_fn or repair_rtl,
        on_success=record_passing_example,
        on_failure=record_failed_example,
    )


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
