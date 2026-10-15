"""Tool-backed lint and synthesis checks for generated hardware."""

from __future__ import annotations

import subprocess
from typing import Iterable


def _missing(tool: str) -> dict:
    return {
        "success": False,
        "tool_missing": True,
        "stdout": "",
        "stderr": f"{tool} was not found on PATH",
    }


def _run(command: list[str], timeout: float) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return _missing(command[0])
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "timeout": True,
            "stdout": exc.stdout or "",
            "stderr": f"{command[0]} timed out after {timeout}s",
        }
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_hardware_checks(
    filepaths: Iterable[str], top: str = "top_glue", timeout: float = 30.0
) -> dict:
    """Run Verilator lint and Yosys synthesis checks on RTL-only sources."""
    paths = [str(path) for path in filepaths]
    verilator = (
        ["verilator", "--lint-only", "-Wno-fatal", "--top-module", top] + paths
    )
    lint = _run(verilator, timeout)
    if lint.get("tool_missing"):
        return {"success": False, "stage": "lint_tool_missing", "lint": lint}
    if not lint["success"]:
        return {"success": False, "stage": "lint", "lint": lint}

    yosys_script = "read_verilog -sv " + " ".join(paths)
    yosys_script += f"; hierarchy -top {top}; proc; opt; check; stat"
    synthesis = _run(["yosys", "-q", "-p", yosys_script], timeout)
    if synthesis.get("tool_missing"):
        return {"success": False, "stage": "synthesis_tool_missing", "lint": lint, "synthesis": synthesis}
    return {
        "success": bool(synthesis["success"]),
        "stage": "synthesis" if not synthesis["success"] else "passed",
        "lint": lint,
        "synthesis": synthesis,
    }
