from agent.rtl_generator import _specialize_protocol_prompt
from tools.i2c_spec import parse_i2c_spec


def test_protocol_spec_drives_address_and_payload_literals(tmp_path):
    spec = tmp_path / "alt.md"
    spec.write_text("Master writes ONE byte, `0x5A`, to slave address 0x2A.\n")
    parsed = parse_i2c_spec(str(spec))
    assert parsed.address_hex == "54"
    assert parsed.data_hex == "5a"
    prompt = _specialize_protocol_prompt("addr 8'hA0 data 8'h3C", parsed.address_hex, parsed.data_hex)
    assert prompt == "addr 8'h54 data 8'h5A"
