"""Per-attempt audit records for reproducible pipeline runs.

Each attempt is written to ``results/runs/<run_id>/attempt_<n>.json`` next to
the exact Verilog that was compiled and simulated. Together with ``run.json``
and the per-attempt ``.v`` files this is enough to replay the gates later
(``tools/replay.py``) without the model or the original process.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.schemas import ValidationResult


class StageRecord(BaseModel):
    """One gate's verdict for one attempt."""

    stage: str
    passed: bool
    tool_missing: bool = False
    failed_checks: List[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    diagnostics: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    duration_sec: Optional[float] = None

    @classmethod
    def from_result(cls, result: ValidationResult, duration_sec: float | None = None) -> "StageRecord":
        return cls(
            stage=result.stage,
            passed=result.passed,
            tool_missing=result.tool_missing,
            failed_checks=list(result.failed_checks),
            stdout=result.stdout,
            stderr=result.stderr,
            diagnostics=result.diagnostics,
            metadata=dict(result.metadata),
            duration_sec=duration_sec,
        )


class AttemptRecord(BaseModel):
    """Everything needed to audit or replay a single candidate attempt."""

    run_id: str = ""
    attempt: int
    outcome: str = ""
    repair: bool = False
    model: str = ""
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None
    duration_sec: Optional[float] = None

    prompt: str = ""
    raw_output: str = ""
    cleaned_code: str = ""
    final_code: str = ""
    code_sha256: str = ""
    model_metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieval: Dict[str, Any] = Field(default_factory=dict)
    # IR path only: the table the model wrote and the protocol boilerplate the
    # generator filled before compiling. Empty on the free-form RTL path.
    fsm_json: str = ""
    enrichment: List[str] = Field(default_factory=list)
    stages: List[StageRecord] = Field(default_factory=list)
    # Structured explanation (generation or repair) — may be absent or unparseable.
    explanation: Optional[Dict[str, Any]] = None

    @classmethod
    def from_candidate(
        cls,
        candidate: Any,
        *,
        run_id: str,
        attempt: int,
        model: str,
        repair: bool = False,
    ) -> "AttemptRecord":
        metadata = getattr(candidate, "metadata", {}) or {}
        code = getattr(candidate, "full_content", "") or ""
        return cls(
            run_id=run_id,
            attempt=attempt,
            repair=repair,
            model=model,
            prompt=metadata.get("prompt", ""),
            raw_output=metadata.get("raw_output", ""),
            cleaned_code=metadata.get("cleaned_code", ""),
            final_code=code,
            code_sha256=hashlib.sha256(code.encode("utf-8")).hexdigest(),
            model_metadata=metadata.get("model_metadata", {}) or {},
            retrieval=metadata.get("retrieval", {}) or {},
            fsm_json=metadata.get("fsm_json", "") or "",
            enrichment=list(metadata.get("enrichment", []) or []),
            explanation=metadata.get("explanation"),
        )

    def add_stage(self, result: ValidationResult, duration_sec: float | None = None) -> None:
        self.stages.append(StageRecord.from_result(result, duration_sec))

    def finish(self, outcome: str) -> None:
        self.outcome = outcome
        self.finished_at = time.time()
        self.duration_sec = round(self.finished_at - self.started_at, 6)

    def stage(self, name: str) -> Optional[StageRecord]:
        return next((stage for stage in self.stages if stage.stage == name), None)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n"


def write_attempt_record(run_dir: str | Path, record: AttemptRecord) -> Path:
    """Persist one attempt record and its raw simulator/compiler streams."""
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"attempt_{record.attempt}.json"
    path.write_text(record.to_json(), encoding="utf-8")

    for stage_name, suffix in (("compile", "compile_stderr"), ("simulation", "sim_stdout")):
        stage = record.stage(stage_name)
        if stage is None:
            continue
        text = stage.stderr if stage_name == "compile" else stage.stdout
        if text:
            (directory / f"attempt_{record.attempt}_{suffix}.txt").write_text(
                text, encoding="utf-8"
            )
    return path
