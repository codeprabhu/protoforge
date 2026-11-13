"""ProtoForge generation -> structural gate -> compile -> simulate -> repair loop.

Configuration is read once per call via :class:`agent_core.config.Config`, and
every artifact for a run lives under its own ``results/runs/<run_id>/``
directory, so two runs never overwrite each other. Each run also writes a
header (``run.json``), one attempt record per candidate, a one-line index entry
in ``results/runs.jsonl``, and a minimal event trace.
"""

from __future__ import annotations

import json
import subprocess
import time
from functools import partial
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from agent.ollama_client import model_digest
from agent.providers import ProviderConfig
from agent.rtl_generator import analyze_rtl_failures, generate_multi_device_glue_rtl, repair_rtl
from agent_core.config import Config, RunPaths
from core import outcomes
from core.event_store import EventStore
from core.orchestrator import Orchestrator
from core.schemas import Task
from core.trace_schema import EventType, RunTrace
from domains.i2c.adapter import I2CDomainAdapter
from tools.compile_tool import compile_verilog
from tools.hardware_checks import run_hardware_checks
from tools.judge import judge
from tools.rtl_memory import memory_summary, record_failed_example, record_passing_example
from tools.simulation_tool import run_simulation
from tools.structural_checks import structural_checks

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@lru_cache(maxsize=1)
def _git_describe() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    started = time.time()
    header: dict[str, Any] = {
        "run_id": paths.run_id,
        "started_at": started,
        "git_commit": _git_commit(),
        "git_describe": _git_describe(),
        "model": cfg.model,
        "model_digest": model_digest(cfg.model) if cfg.provider == "ollama" else None,
        "seed": cfg.seed,
        "temperature": cfg.temperature,
        "config": cfg.as_dict(),
    }
    _write_json(paths.run_header(), header)

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

    trace = RunTrace(run_id=paths.run_id, task_id=task.task_id, domain="i2c")
    trace.add_event(
        EventType.RUN_STARTED,
        model=cfg.model,
        data={"config": cfg.as_dict(), "git_commit": header["git_commit"]},
    )

    def event_sink(event_type, data: dict) -> None:
        trace.add_event(event_type, model=cfg.model, data=data)
        progress_fn = hooks.get("progress_fn")
        if progress_fn:
            try:
                progress_fn(event_type.value if hasattr(event_type, "value") else str(event_type), data)
            except Exception:
                pass

    log = Orchestrator(adapter).execute_task(
        task,
        max_repairs=cfg.max_repairs,
        model=cfg.model,
        seed=cfg.seed,
        temperature=cfg.temperature,
        results_log_path=cfg.results_log,
        run_dir=str(paths.run_dir),
        run_id=paths.run_id,
        retrieval=cfg.retrieval,
        patcher=cfg.patcher,
        repair_model=cfg.repair_model,
        analyst_model=cfg.analyst_model,
        event_sink=event_sink,
        trace=trace,
        compile_fn=hooks.get("compile_fn") or compile_verilog,
        hardware_check_fn=hooks.get("hardware_check_fn") or run_hardware_checks,
        sim_fn=hooks.get("sim_fn") or run_simulation,
        judge_fn=hooks.get("judge_fn") or judge,
        struct_check_fn=hooks.get("struct_check_fn") or structural_checks,
        generate_fn=hooks.get("generate_fn") or partial(
            generate_multi_device_glue_rtl,
            provider_config=ProviderConfig(
                provider=cfg.provider, model=cfg.model, account=cfg.provider_account,
                endpoint=cfg.provider_endpoint,
            ),
            timeout=cfg.generation_timeout,
        ),
        repair_fn=hooks.get("repair_fn") or partial(
            repair_rtl,
            provider_config=ProviderConfig(
                provider=cfg.repair_provider or cfg.provider,
                model=cfg.repair_model or cfg.model,
                account=cfg.repair_provider_account or cfg.provider_account,
                endpoint=cfg.repair_provider_endpoint or cfg.provider_endpoint,
            ),
            timeout=cfg.generation_timeout,
        ),
        failure_analysis_fn=hooks.get("failure_analysis_fn") or partial(
            analyze_rtl_failures,
            provider_config=ProviderConfig(
                provider=cfg.analyst_provider or cfg.provider,
                model=cfg.analyst_model or cfg.model,
                account=cfg.analyst_provider_account or cfg.provider_account,
                endpoint=cfg.analyst_provider_endpoint or cfg.provider_endpoint,
            ),
            timeout=cfg.generation_timeout,
            spec_path=cfg.spec_path,
        ) if cfg.analyst_model else hooks.get("failure_analysis_fn"),
        on_success=hooks.get("on_success") or record_passing_example,
        on_failure=hooks.get("on_failure") or record_failed_example,
    )

    log["run_id"] = paths.run_id
    log["run_dir"] = str(paths.run_dir)

    header.update({
        "finished_at": time.time(),
        "final": log.get("final"),
        "attempts": len(log.get("attempts", [])),
        "first_attempt_pass": bool(log.get("first_attempt_pass", False)),
        "recovered_after_repair": bool(log.get("recovered_after_repair", False)),
        "elapsed_sec": log.get("elapsed_sec"),
        # Only present when the run failed for a reason other than the gates;
        # omitted otherwise so a clean header stays clean.
        **{
            key: log[key]
            for key in ("generation_error", "tool_error", "repair_error")
            if log.get(key)
        },
    })
    _write_json(paths.run_header(), header)

    trace.end_time = time.time()
    trace.status = "completed" if log.get("final") == outcomes.PASS else "failed"
    trace.summary = {
        "final": log.get("final"),
        "attempts": len(log.get("attempts", [])),
        "first_attempt_pass": bool(log.get("first_attempt_pass", False)),
        "recovered_after_repair": bool(log.get("recovered_after_repair", False)),
        "run_dir": str(paths.run_dir),
    }
    trace.add_event(
        EventType.RUN_FINISHED,
        model=cfg.model,
        data={"final": log.get("final"), "run_dir": str(paths.run_dir)},
    )
    log["trace_path"] = EventStore(cfg.trace_dir).save_trace(trace)
    return log


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
