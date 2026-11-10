"""Run an Icarus simulation and parse the stable machine-readable result.

P0-S04: Per-run nonce. The testbench prints RESULT:NONCE=<hex> ... and the
parser accepts ONLY lines with the correct nonce, taking the LAST match.
This defeats the F-01 forgery where the DUT printed fake RESULT lines.
"""

import re
import subprocess

# Legacy regex (no nonce) for backwards compatibility
RESULT_RE_LEGACY = re.compile(
    r"RESULT:saw_start=(\d) saw_stop=(\d) saw_ack_addr=(\d) saw_ack_data=(\d) "
    r"addr=([0-9a-fA-F]+) data=([0-9a-fA-F]+)"
    r"(?: tb_done=(\d) starts=(\d+) stops=(\d+))?"
    r"(?: sda_glitches=(\d+) bytes=(\d+))?"
)

# New regex with nonce - capture the nonce in group 1
NONCE_RE = re.compile(r"RESULT:NONCE=([0-9a-fA-F]{16})")
RESULT_RE = re.compile(
    r"RESULT:NONCE=([0-9a-fA-F]{16}) "
    r"saw_start=(\d) saw_stop=(\d) saw_ack_addr=(\d) saw_ack_data=(\d) "
    r"addr=([0-9a-fA-F]+) data=([0-9a-fA-F]+)"
    r"(?: tb_done=(\d) starts=(\d+) stops=(\d+))?"
    r"(?: sda_glitches=(\d+) bytes=(\d+))?"
)
DATA_EVENT_RE = re.compile(r"EVENT:DATA:([0-9a-fA-F]{2}):")


def run_simulation(executable: str, timeout: float = 8.0, plusargs=None, nonce: str | None = None) -> dict:
    """Run vvp; always return a result dictionary, including on success.

    If ``nonce`` is provided, it is passed as +NONCE=<hex> and the parser
    will only accept RESULT lines carrying that nonce (taking the last one).
    """
    args = ["vvp", executable, *(plusargs or [])]
    if nonce:
        args.append(f"+NONCE={nonce}")
    try:
        result = subprocess.run(
            args,
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


def _parse_with_nonce(stdout: str, nonce: str | None) -> dict | None:
    """Return the parsed RESULT dict for the LAST line matching ``nonce``.

    If ``nonce`` is None, fall back to the legacy parser (first match).
    """
    if nonce is None:
        # Legacy mode: first match wins (for backwards compatibility with
        # old run directories that lack the nonce).
        m = RESULT_RE_LEGACY.search(stdout or "")
        if not m:
            return None
        return _parse_legacy_match(m, stdout)

    # Nonce mode: collect all matching lines, take the last.
    matches = list(RESULT_RE.finditer(stdout or ""))
    for m in reversed(matches):
        if m.group(1).lower() == nonce.lower():
            return _parse_nonce_match(m, stdout)
    return None


def _parse_nonce_match(m: re.Match, stdout: str) -> dict:
    nonce, saw_start, saw_stop, saw_ack_addr, saw_ack_data, addr, data, tb_done, starts, stops, \
        sda_glitches, byte_count = m.groups()
    extra = {}
    if tb_done is not None:
        extra = {"tb_done": int(tb_done), "starts": int(starts), "stops": int(stops)}
    if sda_glitches is not None:
        extra.update({"sda_glitches": int(sda_glitches), "bytes": int(byte_count)})
    data_events = [m.group(1).lower() for m in DATA_EVENT_RE.finditer(stdout or "")]
    if data_events:
        extra["data_bytes"] = data_events
    return {
        **extra,
        "parsed": True,
        "authenticated": True,
        "saw_start": int(saw_start),
        "saw_stop": int(saw_stop),
        "saw_ack_addr": int(saw_ack_addr),
        "saw_ack_data": int(saw_ack_data),
        "addr": addr.lower(),
        "data": data.lower(),
    }


def _parse_legacy_match(m: re.Match, stdout: str) -> dict:
    saw_start, saw_stop, saw_ack_addr, saw_ack_data, addr, data, tb_done, starts, stops, \
        sda_glitches, byte_count = m.groups()
    extra = {}
    if tb_done is not None:
        extra = {"tb_done": int(tb_done), "starts": int(starts), "stops": int(stops)}
    if sda_glitches is not None:
        extra.update({"sda_glitches": int(sda_glitches), "bytes": int(byte_count)})
    data_events = [m.group(1).lower() for m in DATA_EVENT_RE.finditer(stdout or "")]
    if data_events:
        extra["data_bytes"] = data_events
    return {
        **extra,
        "parsed": True,
        "authenticated": False,
        "saw_start": int(saw_start),
        "saw_stop": int(saw_stop),
        "saw_ack_addr": int(saw_ack_addr),
        "saw_ack_data": int(saw_ack_data),
        "addr": addr.lower(),
        "data": data.lower(),
    }


def parse_result_line(stdout: str, nonce: str | None = None) -> dict:
    """Parse the RESULT line emitted by protocols/i2c/testbench_i2c.v.

    With a nonce (P0-S04): only the LAST line with that nonce is accepted.
    Without a nonce: first match wins (legacy compatibility).
    """
    parsed = _parse_with_nonce(stdout, nonce)
    if parsed is not None:
        return parsed

    return {
        "parsed": False,
        "timeout": "EVENT:TIMEOUT" in (stdout or ""),
        "raw_stdout": stdout or "",
    }
