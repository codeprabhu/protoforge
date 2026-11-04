"""Replay the verification gates for a stored attempt and compare verdicts.

Usage::

    python tools/replay.py results/runs/<run_id> [--attempt N]

The replay uses the exact ``attempt_<n>.v`` that was compiled during the run,
the device files and testbench recorded in ``run.json`` (or CLI overrides), and
the same gate tools. It never calls the model. A mismatch means either the
stored artifact or the oracle changed since the run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compile_tool import compile_verilog  # noqa: E402
from tools.hardware_checks import run_hardware_checks  # noqa: E402
from tools.i2c_spec import parse_i2c_spec  # noqa: E402
from tools.judge import judge  # noqa: E402
from tools.simulation_tool import parse_result_line, run_simulation  # noqa: E402
from tools.structural_checks import structural_checks  # noqa: E402
from tools.verilog_parser import parse_verilog_file  # noqa: E402

TERMINAL_OUTCOMES = {
    "pass",
    "structural_failed",
    "compile_failed",
    "hardware_failed",
    "sim_checks_failed",
    "tool_missing",
}


def _load_run_header(run_dir: Path) -> Dict[str, Any]:
    header = run_dir / "run.json"
    if not header.is_file():
        return {}
    try:
        return json.loads(header.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_paths(run_dir: Path, header: Dict[str, Any], args) -> Dict[str, Any]:
    config = header.get("config", {}) if isinstance(header, dict) else {}
    return {
        "device_paths": args.device or config.get("device_paths")
        or ["devices/device_sensor.v", "devices/device_controller.v"],
        "tb_path": args.tb or config.get("tb_path") or "protocols/i2c/testbench_i2c.v",
        "spec_path": args.spec or config.get("spec_path") or "protocols/i2c/spec.md",
    }


def _fresh_gates(
    code: str, *, device_paths: List[str], tb_path: str, spec_path: str
) -> Dict[str, Any]:
    required = [parse_verilog_file(path).name for path in device_paths]
    struct = structural_checks(code, required)
    stages: List[Dict[str, Any]] = [{
        "stage": "structural",
        "passed": struct.get("pass", False),
        "failed_checks": list(struct.get("failed_checks", [])),
    }]
    if not struct.get("pass", False):
        return {"outcome": "structural_failed", "stages": stages}

    spec = parse_i2c_spec(spec_path)
    with tempfile.TemporaryDirectory(prefix="protoforge-replay-") as tmp:
        candidate = Path(tmp) / "attempt.v"
        candidate.write_text(code, encoding="utf-8")
        vvp = str(Path(tmp) / "attempt.vvp")
        compile_res = compile_verilog([*device_paths, str(candidate), tb_path], out=vvp)
        stages.append({
            "stage": "compile",
            "passed": compile_res.get("success", False),
            "failed_checks": [],
        })
        if not compile_res.get("success", False):
            return {"outcome": "tool_missing" if compile_res.get("tool_missing") else "compile_failed",
                    "stages": stages}

        hw = run_hardware_checks([*device_paths, str(candidate)])
        hw_passed = hw.get("success", False)
        stages.append({
            "stage": "hardware",
            "passed": hw_passed,
            "failed_checks": [] if hw_passed else [hw.get("stage", "hardware")],
        })
        if not hw_passed:
            return {"outcome": "tool_missing" if hw.get("stage", "").endswith("tool_missing") else "hardware_failed",
                    "stages": stages}

        plusargs = [
            f"+I2C_ADDR={spec.address_hex}",
            f"+I2C_DATA={spec.data_hex}",
            f"+I2C_NBYTES={spec.nbytes}",
        ]
        sim = run_simulation(vvp, plusargs=plusargs)
        stages.append({
            "stage": "simulation",
            "passed": sim.get("success", False),
            "failed_checks": [],
        })
        if sim.get("tool_missing"):
            return {"outcome": "tool_missing", "stages": stages}

        raw = parse_result_line(sim.get("stdout", ""))
        verdict = judge(
            raw,
            expected_addr=spec.address_hex,
            expected_data=spec.data_hex,
            expected_data_bytes=spec.nbytes,
            expected_data_list=spec.data_hexes,
        )
        stages.append({
            "stage": "judge",
            "passed": verdict.get("pass", False),
            "failed_checks": list(verdict.get("failed_checks", [])),
        })
        passed = bool(sim.get("success", False) and verdict.get("pass", False))
        return {"outcome": "pass" if passed else "sim_checks_failed", "stages": stages}


def _compare(stored: Dict[str, Any], fresh: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    stored_stages = {s["stage"]: s for s in stored.get("stages", [])}
    fresh_stages = {s["stage"]: s for s in fresh.get("stages", [])}
    for name, stored_stage in stored_stages.items():
        fresh_stage = fresh_stages.get(name)
        if fresh_stage is None:
            problems.append(f"stored stage {name!r} was not produced on replay")
            continue
        if bool(stored_stage.get("passed")) != bool(fresh_stage.get("passed")):
            problems.append(
                f"{name}: stored passed={stored_stage.get('passed')} "
                f"replay passed={fresh_stage.get('passed')}"
            )
        if set(stored_stage.get("failed_checks", [])) != set(fresh_stage.get("failed_checks", [])):
            problems.append(
                f"{name}: stored failed_checks={stored_stage.get('failed_checks')} "
                f"replay failed_checks={fresh_stage.get('failed_checks')}"
            )
    stored_outcome = stored.get("outcome", "")
    if stored_outcome in TERMINAL_OUTCOMES and stored_outcome != fresh.get("outcome"):
        problems.append(f"outcome: stored={stored_outcome} replay={fresh.get('outcome')}")
    return problems


def replay_run(
    run_dir: str | Path,
    *,
    attempt: Optional[int] = None,
    device_paths: Optional[List[str]] = None,
    tb_path: Optional[str] = None,
    spec_path: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Replay one or all attempts in ``run_dir``; return a comparison report."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    header = _load_run_header(run_dir)

    class _Args:
        device = device_paths
        tb = tb_path
        spec = spec_path

    paths = _resolve_paths(run_dir, header, _Args)
    record_paths = sorted(run_dir.glob("attempt_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    if attempt is not None:
        record_paths = [p for p in record_paths if p.stem == f"attempt_{attempt}"]
    if not record_paths:
        raise FileNotFoundError(f"no attempt records found in {run_dir}")

    results = []
    for record_path in record_paths:
        number = int(record_path.stem.split("_")[1])
        stored = json.loads(record_path.read_text(encoding="utf-8"))
        code_path = run_dir / f"attempt_{number}.v"
        if not code_path.is_file():
            results.append({"attempt": number, "matched": False, "problems": ["missing attempt code file"]})
            continue
        code = code_path.read_text(encoding="utf-8")
        fresh = _fresh_gates(
            code,
            device_paths=paths["device_paths"],
            tb_path=paths["tb_path"],
            spec_path=paths["spec_path"],
        )
        problems = _compare(stored, fresh)
        entry = {
            "attempt": number,
            "matched": not problems,
            "stored_outcome": stored.get("outcome"),
            "replay_outcome": fresh.get("outcome"),
            "problems": problems,
        }
        results.append(entry)
        if verbose:
            status = "MATCH" if entry["matched"] else "MISMATCH"
            print(
                f"attempt {number}: {status} "
                f"(stored={entry['stored_outcome']} replay={entry['replay_outcome']})"
            )
            for problem in problems:
                print(f"  - {problem}")

    return {
        "run_dir": str(run_dir),
        "matched": all(item["matched"] for item in results),
        "attempts": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="results/runs/<run_id> directory")
    parser.add_argument("--attempt", type=int, default=None, help="replay only this attempt")
    parser.add_argument("--device", action="append", help="device Verilog path (repeatable)")
    parser.add_argument("--tb", help="testbench path")
    parser.add_argument("--spec", help="protocol spec path")
    args = parser.parse_args(argv)

    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        print("Icarus Verilog (iverilog/vvp) is required for replay", file=sys.stderr)
        return 2
    report = replay_run(
        args.run_dir,
        attempt=args.attempt,
        device_paths=args.device,
        tb_path=args.tb,
        spec_path=args.spec,
    )
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
