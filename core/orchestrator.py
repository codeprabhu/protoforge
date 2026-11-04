"""Domain-neutral execution coordinator for ProtoForge.

The live loop is deterministic:

    generate -> structural -> compile -> hardware -> simulate -> judge -> repair

Repair is bounded and receives the specific failed stage/checks. The
specialist multi-agent draft that used to live (unreachable) in this module is
archived in ``archive/multi_agent_draft.py`` until Phase 9.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from core import outcomes
from core.schemas import Task, ValidationResult
from domains.base import BaseDomainAdapter
from domains.registry import get_domain


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
    ):
        return self.domain.repair_candidate(
            candidate,
            failed_results,
            model=model,
            temperature=temperature,
            seed=seed,
            repair_fn=repair_fn,
        )

    def execute_task(
        self,
        task: Task,
        max_repairs: int = 3,
        model: Optional[str] = None,
        seed: Optional[int] = None,
        temperature: float = 0.0,
        results_log_path: Optional[str] = None,
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
            "domain": self.domain.name,
            "model": selected_model,
            "temperature_policy": f"generation={temperature}; repair increases slightly per attempt",
            "generation_temperature": temperature,
            "seed": seed,
            "retrieval_enabled": os.getenv("PROTOFORGE_NO_RETRIEVAL", "").strip().lower()
            not in {"1", "true", "yes", "on"},
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
            )
            task.candidate_patch = candidate
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
            repair_temperature = min(0.15 + 0.15 * attempt, 0.45)
            code = candidate.full_content or ""

            # Structural gate
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

            if not struct_val.passed:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "structural",
                    "success": False,
                    "failed_checks": struct_val.failed_checks,
                    **_snapshot(code, "structural", struct_val.failed_checks),
                })
                if attempt < repair_budget:
                    candidate = self._repair(
                        candidate, [struct_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn,
                    )
                    continue
                log["final"] = outcomes.STRUCTURAL_FAILED
                break

            # Compile, hardware, simulate
            test_kwargs = {
                "sim_out": task.metadata.get("sim_out", "generated/top_sim.vvp"),
                "compile_fn": compile_fn,
                "hardware_check_fn": hardware_check_fn,
                "sim_fn": sim_fn,
            }
            val_results = self.domain.build_and_test(
                candidate,
                **{k: v for k, v in test_kwargs.items() if v is not None},
            )

            compile_val = next((r for r in val_results if r.stage == "compile"), None)
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
                    break
                if attempt < repair_budget:
                    candidate = self._repair(
                        candidate, [compile_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn,
                    )
                    continue
                log["final"] = outcomes.COMPILE_FAILED
                break

            hw_val = next((r for r in val_results if r.stage == "hardware"), None)
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
                    break
                if attempt < repair_budget:
                    candidate = self._repair(
                        candidate, [hw_val],
                        model=selected_model, temperature=repair_temperature,
                        seed=seed, repair_fn=repair_fn,
                    )
                    continue
                log["final"] = outcomes.HARDWARE_FAILED
                break

            sim_val = next((r for r in val_results if r.stage == "simulation"), None)
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
                break

            # Protocol judge
            judge_kwargs = {"judge_fn": judge_fn} if judge_fn else {}
            verdict = self.domain.judge(val_results, **judge_kwargs)
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
                candidate = self._repair(
                    candidate, [*val_results, verdict],
                    model=selected_model, temperature=repair_temperature,
                    seed=seed, repair_fn=repair_fn,
                )
                continue

            log["final"] = outcomes.SIM_CHECKS_FAILED
            task.status = "failed"
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
    def _finish(log: Dict[str, Any], results_log_path: Optional[str]) -> Dict[str, Any]:
        if results_log_path:
            os.makedirs(os.path.dirname(results_log_path) or ".", exist_ok=True)
            with open(results_log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(log, sort_keys=True) + "\n")
        return log


def run_orchestrated_task(task: Task, **kwargs) -> Dict[str, Any]:
    """Run a task on its registered domain adapter."""
    domain = get_domain(task.domain)
    orchestrator = Orchestrator(domain)
    return orchestrator.execute_task(task, **kwargs)
