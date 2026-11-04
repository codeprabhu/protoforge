"""Run an Icarus simulation and parse the stable machine-readable result."""

import re
import subprocess

RESULT_RE = re.compile(
    r"RESULT:saw_start=(\d) saw_stop=(\d) saw_ack_addr=(\d) saw_ack_data=(\d) "
    r"addr=([0-9a-fA-F]+) data=([0-9a-fA-F]+)"
    r"(?: tb_done=(\d) starts=(\d+) stops=(\d+))?"
    r"(?: sda_glitches=(\d+) bytes=(\d+))?"
)
DATA_EVENT_RE = re.compile(r"EVENT:DATA:([0-9a-fA-F]{2}):")


def run_simulation(executable: str, timeout: float = 8.0, plusargs=None) -> dict:
    """Run vvp; always return a result dictionary, including on success."""
    try:
        result = subprocess.run(
            ["vvp", executable, *(plusargs or [])],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "returncode": None,
            "stdout": "",
            "stderr": "Icarus Verilog runtime vvp was not found. Install iverilog and ensure vvp is on PATH.",
            "timeout": False,
            "tool_missing": True,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "success": False,
            "returncode": None,
            "stdout": stdout,
            "stderr": f"Simulation timeout after {timeout}s. {stderr}".strip(),
            "timeout": True,
        }
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "timeout": False,
    }


def parse_result_line(stdout: str) -> dict:
    """Parse the RESULT line emitted by protocols/i2c/testbench_i2c.v."""
    m = RESULT_RE.search(stdout or "")
    if not m:
        return {
            "parsed": False,
            "timeout": "EVENT:TIMEOUT" in (stdout or ""),
            "raw_stdout": stdout or "",
        }

    saw_start, saw_stop, saw_ack_addr, saw_ack_data, addr, data, tb_done, starts, stops, \
        sda_glitches, byte_count = m.groups()
    extra = {}
    if tb_done is not None:  # newer testbench; older RESULT lines omit these
        extra = {"tb_done": int(tb_done), "starts": int(starts), "stops": int(stops)}
    if sda_glitches is not None:  # even newer testbench; keep old lines parseable
        extra.update({"sda_glitches": int(sda_glitches), "bytes": int(byte_count)})
    data_events = [m.group(1).lower() for m in DATA_EVENT_RE.finditer(stdout or "")]
    if data_events:
        # Every ACKed data byte, in bus order. Older logs may not have these
        # events; the single ``data`` field remains the fallback.
        extra["data_bytes"] = data_events
    return {
        **extra,
        "parsed": True,
        "saw_start": int(saw_start),
        "saw_stop": int(saw_stop),
        "saw_ack_addr": int(saw_ack_addr),
        "saw_ack_data": int(saw_ack_data),
        "addr": addr.lower(),
        "data": data.lower(),
    }
