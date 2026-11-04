"""Build a protocol-spec file for one benchmark case.

The benchmark cases live in ``benchmarks/cases.json``. Each case is a plain
transaction description; this module turns it into the markdown spec that the
existing I2C parser (``tools.i2c_spec``) and the model prompt already consume.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks/cases.json"

_COUNT_WORDS = {
    1: "ONE",
    2: "TWO",
    3: "THREE",
    4: "FOUR",
    5: "FIVE",
    6: "SIX",
    7: "SEVEN",
    8: "EIGHT",
}


def _hex(value: str | int) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def load_cases(path: str | Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    seen: set[str] = set()
    for case in cases:
        required = {"id", "address", "data", "devices"}
        missing = required - set(case)
        if missing:
            raise ValueError(f"case {case.get('id', '?')} is missing {sorted(missing)}")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id: {case['id']}")
        seen.add(case["id"])
        address = _hex(case["address"])
        data = [_hex(value) for value in case["data"]]
        if not 0 <= address <= 0x7F:
            raise ValueError(f"case {case['id']}: address out of 7-bit range")
        if not data:
            raise ValueError(f"case {case['id']}: at least one data byte is required")
        if any(not 0 <= byte <= 0xFF for byte in data):
            raise ValueError(f"case {case['id']}: data byte out of 8-bit range")
        if not case["devices"]:
            raise ValueError(f"case {case['id']}: at least one device is required")
    return list(cases)


def case_device_paths(case: dict[str, Any], root: str | Path = ROOT) -> list[str]:
    """Resolve device module names to ``devices/<name>.v`` paths."""
    base = Path(root) / "devices"
    return [str(base / f"{name}.v") for name in case["devices"]]


def case_spec_text(case: dict[str, Any]) -> str:
    """Render a markdown spec that ``parse_i2c_spec`` accepts."""
    data = [_hex(value) for value in case["data"]]
    count_word = _COUNT_WORDS.get(len(data), str(len(data)))
    rendered = ", ".join(f"`0x{byte:02X}`" for byte in data)
    devices = ", ".join(case["devices"])
    note = case.get("notes", "")
    lines = [
        f"# I2C benchmark case {case['id']}",
        "",
        (
            f"Master writes {count_word} byte{'s' if len(data) != 1 else ''}, "
            f"{rendered}, to slave address 0x{_hex(case['address']):02X}."
        ),
        "",
        f"Device instances: {devices}.",
        "The fixed top_glue port list and open-drain rules are unchanged.",
    ]
    if note:
        lines.extend(["", f"Notes: {note}"])
    return "\n".join(lines) + "\n"


def write_case_spec(case: dict[str, Any], out_dir: str | Path) -> Path:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{case['id']}.md"
    path.write_text(case_spec_text(case), encoding="utf-8")
    return path


def write_specs(cases: Iterable[dict[str, Any]], out_dir: str | Path) -> list[Path]:
    return [write_case_spec(case, out_dir) for case in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="benchmark cases JSON")
    parser.add_argument("--case", default=None, help="only this case id")
    parser.add_argument("--out-dir", default="benchmarks/specs", help="spec output directory")
    parser.add_argument("--stdout", action="store_true", help="print the spec instead of writing")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error(f"unknown case id: {args.case}")

    if args.stdout:
        for case in cases:
            print(case_spec_text(case), end="")
        return 0

    paths = write_specs(cases, args.out_dir)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
