import subprocess
import re


def run_simulation(executable: str) -> dict:
    try:
        result = subprocess.run(
            ["vvp", executable],
            capture_output=True,
            text=True,
            timeout=5
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Simulation timeout"
        }


RESULT_RE = re.compile(
    r"RESULT:saw_start=(\d) saw_stop=(\d) saw_ack_addr=(\d) saw_ack_data=(\d) "
    r"addr=([0-9a-fA-F]+) data=([0-9a-fA-F]+)"
)


def parse_result_line(stdout: str) -> dict:
    """
    Parses the RESULT:/EVENT: lines emitted by protocols/i2c/testbench_i2c.v
    into a structured dict. This is the boundary between raw simulator text
    and everything downstream (judge(), repair prompts, logging).
    """
    m = RESULT_RE.search(stdout)
    if not m:
        return {"parsed": False, "timeout": "EVENT:TIMEOUT" in stdout, "raw_stdout": stdout}

    saw_start, saw_stop, saw_ack_addr, saw_ack_data, addr, data = m.groups()
    return {
        "parsed": True,
        "saw_start": int(saw_start),
        "saw_stop": int(saw_stop),
        "saw_ack_addr": int(saw_ack_addr),
        "saw_ack_data": int(saw_ack_data),
        "addr": addr.lower(),
        "data": data.lower(),
    }
