"""Minimal parser for the I2C write-transaction contract.

The v1 spec writes one data byte. The parser also accepts a multi-byte write
so the same oracle can evaluate a harder task; ``data_byte`` and ``data_hex``
keep their old one-byte meaning, while ``data_bytes``/``data_hexes`` expose the
whole payload.
"""

from dataclasses import dataclass
import re


_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8,
}
_WRITE_RE = re.compile(r"writes\s+(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|\d+)\s+bytes?\b([^\n]*)", re.I)


@dataclass(frozen=True)
class I2CSpec:
    slave_address: int
    address_byte: int
    data_byte: int
    extra_data_bytes: tuple[int, ...] = ()

    @property
    def data_bytes(self) -> tuple[int, ...]:
        return (self.data_byte, *self.extra_data_bytes)

    @property
    def nbytes(self) -> int:
        return 1 + len(self.extra_data_bytes)

    @property
    def address_hex(self) -> str:
        return f"{self.address_byte:02x}"

    @property
    def data_hex(self) -> str:
        return f"{self.data_byte:02x}"

    @property
    def data_hexes(self) -> list[str]:
        return [f"{byte:02x}" for byte in self.data_bytes]


def _parse_byte_count(token: str) -> int:
    token = token.lower()
    if token.isdigit():
        count = int(token)
    else:
        count = _COUNT_WORDS[token]
    if count < 1:
        raise ValueError("I2C spec must request at least one data byte")
    return count


def parse_i2c_spec(path: str) -> I2CSpec:
    text = open(path, encoding="utf-8").read()
    address = re.search(r"slave\s+address\s+0x([0-9a-fA-F]+)", text, re.I)
    write = _WRITE_RE.search(text)
    if not address or not write:
        raise ValueError(
            "I2C spec must declare a slave address and a write payload "
            "(e.g. 'writes ONE byte, 0x3C, to slave address 0x50')"
        )

    slave_address = int(address.group(1), 16)
    count = _parse_byte_count(write.group(1))
    # Only look at the payload clause, not the address that follows it.
    clause = re.split(r"\bto\s+slave\s+address\b", write.group(2), flags=re.I)[0]
    values = [int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]+)", clause)]
    if len(values) != count:
        raise ValueError(
            f"I2C spec says {count} data byte(s) but declares {len(values)} value(s)"
        )
    if slave_address > 0x7F or any(value > 0xFF for value in values):
        raise ValueError("I2C spec address/data values are out of range")

    return I2CSpec(
        slave_address=slave_address,
        address_byte=slave_address << 1,
        data_byte=values[0],
        extra_data_bytes=tuple(values[1:]),
    )
