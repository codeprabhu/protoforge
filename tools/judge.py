def judge(result: dict, expected_addr: str = "a0") -> dict:
    """
    Turns a parsed simulation result (from simulation_tool.parse_result_line)
    into a pass/fail verdict plus which specific I2C primitive(s) failed.
    This is what makes repair prompts semantic ("no STOP condition was
    observed") instead of just a raw compiler error.
    """
    if not result.get("parsed"):
        reason = "simulation timed out before tb_done" if result.get("timeout") else \
                 "simulation output did not match the expected RESULT: line format"
        return {"pass": False, "failed_checks": ["simulation_unparseable"], "reason": reason}

    checks = {
        "start": result["saw_start"] == 1,
        "address_ack": result["saw_ack_addr"] == 1,
        "data_ack": result["saw_ack_data"] == 1,
        "stop": result["saw_stop"] == 1,
        "correct_address": result.get("addr", "").lower() == expected_addr.lower(),
    }
    failed = [k for k, v in checks.items() if not v]
    return {"pass": len(failed) == 0, "failed_checks": failed, "raw": result}


FAILURE_EXPLANATIONS = {
    "start": "No START condition (SDA falling while SCL is high) was observed on the bus.",
    "address_ack": "No ACK was seen after the address byte -- the slave never pulled SDA low on the 9th clock of the address phase.",
    "data_ack": "No ACK was seen after the data byte -- the slave never pulled SDA low on the 9th clock of the data phase.",
    "stop": "No STOP condition (SDA rising while SCL is high) was observed on the bus -- the transaction never terminated correctly.",
    "correct_address": "The address byte captured on the bus did not match the expected 0xA0 (0x50 write).",
    "simulation_unparseable": "The simulation did not produce a valid RESULT: line -- it likely hung or tb_done was never asserted.",
}


def explain_failures(failed_checks) -> str:
    return " ".join(FAILURE_EXPLANATIONS.get(c, c) for c in failed_checks)
