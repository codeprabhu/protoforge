"""Domain-neutral execution coordinator for ProtoForge.

The live loop is deterministic:

    generate -> structural -> compile -> hardware -> simulate -> judge -> repair

Repair is bounded and receives the specific failed stage/checks. The
specialist multi-agent flow is not wired in; it was removed together with
``agents/`` and ``core/task_graph.py`` (see ``docs/WORKFLOW.md`` phase 1 and
the git history on ``v1-stabilize``).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core import outcomes
from core.attempt_record import AttemptRecord, write_attempt_record
from core.schemas import Task, ValidationResult
from core.trace_schema import EventType
from domains.base import BaseDomainAdapter


def _emit(event_sink: Optional[Callable], event_type: EventType, **data: Any) -> None:
    """Best-effort event emission: tracing must never break a run."""
    if event_sink is None:
        return
    try:
        event_sink(event_type, data)
    except Exception:
        pass


def _snapshot(code: str, stage: str, failed_checks: Optional[List[str]] = None) -> Dict[str, Any]:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return {
        "sha256": digest,
        "candidate_hash": digest[:16],
        "code_len_chars": len(code),
        "stage": stage,
        "failed_checks": failed_checks or [],
    }


class Orchestrator:
    """Coordinate candidate generation, verification, and bounded repair."""

    def __init__(self, domain: BaseDomainAdapter):
        self.domain = domain

    def _repair(
        self,
        candidate,
        failed_results: List[ValidationResult],
        *,
        model: Optional[str],
        temperature: float,
        seed: Optional[int],
        repair_fn: Optional[Callable],
        retrieval: bool,
        patcher: bool = True,
        event_sink: Optional[Callable] = None,
    ):
        repaired = self.domain.repair_candidate(
            candidate,
            failed_results,
            model=model,
            temperature=temperature,
            seed=seed,
            repair_fn=repair_fn,
            retrieval=retrieval,
            patcher=patcher,
        )
        metadata = getattr(repaired, "metadata", {}) or {}
        _emit(
            event_sink,
            EventType.MODEL_INVOKED,
            model=model,
            repair=True,
            prompt_chars=len(metadata.get("prompt", "")),
            raw_output_chars=len(metadata.get("raw_output", "")),
            candidate_hash=hashlib.sha256(
                (getattr(repaired, "full_content", "") or "").encode("utf-8")
            ).hexdigest()[:16],
        )
        return repaired

    @staticmethod
    def _close_attempt(record: AttemptRecord, run_dir: Optional[str], outcome: str) -> None:
        """Finish and persist one attempt record (when a run directory exists)."""
        record.finish(outcome)
        if run_dir:
            write_attempt_record(run_dir, record)

    @staticmethod
    def _stage_attempt(candidate, attempt: int, run_dir: Optional[str], default_sim_out: str):
        """Write the attempt's source (and prompt) under its own run directory.

        Returns the simulator output path. Writing happens here, immediately
        before the gates run, so every attempt on disk matches what was
        actually compiled and simulated.
        """
        if not run_dir:
            return default_sim_out
        directory = Path(run_dir)
        directory.mkdir(parents=True, exist_ok=True)
        code_path = directory / f"attempt_{attempt}.v"
        code_path.write_text(candidate.full_content or "", encoding="utf-8")
        candidate.target_file = str(code_path)
        prompt = candidate.metadata.get("prompt")
        if prompt:
            (directory / f"attempt_{attempt}_prompt.txt").write_text(
                prompt, encoding="utf-8"
            )
        return str(directory / f"attempt_{attempt}.vvp")

    def execute_task(
        self,
        task: Task,
        max_repairs: int = 3,
        model: Optional[str] = None,
        seed: Optional[int] = None,
        temperature: float = 0.0,
        results_log_path: Optional[str] = None,
        run_dir: Optional[str] = None,
        retrieval: bool = True,
        patcher: bool = True,
        run_id: Optional[str] = None,
        event_sink: Optional[Callable] = None,
        # Injection hooks for mocking / tests
        compile_fn: Optional[Callable] = None,
        hardware_check_fn: Optional[Callable] = None,
        sim_fn: Optional[Callable] = None,
        judge_fn: Optional[Callable] = None,
        struct_check_fn: Optional[Callable] = None,
        generate_fn: Optional[Callable] = None,
        repair_fn: Optional[Callable] = None,
        on_success: Optional[Callable] = None,
        on_failure: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Execute one task through the domain adapter with bounded repair."""
        started = time.time()
        selected_model = model or "default"
        repair_budget = max(0, int(max_repairs))
        context = self.domain.build_context(task)
        if not task.requirements:
            task.requirements = self.domain.parse_requirements(context.get("spec_path"))

        log: Dict[str, Any] = {
            "timestamp": started,
            "task_id": task.task_id,
            "run_id": run_id or (Path(run_dir).name if run_dir else task.task_id),
            "run_dir": run_dir,
            "domain": self.domain.name,
            "model": selected_model,
            "temperature_policy": f"generation={temperature}; repair increases slightly per attempt",
            "generation_temperature": temperature,
            "seed": seed,
            "retrieval_enabled": retrieval,
            "max_repairs": repair_budget,
            "protocol": context.get(
                "protocol",
                {"address": context.get("address_hex", ""), "data": context.get("data_hex", "")},
            ),
            "rtl_memory_before_run": task.metadata.get(
                "rtl_memory_before_run", context.get("rtl_memory_before_run")
            ),
            "requirements": [r.req_id for r in task.requirements],
            "attempts": [],
        }

        # 1. Generation
        target_path = task.metadata.get("target_file", "generated/top_glue.v")
        try:
            candidate = self.domain.generate_candidate(
                task,
                context,
                out_path=target_path,
                model=selected_model,
                temperature=temperature,
                seed=seed,
                generate_fn=generate_fn,
                retrieval=retrieval,
                patcher=patcher,
            )
            task.candidate_patch = candidate
            metadata = getattr(candidate, "metadata", {}) or {}
            _emit(
                event_sink,
                EventType.MODEL_INVOKED,
                model=selected_model,
                repair=False,
                prompt_chars=len(metadata.get("prompt", "")),
                raw_output_chars=len(metadata.get("raw_output", "")),
                candidate_hash=hashlib.sha256(
                    (candidate.full_content or "").encode("utf-8")
                ).hexdigest()[:16],
            )
        except Exception as exc:  # model/runtime failure is a known outcome
            log["final"] = outcomes.GENERATION_ERROR
            log["generation_error"] = f"{type(exc).__name__}: {exc}"
            log["elapsed_sec"] = round(time.time() - started, 3)
            return self._finish(log, results_log_path)

        if not candidate.full_content or not candidate.full_content.strip():
            log["final"] = outcomes.GENERATION_ERROR
            log["generation_error"] = "Empty or missing candidate output after generation"
            log["elapsed_sec"] = round(time.time() - started, 3)
            return self._finish(log, results_log_path)

        # 2. Bounded verify/repair loop
        for attempt in range(repair_budget + 1):
            attempt_started = time.time()
            repair_temperature = min(0.15 + 0.15 * attempt, 0.45)
            code = candidate.full_content or ""
            sim_out = self._stage_attempt(
                candidate,
                attempt,
                run_dir,
                task.metadata.get("sim_out", "generated/top_sim.vvp"),
            )
            record = AttemptRecord.from_candidate(
                candidate,
                run_id=log["run_id"],
                attempt=attempt,
                model=selected_model,
                repair=attempt > 0,
            )
            record.started_at = attempt_started

            # Structural gate
            stage_started = time.time()
            if struct_check_fn:
                required = getattr(self.domain, "required_instances", [])
                struct_res_raw = struct_check_fn(code, required)
                struct_val = ValidationResult(
                    stage="structural",
                    passed=struct_res_raw.get("pass", False),
                    failed_checks=struct_res_raw.get("failed_checks", []),
                    metadata=struct_res_raw,
                )
            else:
                struct_val = self.domain.validate_structure(candidate)
            record.add_stage(struct_val, round(time.time() - stage_started, 6))
            _emit(
                event_sink, EventType.STAGE_FINISHED,
                stage="structural", passed=struct_val.passed, attempt=attempt,
                failed_checks=struct_val.failed_checks,
            )

            if not struct_val.passed:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "structural",
                    "success": False,
                    "failed_checks": struct_val.failed_checks,
                    **_snapshot(code, "structural", struct_val.failed_checks),
                })
                if attempt < repair_budget:
                    self._close_attempt(record, run_dir, "repaired")
                    candidate = self._repair(
                        candidate, [struct_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn, retrieval=retrieval,
                        patcher=patcher, event_sink=event_sink,
                    )
                    continue
                log["final"] = outcomes.STRUCTURAL_FAILED
                self._close_attempt(record, run_dir, outcomes.STRUCTURAL_FAILED)
                break

            # Compile, hardware, simulate
            test_kwargs = {
                "sim_out": sim_out,
                "spec_path": task.metadata.get("spec_path", context.get("spec_path")),
                "compile_fn": compile_fn,
                "hardware_check_fn": hardware_check_fn,
                "sim_fn": sim_fn,
            }
            val_results = self.domain.build_and_test(
                candidate,
                **{k: v for k, v in test_kwargs.items() if v is not None},
            )

            compile_val = next((r for r in val_results if r.stage == "compile"), None)
            if compile_val:
                record.add_stage(compile_val)
                _emit(
                    event_sink, EventType.STAGE_FINISHED,
                    stage="compile", passed=compile_val.passed, attempt=attempt,
                    failed_checks=compile_val.failed_checks,
                )
            if compile_val and not compile_val.passed:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "compile",
                    "success": False,
                    "stderr": compile_val.stderr,
                    **_snapshot(code, "compile"),
                })
                if compile_val.tool_missing:
                    log["final"] = outcomes.TOOL_MISSING
                    log["tool_error"] = compile_val.stderr
                    self._close_attempt(record, run_dir, outcomes.TOOL_MISSING)
                    break
                if attempt < repair_budget:
                    self._close_attempt(record, run_dir, "repaired")
                    candidate = self._repair(
                        candidate, [compile_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn, retrieval=retrieval,
                        patcher=patcher, event_sink=event_sink,
                    )
                    continue
                log["final"] = outcomes.COMPILE_FAILED
                self._close_attempt(record, run_dir, outcomes.COMPILE_FAILED)
                break

            hw_val = next((r for r in val_results if r.stage == "hardware"), None)
            if hw_val:
                record.add_stage(hw_val)
                _emit(
                    event_sink, EventType.STAGE_FINISHED,
                    stage="hardware", passed=hw_val.passed, attempt=attempt,
                    failed_checks=hw_val.failed_checks,
                )
            if hw_val and not hw_val.passed:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "hardware",
                    "success": False,
                    "failed_checks": hw_val.failed_checks,
                    "hardware": hw_val.metadata,
                    **_snapshot(code, "hardware"),
                })
                if hw_val.tool_missing:
                    log["final"] = outcomes.TOOL_MISSING
                    log["tool_error"] = hw_val.diagnostics
                    self._close_attempt(record, run_dir, outcomes.TOOL_MISSING)
                    break
                if attempt < repair_budget:
                    self._close_attempt(record, run_dir, "repaired")
                    candidate = self._repair(
                        candidate, [hw_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn, retrieval=retrieval,
                        patcher=patcher, event_sink=event_sink,
                    )
                    continue
                log["final"] = outcomes.HARDWARE_FAILED
                self._close_attempt(record, run_dir, outcomes.HARDWARE_FAILED)
                break

            sim_val = next((r for r in val_results if r.stage == "simulation"), None)
            if sim_val:
                record.add_stage(sim_val)
                _emit(
                    event_sink, EventType.STAGE_FINISHED,
                    stage="simulation", passed=sim_val.passed, attempt=attempt,
                    failed_checks=sim_val.failed_checks,
                )
            if sim_val and sim_val.tool_missing:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "simulation_tool_missing",
                    "success": False,
                    "stderr": sim_val.stderr,
                    **_snapshot(code, "simulation_tool_missing"),
                })
                log["final"] = outcomes.TOOL_MISSING
                log["tool_error"] = sim_val.stderr
                self._close_attempt(record, run_dir, outcomes.TOOL_MISSING)
                break

            # Protocol judge
            judge_kwargs = {"judge_fn": judge_fn} if judge_fn else {}
            verdict = self.domain.judge(val_results, **judge_kwargs)
            record.add_stage(verdict)
            _emit(
                event_sink, EventType.STAGE_FINISHED,
                stage="judge", passed=verdict.passed, attempt=attempt,
                failed_checks=verdict.failed_checks,
            )
            sim_passed = sim_val.passed if sim_val else False
            overall_pass = verdict.passed and sim_passed

            log["attempts"].append({
                "attempt": attempt,
                "stage": "simulate",
                "success": overall_pass,
                "failed_checks": verdict.failed_checks,
                "raw": sim_val.metadata.get("raw") if sim_val else {},
                "simulator_success": sim_passed,
                "simulator_stderr": sim_val.stderr[:2000] if sim_val else "",
                "hardware_success": hw_val.passed if hw_val else True,
                "hardware_stage": hw_val.stage if hw_val else "none",
                **_snapshot(code, "simulate", verdict.failed_checks),
            })

            if overall_pass:
                log["final"] = outcomes.PASS
                task.status = "completed"
                self._close_attempt(record, run_dir, outcomes.PASS)
                if on_success:
                    try:
                        artifact = on_success(
                            code,
                            model=selected_model,
                            metadata={"checks": verdict.metadata.get("raw", {})},
                        )
                        log["verified_example"] = str(artifact)
                    except Exception as exc:
                        log["on_success_error"] = f"{type(exc).__name__}: {exc}"
                break

            if attempt < repair_budget:
                self._close_attempt(record, run_dir, "repaired")
                candidate = self._repair(
                    candidate, [*val_results, verdict],
                    model=selected_model, temperature=repair_temperature,
                    seed=seed, repair_fn=repair_fn, retrieval=retrieval,
                    patcher=patcher, event_sink=event_sink,
                )
                continue

            log["final"] = outcomes.SIM_CHECKS_FAILED
            task.status = "failed"
            self._close_attempt(record, run_dir, outcomes.SIM_CHECKS_FAILED)
            if on_failure:
                try:
                    on_failure(code, selected_model, "simulate", verdict.failed_checks, verdict.diagnostics)
                except Exception as exc:
                    log["on_failure_error"] = f"{type(exc).__name__}: {exc}"
            break

        log["first_attempt_pass"] = bool(
            log["attempts"] and log["attempts"][0].get("success", False)
        )
        log["recovered_after_repair"] = bool(
            log.get("final") == outcomes.PASS and len(log["attempts"]) > 1
        )
        log["elapsed_sec"] = round(time.time() - started, 3)
        return self._finish(log, results_log_path)

    @staticmethod
    def _summary(log: Dict[str, Any]) -> Dict[str, Any]:
        """One-line index entry pointing at the run directory and full record."""
        attempts = log.get("attempts") or []
        # Why the run ended must be on disk even when no attempt was staged:
        # without it a run that never reached the model is indistinguishable
        # from a model that produced nothing.
        error = log.get("generation_error") or log.get("tool_error")
        return {
            "run_id": log.get("run_id"),
            "timestamp": log.get("timestamp"),
            "task_id": log.get("task_id"),
            "domain": log.get("domain"),
            "model": log.get("model"),
            "seed": log.get("seed"),
            "temperature": log.get("generation_temperature"),
            "retrieval_enabled": log.get("retrieval_enabled"),
            "final": log.get("final"),
            "error": error,
            "first_attempt_pass": bool(log.get("first_attempt_pass", False)),
            "recovered_after_repair": bool(log.get("recovered_after_repair", False)),
            "attempts": len(attempts),
            "first_attempt_hash": attempts[0].get("candidate_hash") if attempts else None,
            "elapsed_sec": log.get("elapsed_sec"),
            "run_dir": log.get("run_dir"),
        }

    @classmethod
    def _finish(cls, log: Dict[str, Any], results_log_path: Optional[str]) -> Dict[str, Any]:
        if results_log_path:
            os.makedirs(os.path.dirname(results_log_path) or ".", exist_ok=True)
            with open(results_log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(cls._summary(log), sort_keys=True) + "\n")
        return log
