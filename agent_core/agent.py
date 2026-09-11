import json
import time

from agent.rtl_generator import generate_glue_rtl, repair_rtl
from tools.compile_tool import compile_verilog
from tools.simulation_tool import run_simulation, parse_result_line
from tools.judge import judge, explain_failures

DEVICE_A = "devices/device_sensor.v"
DEVICE_B = "devices/device_controller.v"
SPEC = "protocols/i2c/spec.md"
TB = "protocols/i2c/testbench_i2c.v"
TOP = "generated/top_glue.v"
SIM_OUT = "generated/sim.out"
MAX_REPAIRS = 3
RESULTS_LOG = "results/runs.jsonl"


def run_once(model: str = None) -> dict:
    log = {"timestamp": time.time(), "model": model, "attempts": []}

    generate_glue_rtl(DEVICE_A, DEVICE_B, SPEC, out_path=TOP, model=model)

    for attempt in range(MAX_REPAIRS + 1):
        compile_result = compile_verilog([DEVICE_A, DEVICE_B, TOP, TB], out=SIM_OUT)

        if not compile_result["success"]:
            log["attempts"].append({
                "attempt": attempt, "stage": "compile", "success": False,
                "stderr": compile_result["stderr"],
            })
            if attempt < MAX_REPAIRS:
                code = open(TOP).read()
                repair_rtl(code, "failed to compile", compile_result["stderr"], out_path=TOP, model=model)
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
            code = open(TOP).read()
            detail = f"compiled but failed protocol checks: {explain_failures(verdict['failed_checks'])}"
            repair_rtl(code, "compiled but failed protocol checks", detail, out_path=TOP, model=model)
        else:
            log["final"] = "sim_checks_failed"

    with open(RESULTS_LOG, "a") as f:
        f.write(json.dumps(log) + "\n")

    return log


if __name__ == "__main__":
    print(json.dumps(run_once(), indent=2))
