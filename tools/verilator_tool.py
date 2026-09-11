import subprocess


def lint_verilog(filepath: str) -> dict:
    result = subprocess.run(
        ["verilator", "--lint-only", "-Wno-fatal", filepath],
        capture_output=True,
        text=True,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
