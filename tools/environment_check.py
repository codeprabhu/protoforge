"""Environment and toolchain availability and version inspection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict


def get_command_version(cmd: str, version_flag: str = "--version") -> str | None:
    """Run a command with version flag and return the first line of output."""
    path = shutil.which(cmd)
    if not path:
        return None
    try:
        res = subprocess.run(
            [path, version_flag],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        out = (res.stdout or res.stderr or "").strip().splitlines()
        return out[0] if out else "installed (no version output)"
    except Exception as exc:
        return f"installed (error querying version: {exc})"


def check_ollama(url: str = "http://localhost:11434") -> dict[str, Any]:
    """Check if Ollama service is reachable and list available models."""
    cli_path = shutil.which("ollama")
    cli_version = get_command_version("ollama", "--version") if cli_path else None
    api_available = False
    models: list[str] = []
    error = None

    try:
        req = urllib.request.Request(f"{url}/api/tags", headers={"User-Agent": "ProtoForge"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                api_available = True
    except Exception as exc:
        error = str(exc)

    return {
        "installed": bool(cli_path),
        "path": cli_path,
        "version": cli_version,
        "service_reachable": api_available,
        "url": url,
        "models": models,
        "error": error,
    }


def check_environment() -> dict[str, Any]:
    """Inspect all required and optional tools for ProtoForge harness."""
    tools = {
        "iverilog": {
            "path": shutil.which("iverilog"),
            "version": get_command_version("iverilog", "-V"),
            "role": "Icarus Verilog compiler (required for I2C/RTL simulation)",
        },
        "vvp": {
            "path": shutil.which("vvp"),
            "version": get_command_version("vvp", "-V"),
            "role": "Icarus Verilog runtime (required for simulation execution)",
        },
        "verilator": {
            "path": shutil.which("verilator"),
            "version": get_command_version("verilator", "--version"),
            "role": "Verilator RTL linter (recommended for hardware checks)",
        },
        "yosys": {
            "path": shutil.which("yosys"),
            "version": get_command_version("yosys", "-V"),
            "role": "Yosys synthesis suite (recommended for hardware synthesis gate)",
        },
        "clang": {
            "path": shutil.which("clang"),
            "version": get_command_version("clang", "--version"),
            "role": "C compiler / LibTooling (required for C/PCIe driver AST & validation)",
        },
        "gcc": {
            "path": shutil.which("gcc"),
            "version": get_command_version("gcc", "--version"),
            "role": "C compiler / kernel build backend",
        },
        "make": {
            "path": shutil.which("make"),
            "version": get_command_version("make", "--version"),
            "role": "Build automation for kernel modules",
        },
    }

    for name, info in tools.items():
        info["available"] = bool(info["path"])

    ollama_info = check_ollama()
    tools["ollama"] = ollama_info

    python_info = {
        "version": sys.version.split()[0],
        "executable": sys.executable,
        "platform": sys.platform,
    }

    missing_required = []
    if not tools["iverilog"]["available"]:
        missing_required.append("iverilog")
    if not tools["vvp"]["available"]:
        missing_required.append("vvp")

    return {
        "ready_for_rtl_sim": len(missing_required) == 0,
        "missing_required_rtl_tools": missing_required,
        "tools": tools,
        "python": python_info,
    }


if __name__ == "__main__":
    report = check_environment()
    print(json.dumps(report, indent=2))
