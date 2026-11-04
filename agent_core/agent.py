"""ProtoForge generation -> structural gate -> compile -> simulate -> repair loop.

Configuration is read once per call via :class:`agent_core.config.Config`, and
every artifact for a run lives under its own ``results/runs/<run_id>/``
directory, so two runs never overwrite each other.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agent.rtl_generator import generate_multi_device_glue_rtl, repair_rtl
from agent_core.config import Config, RunPaths
from core.schemas import Task
from core.orchestrator import Orchestrator
from domains.i2c.adapter import I2CDomainAdapter
from tools.compile_tool import compile_verilog
from tools.hardware_checks import run_hardware_checks
from tools.judge import judge
from tools.rtl_memory import memory_summary, record_failed_example, record_passing_example
from tools.simulation_tool import run_simulation
from tools.structural_checks import structural_checks


def run_once(
    model: Optional[str] = None,
    max_repairs: Optional[int] = None,
    seed: Optional[int] = None,
    temperature: Optional[float] = None,
    config: Optional[Config] = None,
    run_id: Optional[str] = None,
    **hooks: Any,
) -> dict:
    """Run one model attempt with bounded automatic repair.

    ``hooks`` may override any pipeline stage (``compile_fn``, ``sim_fn``,
    ``judge_fn``, ``generate_fn``, ``repair_fn``, ...) for tests.
    """
    cfg = config or Config.from_env()
    overrides: dict[str, Any] = {}
    if model is not None:
        overrides["model"] = model
    if max_repairs is not None:
        overrides["max_repairs"] = int(max_repairs)
    if seed is not None:
        overrides["seed"] = seed
    if temperature is not None:
        overrides["temperature"] = temperature
    if overrides:
        cfg = cfg.with_overrides(**overrides)

    paths = RunPaths.create(cfg.output_root, run_id=run_id)
    adapter = I2CDomainAdapter(
        device_paths=list(cfg.device_paths),
        tb_path=cfg.tb_path,
        spec_path=cfg.spec_path,
        required_instances=list(cfg.required_instances),
    )
    task = Task(
        task_id=f"i2c-{paths.run_id}",
        domain="i2c",
        description="Generate and verify I2C multi-device glue RTL",
        metadata={
            "target_file": str(paths.attempt_code(0)),
            "sim_out": str(paths.attempt_vvp(0)),
            "spec_path": cfg.spec_path,
            "rtl_memory_before_run": memory_summary(),
        },
    )
    log = Orchestrator(adapter).execute_task(
        task,
        max_repairs=cfg.max_repairs,
        model=cfg.model,
        seed=cfg.seed,
        temperature=cfg.temperature,
        results_log_path=cfg.results_log,
        run_dir=str(paths.run_dir),
        retrieval=cfg.retrieval,
        compile_fn=hooks.get("compile_fn") or compile_verilog,
        hardware_check_fn=hooks.get("hardware_check_fn") or run_hardware_checks,
        sim_fn=hooks.get("sim_fn") or run_simulation,
        judge_fn=hooks.get("judge_fn") or judge,
        struct_check_fn=hooks.get("struct_check_fn") or structural_checks,
        generate_fn=hooks.get("generate_fn") or generate_multi_device_glue_rtl,
        repair_fn=hooks.get("repair_fn") or repair_rtl,
        on_success=hooks.get("on_success") or record_passing_example,
        on_failure=hooks.get("on_failure") or record_failed_example,
    )
    log["run_id"] = paths.run_id
    log["run_dir"] = str(paths.run_dir)
    return log


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
