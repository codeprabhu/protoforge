"""ProtoForge generation -> structural gate -> compile -> simulate -> repair loop."""

import hashlib
import json
import os
import time

from agent.ollama_client import DEFAULT_MODEL, DEFAULT_SEED
from agent.rtl_generator import generate_glue_rtl, repair_rtl
from tools.compile_tool import compile_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from tools.structural_checks import structural_checks, explain_structural_failures
from tools.error_context import extract_error_context
from tools.rtl_memory import record_passing_example, record_failed_example, memory_summary

DEVICE_A = "devices/device_sensor.v"
DEVICE_B = "devices/device_controller.v"
SPEC = "protocols/i2c/spec.md"
TB = "protocols/i2c/testbench_i2c.v"
TOP = "generated/top_glue.v"
SIM_OUT = "generated/sim.out"
MAX_REPAIRS = int(os.getenv("PROTOFORGE_MAX_REPAIRS", "2"))
RESULTS_LOG = "results/runs.jsonl"
REQUIRED_INSTANCES = ["device_sensor", "device_controller"]


def _snapshot(code: str, stage: str, failed_checks: list[str] | None = None) -> dict:
    return {
        "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "stage": stage,
        "failed_checks": failed_checks or [],
    }


def run_once(model: str = None, max_repairs: int = MAX_REPAIRS, seed: int | None = None,
             temperature: float = 0.0) -> dict:
    """Run one model attempt with bounded automatic repair and verified memory."""
    started = time.time()
    selected_model = model or DEFAULT_MODEL
    log = {
        "timestamp": started,
        "model": selected_model,
        "temperature_policy": f"generation={temperature}; repair increases slightly per attempt",
        "generation_temperature": temperature,
        "seed": DEFAULT_SEED if seed is None else seed,
        "retrieval_enabled": os.getenv("PROTOFORGE_NO_RETRIEVAL", "").strip().lower() not in {"1", "true", "yes", "on"},
        "max_repairs": max(0, int(max_repairs)),
        "rtl_memory_before_run": memory_summary(),
        "attempts": [],
    }

    generate_glue_rtl(DEVICE_A, DEVICE_B, SPEC, out_path=TOP, model=model, temperature=temperature, seed=seed)

    for attempt in range(max(0, int(max_repairs)) + 1):
        # A small increase per repair avoids identical repeated samples while
        # keeping the initial generation deterministic.
        repair_temperature = min(0.15 + 0.15 * attempt, 0.45)
        code = open(TOP, encoding="utf-8").read()

        # Cheap semantic/synthesis-shape checks first; do not spend a compile
        # or simulation cycle on an answer that ignores the physical bus rules.
        struct = structural_checks(code, REQUIRED_INSTANCES)
        if not struct["pass"]:
            log["attempts"].append({
                "attempt": attempt,
                "stage": "structural",
                "success": False,
                "failed_checks": struct["failed_checks"],
                **_snapshot(code, "structural", struct["failed_checks"]),
            })
            if attempt < max_repairs:
                detail = explain_structural_failures(struct["failed_checks"], REQUIRED_INSTANCES)
                repair_rtl(code, "is missing required structure", detail, out_path=TOP,
                           model=model, temperature=repair_temperature, seed=seed)
                continue
            log["final"] = "structural_failed"
            record_failed_example(code, selected_model, "structural", struct["failed_checks"],
                                  explain_structural_failures(struct["failed_checks"], REQUIRED_INSTANCES))
            break

        compile_result = compile_verilog([DEVICE_A, DEVICE_B, TOP, TB], out=SIM_OUT)
        if not compile_result["success"]:
            log["attempts"].append({
                "attempt": attempt,
                "stage": "compile",
                "success": False,
                "stderr": compile_result["stderr"],
                **_snapshot(code, "compile"),
            })
            if compile_result.get("tool_missing"):
                log["final"] = "tool_missing"
                log["tool_error"] = compile_result["stderr"]
                break
            if attempt < max_repairs:
                context = extract_error_context(TOP, code, compile_result["stderr"])
                detail = f"Compiler error context (only the lines that actually failed):\n\n{context}"
                repair_rtl(code, "failed to compile", detail, out_path=TOP,
                           model=model, temperature=repair_temperature, seed=seed)
                continue
            log["final"] = "compile_failed"
            record_failed_example(code, selected_model, "compile", detail=compile_result["stderr"])
            break

        sim = run_simulation(SIM_OUT)
        if sim.get("tool_missing"):
            log["attempts"].append({
                "attempt": attempt,
                "stage": "simulation_tool_missing",
                "success": False,
                "stderr": sim.get("stderr", ""),
                **_snapshot(code, "simulation_tool_missing"),
            })
            log["final"] = "tool_missing"
            log["tool_error"] = sim.get("stderr", "")
            break

        result = parse_result_line(sim.get("stdout", ""))
        verdict = judge(result)
        failed_checks = list(verdict.get("failed_checks") or [])
        if verdict["pass"] and not sim.get("success", False):
            failed_checks.append("simulator_nonzero_exit")
        simulation_pass = verdict["pass"] and sim.get("success", False)
        log["attempts"].append({
            "attempt": attempt,
            "stage": "simulate",
            "success": simulation_pass,
            "failed_checks": failed_checks,
            "raw": result,
            "simulator_success": sim.get("success"),
            "simulator_stderr": sim.get("stderr", "")[:2000],
            **_snapshot(code, "simulate", failed_checks),
        })

        if simulation_pass:
            log["final"] = "pass"
            artifact = record_passing_example(
                code,
                model=selected_model,
                metadata={"protocol": "i2c-v1", "checks": verdict["raw"]},
            )
            log["verified_example"] = str(artifact)
            break
        if attempt < max_repairs:
            detail = f"compiled but failed protocol checks: {explain_failures(failed_checks)}"
            repair_rtl(code, "compiled but failed protocol checks", detail, out_path=TOP,
                       model=model, temperature=repair_temperature, seed=seed)
            continue

        log["final"] = "sim_checks_failed"
        record_failed_example(code, selected_model, "simulate", failed_checks,
                              explain_failures(failed_checks))
        break

    log["first_attempt_pass"] = bool(
        log["attempts"] and log["attempts"][0].get("success", False)
    )
    log["recovered_after_repair"] = bool(
        log.get("final") == "pass" and len(log["attempts"]) > 1
    )
    log["elapsed_sec"] = round(time.time() - started, 3)
    os.makedirs(os.path.dirname(RESULTS_LOG) or ".", exist_ok=True)
    with open(RESULTS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(log, sort_keys=True) + "\n")
    return log


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
