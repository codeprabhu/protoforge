from agent.rtl_generator import _specialize_protocol_prompt
from tools.i2c_spec import parse_i2c_spec


def test_protocol_spec_drives_address_and_payload_literals(tmp_path):
    spec = tmp_path / "alt.md"
    spec.write_text("Master writes ONE byte, `0x5A`, to slave address 0x2A.\n")
    parsed = parse_i2c_spec(str(spec))
    assert parsed.address_hex == "54"
    assert parsed.data_hex == "5a"
    assert parsed.data_bytes == (0x5A,)
    assert parsed.nbytes == 1
    prompt = _specialize_protocol_prompt("addr 8'hA0 data 8'h3C", parsed.address_hex, parsed.data_hex)
    assert prompt == "addr 8'h54 data 8'h5A"


def test_two_byte_spec_exposes_the_full_payload(tmp_path):
    spec = tmp_path / "two.md"
    spec.write_text("Master writes TWO bytes, `0x11`, `0x22`, to slave address 0x50.\n")
    parsed = parse_i2c_spec(str(spec))
    assert parsed.data_bytes == (0x11, 0x22)
    assert parsed.data_hexes == ["11", "22"]
    assert parsed.nbytes == 2
    # The old single-byte views remain the first byte for compatibility.
    assert parsed.data_byte == 0x11
    assert parsed.data_hex == "11"


def test_spec_rejects_a_payload_count_mismatch(tmp_path):
    spec = tmp_path / "bad.md"
    spec.write_text("Master writes TWO bytes, `0x11`, to slave address 0x50.\n")
    try:
        parse_i2c_spec(str(spec))
    except ValueError as exc:
        assert "2 data byte" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a mismatched payload count must be rejected")


def test_multi_byte_prompt_names_the_payload_constants():
    """The model must know which names the deterministic wrapper declares."""
    prompt = _specialize_protocol_prompt(
        "addr 8'hA0 data 8'h3C", "50", "11", ["11", "22"]
    )
    assert "Multi-byte payload contract:" in prompt
    assert "localparam DATA_BYTE1 = 8'h11;" in prompt
    assert "localparam DATA_BYTE2 = 8'h22;" in prompt
    # Single-byte prompts must not gain the multi-byte block.
    single = _specialize_protocol_prompt("addr 8'hA0", "50", "11", ["11"])
    assert "Multi-byte payload contract:" not in single
