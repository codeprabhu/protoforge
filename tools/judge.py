def judge(
    result: dict,
    expected_addr: str = "a0",
    expected_data: str = "3c",
    expected_data_bytes: int = 1,
    expected_data_list: list[str] | None = None,
) -> dict:
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

    if expected_data_list is None:
        expected_data_list = [expected_data]
    expected_data_list = [byte.lower() for byte in expected_data_list]
    expected_data_bytes = max(expected_data_bytes, len(expected_data_list))

    checks = {
        "start": result["saw_start"] == 1,
        "address_ack": result["saw_ack_addr"] == 1,
        "data_ack": result["saw_ack_data"] == 1,
        "stop": result["saw_stop"] == 1,
        "correct_address": result.get("addr", "").lower() == expected_addr.lower(),
    }
    actual_data = result.get("data_bytes")
    if actual_data is not None:
        checks["correct_data"] = [byte.lower() for byte in actual_data] == expected_data_list
    else:
        checks["correct_data"] = result.get("data", "").lower() == expected_data_list[-1]
    # Extended checks, only when the testbench reported them (older RESULT
    # lines do not carry these fields, so they are skipped rather than failed).
    if "tb_done" in result:
        checks["tb_done"] = result["tb_done"] == 1
        checks["single_start"] = result["starts"] == 1
        checks["single_stop"] = result["stops"] == 1
    if "sda_glitches" in result:
        # Any SDA transition while SCL is high that is not a legal START or
        # STOP means the master changed data during a clock-high phase.
        checks["sda_stable"] = result["sda_glitches"] == 0
    if "bytes" in result:
        # Address byte + the expected number of data bytes, no more.
        checks["byte_count"] = result["bytes"] == expected_data_bytes + 1
    failed = [k for k, v in checks.items() if not v]
    return {"pass": len(failed) == 0, "failed_checks": failed, "raw": result}


FAILURE_EXPLANATIONS = {
    "start": "No START condition (SDA falling while SCL is high) was observed on the bus.",
    "address_ack": "No ACK was seen after the address byte -- the slave never pulled SDA low on the 9th clock of the address phase.",
    "data_ack": "No ACK was seen after the data byte -- the slave never pulled SDA low on the 9th clock of the data phase.",
    "stop": "No STOP condition (SDA rising while SCL is high) was observed on the bus -- the transaction never terminated correctly.",
    "correct_address": "The address byte captured on the bus did not match the expected 0xA0 (0x50 write).",
    "correct_data": "The data byte captured on the bus did not match the required 0x3C payload.",
    "tb_done": "tb_done was never asserted -- the spec requires it to go high once the STOP condition has been issued.",
    "single_start": "The bus showed more than one START condition (or none); v1 requires exactly one START and no repeated START.",
    "single_stop": "The bus showed more than one STOP condition (or none); v1 requires exactly one STOP at the end of the transaction.",
    "sda_stable": "SDA changed while SCL was high outside a legal START or STOP -- data must only change while SCL is low.",
    "byte_count": "The transaction did not contain exactly one address byte followed by the expected number of data bytes.",
    "simulation_unparseable": "The simulation did not produce a valid RESULT: line -- it likely hung or tb_done was never asserted.",
}


def explain_failures(
    failed_checks,
    expected_addr: str | None = None,
    expected_data: str | None = None,
    expected_data_list: list[str] | None = None,
) -> str:
    """Human-readable, error-specific feedback for repair prompts.

    ``expected_addr``/``expected_data`` let the explanation name the actual
    expected values instead of the v1 defaults when plusargs select another
    transaction.
    """
    dynamic = {}
    if expected_addr:
        dynamic["correct_address"] = (
            "The address byte captured on the bus did not match the expected "
            f"0x{expected_addr.upper()}."
        )
    if expected_data_list:
        rendered = ", ".join(f"0x{byte.upper()}" for byte in expected_data_list)
        dynamic["correct_data"] = (
            "The data bytes captured on the bus did not match the required "
            f"[{rendered}] payload."
        )
    elif expected_data:
        dynamic["correct_data"] = (
            "The data byte captured on the bus did not match the required "
            f"0x{expected_data.upper()} payload."
        )
    return " ".join(
        dynamic.get(check) or FAILURE_EXPLANATIONS.get(check, check)
        for check in failed_checks
    )
