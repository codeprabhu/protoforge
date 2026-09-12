import json
import time

from agent.rtl_generator import generate_glue_rtl, repair_rtl
from tools.compile_tool import compile_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures
from tools.structural_checks import structural_checks, explain_structural_failures
from tools.error_context import extract_error_context

DEVICE_A = "devices/device_sensor.v"
DEVICE_B = "devices/device_controller.v"
SPEC = "protocols/i2c/spec.md"
TB = "protocols/i2c/testbench_i2c.v"
TOP = "generated/top_glue.v"
SIM_OUT = "generated/sim.out"
MAX_REPAIRS = 3
RESULTS_LOG = "results/runs.jsonl"
REQUIRED_INSTANCES = ["device_sensor", "device_controller"]


def run_once(model: str = None) -> dict:
    log = {"timestamp": time.time(), "model": model, "attempts": []}

    generate_glue_rtl(DEVICE_A, DEVICE_B, SPEC, out_path=TOP, model=model)

    for attempt in range(MAX_REPAIRS + 1):
        # repeated identical failures across attempts is a known failure
        # mode with small local models at temperature 0 (or near it) --
        # they regenerate the exact same wrong answer for the exact same
        # prompt. Nudge sampling temperature up slightly on each retry so
        # repair attempts actually explore different fixes.
        repair_temperature = 0.2 + 0.2 * attempt

        # --- structural gate: catches "compiles but ignored the task"
        # failures (missing instantiation, markdown artifacts, no FSM,
        # illegal delay/brace constructs) before spending an iverilog
        # cycle on them. ---
        code = open(TOP).read()
        struct = structural_checks(code, REQUIRED_INSTANCES)
        if not struct["pass"]:
            log["attempts"].append({
                "attempt": attempt, "stage": "structural", "success": False,
                "failed_checks": struct["failed_checks"],
            })
            if attempt < MAX_REPAIRS:
                detail = explain_structural_failures(struct["failed_checks"], REQUIRED_INSTANCES)
                repair_rtl(code, "is missing required structure", detail, out_path=TOP,
                           model=model, temperature=repair_temperature)
                continue
            else:
                log["final"] = "structural_failed"
                break

        compile_result = compile_verilog([DEVICE_A, DEVICE_B, TOP, TB], out=SIM_OUT)

        if not compile_result["success"]:
            log["attempts"].append({
                "attempt": attempt, "stage": "compile", "success": False,
                "stderr": compile_result["stderr"],
            })
            if attempt < MAX_REPAIRS:
                # give the model the ACTUAL offending lines, not just the
                # abstract compiler message -- "Malformed statement" alone
                # isn't enough signal for a small model to fix the bug.
                context = extract_error_context(TOP, code, compile_result["stderr"])
                detail = f"Compiler error context (only the lines that actually failed):\n\n{context}"
                repair_rtl(code, "failed to compile", detail, out_path=TOP,
                           model=model, temperature=repair_temperature)
                continue
            else:
                log["final"] = "compile_failed"
                break

        sim = run_simulation(SIM_OUT)
        result = parse_result_line(sim["stdout"])
        verdict = judge(result)

        log["attempts"].append({
            "attempt": attempt, "stage": "simulate", "success": verdict["pass"],
            "failed_checks": verdict.get("failed_checks"), "raw": result,
        })

        if verdict["pass"]:
            log["final"] = "pass"
            break
        elif attempt < MAX_REPAIRS:
            detail = f"compiled but failed protocol checks: {explain_failures(verdict['failed_checks'])}"
            repair_rtl(code, "compiled but failed protocol checks", detail, out_path=TOP,
                       model=model, temperature=repair_temperature)
        else:
            log["final"] = "sim_checks_failed"

    with open(RESULTS_LOG, "a") as f:
        f.write(json.dumps(log) + "\n")

    return log


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
