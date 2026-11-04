"""Explicit run configuration and per-run filesystem layout.

Phase 2 removes import-time configuration and cross-run file handoff: a run
reads its environment once in :meth:`Config.from_env`, then every path it
touches lives under a unique ``results/runs/<run_id>/`` directory.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from agent import ollama_client

DEFAULT_DEVICE_PATHS = ("devices/device_sensor.v", "devices/device_controller.v")
DEFAULT_SPEC = "protocols/i2c/spec.md"
DEFAULT_TB = "protocols/i2c/testbench_i2c.v"
DEFAULT_OUTPUT_ROOT = "results/runs"
DEFAULT_RESULTS_LOG = "results/runs.jsonl"
DEFAULT_TRACE_DIR = "results/traces"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Everything one pipeline run needs, with no hidden globals."""

    device_paths: tuple[str, ...] = DEFAULT_DEVICE_PATHS
    spec_path: str = DEFAULT_SPEC
    tb_path: str = DEFAULT_TB
    max_repairs: int = 2
    model: str = ollama_client.DEFAULT_MODEL
    seed: int = ollama_client.DEFAULT_SEED
    temperature: float = ollama_client.DEFAULT_TEMPERATURE
    retrieval: bool = True
    output_root: str = DEFAULT_OUTPUT_ROOT
    results_log: str = DEFAULT_RESULTS_LOG
    trace_dir: str = DEFAULT_TRACE_DIR
    target_name: str = "top_glue.v"

    def __post_init__(self) -> None:
        if not self.device_paths:
            raise ValueError("Config.device_paths must contain at least one Verilog device")
        if self.max_repairs < 0:
            raise ValueError("Config.max_repairs must be >= 0")

    @classmethod
    def from_env(cls, **overrides: Any) -> "Config":
        """Build a config from PROTOFORGE_* variables, then apply overrides."""
        raw_devices = os.getenv("PROTOFORGE_DEVICE_PATHS", ",".join(DEFAULT_DEVICE_PATHS))
        devices = tuple(part.strip() for part in raw_devices.split(",") if part.strip())
        values: dict[str, Any] = {
            "device_paths": devices or DEFAULT_DEVICE_PATHS,
            "spec_path": os.getenv("PROTOFORGE_SPEC", DEFAULT_SPEC),
            "tb_path": os.getenv("PROTOFORGE_TB", DEFAULT_TB),
            "max_repairs": int(os.getenv("PROTOFORGE_MAX_REPAIRS", "2")),
            "model": os.getenv("PROTOFORGE_MODEL", ollama_client.DEFAULT_MODEL),
            "seed": int(os.getenv("PROTOFORGE_SEED", str(ollama_client.DEFAULT_SEED))),
            "temperature": float(
                os.getenv("PROTOFORGE_TEMPERATURE", str(ollama_client.DEFAULT_TEMPERATURE))
            ),
            "retrieval": not _env_flag("PROTOFORGE_NO_RETRIEVAL", False),
            "output_root": os.getenv("PROTOFORGE_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT),
            "results_log": os.getenv("PROTOFORGE_RESULTS_LOG", DEFAULT_RESULTS_LOG),
            "trace_dir": os.getenv("PROTOFORGE_TRACE_DIR", DEFAULT_TRACE_DIR),
        }
        values.update(overrides)
        return cls(**values)

    def with_overrides(self, **overrides: Any) -> "Config":
        return replace(self, **overrides)

    @property
    def required_instances(self) -> tuple[str, ...]:
        """Module names of every configured device, in interface order."""
        from tools.verilog_parser import parse_verilog_file

        return tuple(parse_verilog_file(path).name for path in self.device_paths)

    def as_dict(self) -> dict[str, Any]:
        data = {
            "device_paths": list(self.device_paths),
            "spec_path": self.spec_path,
            "tb_path": self.tb_path,
            "max_repairs": self.max_repairs,
            "model": self.model,
            "seed": self.seed,
            "temperature": self.temperature,
            "retrieval": self.retrieval,
            "output_root": self.output_root,
            "results_log": self.results_log,
            "trace_dir": self.trace_dir,
            "target_name": self.target_name,
        }
        return data


def new_run_id() -> str:
    """Sortable, collision-resistant identifier for one run directory."""
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RunPaths:
    """All artifact paths for a single run, confined to its own directory."""

    run_id: str
    run_dir: Path

    @classmethod
    def create(
        cls, output_root: str | Path = DEFAULT_OUTPUT_ROOT, run_id: Optional[str] = None
    ) -> "RunPaths":
        run_id = run_id or new_run_id()
        run_dir = Path(output_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return cls(run_id=run_id, run_dir=run_dir)

    def attempt_code(self, attempt: int) -> Path:
        return self.run_dir / f"attempt_{attempt}.v"

    def attempt_vvp(self, attempt: int) -> Path:
        return self.run_dir / f"attempt_{attempt}.vvp"

    def attempt_prompt(self, attempt: int) -> Path:
        return self.run_dir / f"attempt_{attempt}_prompt.txt"

    def attempt_record(self, attempt: int) -> Path:
        return self.run_dir / f"attempt_{attempt}.json"

    def run_header(self) -> Path:
        return self.run_dir / "run.json"
