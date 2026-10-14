"""Subprocess wrapper for Icarus Verilog compilation."""

import subprocess
from typing import List, Union


def compile_verilog(filepaths: Union[str, List[str]], out: str = "generated/sim.out", timeout: float = 20.0) -> dict:
    """Compile multiple Verilog files and return structured diagnostics."""
    if isinstance(filepaths, str):
        filepaths = [filepaths]
    try:
        result = subprocess.run(
            ["iverilog", "-g2001", "-o", out, *filepaths],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Icarus Verilog was not found. Install iverilog and ensure iverilog/vvp are on PATH.",
            "tool_missing": True,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "success": False,
            "stdout": stdout,
            "stderr": f"Compilation timed out after {timeout}s. {stderr}".strip(),
            "timeout": True,
        }
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
