"""Minimal parser for the v1 I2C transaction contract."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class I2CSpec:
    slave_address: int
    address_byte: int
    data_byte: int

    @property
    def address_hex(self) -> str:
        return f"{self.address_byte:02x}"

    @property
    def data_hex(self) -> str:
        return f"{self.data_byte:02x}"


def parse_i2c_spec(path: str) -> I2CSpec:
    text = open(path, encoding="utf-8").read()
    address = re.search(r"slave\s+address\s+0x([0-9a-fA-F]+)", text, re.I)
    data = re.search(r"writes\s+ONE\s+byte,\s*`?0x([0-9a-fA-F]+)`?", text, re.I)
    if not address or not data:
        raise ValueError("I2C spec must declare a slave address and one write data byte")
    slave_address = int(address.group(1), 16)
    data_byte = int(data.group(1), 16)
    if slave_address > 0x7F or data_byte > 0xFF:
        raise ValueError("I2C spec address/data values are out of range")
    return I2CSpec(slave_address, slave_address << 1, data_byte)
