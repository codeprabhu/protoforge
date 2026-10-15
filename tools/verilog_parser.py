"""
Lightweight Verilog module/port extractor.

Regex-based (no pyverilog dependency) per devices/CONTRACT.md's constraint
that devices are single, ANSI-style-port modules. Not a general Verilog
parser -- it exists to give the LLM prompt an exact, structured interface
description instead of raw source text it might misread.
"""

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Port:
    direction: str      # "input" | "output" | "inout"
    width: str          # "" for 1-bit, else "[7:0]" style string
    name: str

    def as_str(self) -> str:
        w = f" {self.width}" if self.width else ""
        return f"{self.direction}{w} {self.name}"


@dataclass
class ModuleInfo:
    name: str
    ports: List[Port] = field(default_factory=list)
    raw_source: str = ""

    def interface_summary(self) -> str:
        lines = [f"module {self.name}("]
        for p in self.ports:
            lines.append(f"    {p.as_str()},")
        lines.append(");")
        return "\n".join(lines)


MODULE_HEADER_RE = re.compile(
    r"module\s+(\w+)\s*(?:#\s*\([\s\S]*?\))?\s*\(([\s\S]*?)\)\s*;",
    re.IGNORECASE,
)

PORT_RE = re.compile(
    r"\b(input|output|inout)\b\s*(reg|wire)?\s*(\[[^\]]+\])?\s*([A-Za-z_]\w*)",
    re.IGNORECASE,
)


def parse_verilog_file(filepath: str) -> ModuleInfo:
    with open(filepath, "r") as f:
        return parse_verilog_source(f.read())


def parse_verilog_source(source: str) -> ModuleInfo:
    matches = list(MODULE_HEADER_RE.finditer(source))
    if not matches:
        raise ValueError(
            "Could not find a `module name (...);` header. "
            "Only ANSI-style port declarations are supported (see devices/CONTRACT.md)."
        )

    if len(matches) != 1:
        raise ValueError("A device file must contain exactly one module")

    if re.search(r"\bgenerate\b", source, re.IGNORECASE):
        raise ValueError("Device files must not contain generate blocks")

    match = matches[0]
    name = match.group(1)
    port_block = match.group(2)

    ports = []
    for direction, _regtype, width, pname in PORT_RE.findall(port_block):
        ports.append(Port(direction=direction.lower(), width=width or "", name=pname))

    if not ports:
        raise ValueError(
            f"Found module '{name}' but couldn't extract any ports. "
            "Check that ports are declared ANSI-style inside the parentheses."
        )

    port_names = {p.name for p in ports}
    if not {"clk", "rst_n"}.issubset(port_names):
        raise ValueError(f"Device {name} must expose clk and rst_n ports")

    return ModuleInfo(name=name, ports=ports, raw_source=source)
