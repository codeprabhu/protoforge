"""Domain-neutral execution coordinator for ProtoForge."""

from __future__ import annotations

import os
import time
import json
import hashlib
from typing import Any, Callable, Dict, List, Optional

from core.schemas import CandidatePatch, Task, ValidationResult, RunEvent, AgentResult
from core.task_graph import TaskGraph, TaskState
from domains.registry import get_domain
from domains.base import BaseDomainAdapter


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def _snapshot(code: str, stage: str, failed_checks: Optional[List[str]] = None) -> Dict[str, Any]:
    h = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return {
        "sha256": h,
        "candidate_hash": h[:16],
        "code_len_chars": len(code),
        "stage": stage,
        "failed_checks": failed_checks or [],
    }


class Orchestrator:
    """Domain-independent coordinator that manages candidate generation, verification, and repair."""

    def __init__(self, domain: BaseDomainAdapter):
        self.domain = domain

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
        """Execute task through domain adapter with bounded repair."""
        started = time.time()
        selected_model = model or "default"
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
            "retrieval_enabled": os.getenv("PROTOFORGE_NO_RETRIEVAL", "").strip().lower() not in {"1", "true", "yes", "on"},
            "max_repairs": max(0, int(max_repairs)),
            "protocol": context.get("protocol", {"address": context.get("address_hex", ""), "data": context.get("data_hex", "")}),
            "rtl_memory_before_run": context.get("rtl_memory_before_run"),
            "requirements": [r.req_id for r in task.requirements],
            "attempts": [],
        }

        # 1. Generation
        target_path = task.metadata.get("target_file", "protocols/i2c/generated/top_glue.v")
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
        except Exception as exc:
            log["final"] = "generation_failed"
            log["generation_error"] = str(exc)
            return log

        if not candidate.full_content or not candidate.full_content.strip():
            log["final"] = "generation_failed"
            log["generation_error"] = "Empty or missing candidate output after generation"
            return log

        # 2. Repair loop
        for attempt in range(max(0, int(max_repairs)) + 1):
            repair_temperature = min(0.15 + 0.15 * attempt, 0.45)
            code = candidate.full_content or ""

            # Structural validation
            if struct_check_fn:
                struct_res_raw = struct_check_fn(code, self.domain.required_instances if hasattr(self.domain, "required_instances") else [])
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
                if attempt < max_repairs:
                    candidate = self.domain.repair_candidate(
                        candidate,
                        [struct_val],
                        model=selected_model,
                        temperature=repair_temperature,
                        seed=seed,
                        repair_fn=repair_fn,
                    )
                    continue
                log["final"] = "structural_failed"
                break

            # Build and test
            test_kwargs = {
                "sim_out": task.metadata.get("sim_out", "protocols/i2c/generated/top_sim.vvp"),
                "compile_fn": compile_fn,
                "hardware_check_fn": hardware_check_fn,
                "sim_fn": sim_fn,
            }
            val_results = self.domain.build_and_test(
                candidate,
                **{k: v for k, v in test_kwargs.items() if v is not None}
            )

            # Check compile stage
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
                    log["final"] = "tool_missing"
                    log["tool_error"] = compile_val.stderr
                    break
                if attempt < max_repairs:
                    candidate = self.domain.repair_candidate(
                        candidate,
                        [compile_val],
                        model=selected_model,
                        temperature=repair_temperature,
                        seed=seed,
                        repair_fn=repair_fn,
                    )
                    continue
                log["final"] = "compile_failed"
                break

            # Check hardware stage
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
                    log["final"] = "tool_missing"
                    log["tool_error"] = hw_val.diagnostics
                    break
                if attempt < max_repairs:
                    candidate = self.domain.repair_candidate(
                        candidate,
                        [hw_val],
                        model=selected_model,
                        temperature=repair_temperature,
                        seed=seed,
                        repair_fn=repair_fn,
                    )
                    continue
                log["final"] = "hardware_failed"
                break

            # Check simulation stage
            sim_val = next((r for r in val_results if r.stage == "simulation"), None)
            if sim_val and sim_val.tool_missing:
                log["attempts"].append({
                    "attempt": attempt,
                    "stage": "simulation_tool_missing",
                    "success": False,
                    "stderr": sim_val.stderr,
                    **_snapshot(code, "simulation_tool_missing"),
                })
                log["final"] = "tool_missing"
                log["tool_error"] = sim_val.stderr
                break

            # Protocol judging
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
                log["final"] = "pass"
                task.status = "completed"
                if on_success:
                    try:
                        artifact = on_success(code, model=selected_model, metadata={"checks": verdict.metadata.get("raw", {})})
                        log["verified_example"] = str(artifact)
                    except Exception:
                        pass
                break

            if attempt < max_repairs:
                candidate = self.domain.repair_candidate(
                    candidate,
                    [*val_results, verdict],
                    model=selected_model,
                    temperature=repair_temperature,
                    seed=seed,
                    repair_fn=repair_fn,
                )
                continue

            log["final"] = "sim_checks_failed"
            task.status = "failed"
            if on_failure:
                try:
                    on_failure(code, selected_model, "simulate", verdict.failed_checks, verdict.diagnostics)
                except Exception:
                    pass
            break

        log["first_attempt_pass"] = bool(
            log["attempts"] and log["attempts"][0].get("success", False)
        )
        log["recovered_after_repair"] = bool(
            log.get("final") == "pass" and len(log["attempts"]) > 1
        )
        log["elapsed_sec"] = round(time.time() - started, 3)

        if results_log_path:
            os.makedirs(os.path.dirname(results_log_path) or ".", exist_ok=True)
            with open(results_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log, sort_keys=True) + "\n")

        return log

    def execute_multi_agent_workflow(
        self,
        task: Task,
        max_repairs: int = 3,
        model: Optional[str] = None,
        seed: Optional[int] = None,
        temperature: float = 0.0,
        results_log_path: Optional[str] = None,
        # Injection hooks
        generate_fn: Optional[Callable] = None,
        compile_fn: Optional[Callable] = None,
        hardware_check_fn: Optional[Callable] = None,
        sim_fn: Optional[Callable] = None,
        judge_fn: Optional[Callable] = None,
        repair_fn: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Execute full multi-agent specialist workflow: Analyst -> Mapper -> Planner -> Implementer -> Reviewer -> Verification -> Repair."""
        from agents.spec_analyst import SpecificationAnalyst
        from agents.codebase_mapper import CodebaseMapper
        from agents.planner import PlanningAgent
        from agents.implementer import ImplementationAgent
        from agents.reviewer import ReviewerAgent
        from agents.repair import RepairAgent

        started = time.time()
        selected_model = model or "default"
        context = self.domain.build_context(task)
        context.update({
            "model": selected_model,
            "seed": seed,
            "temperature": temperature,
            "generate_fn": generate_fn,
            "repair_fn": repair_fn,
        })

        graph = TaskGraph(task_id=task.task_id, max_retries=max_repairs)
        agent_results: List[AgentResult] = []

        # 1. Specification Analyst
        graph.transition_to(TaskState.SPEC_ANALYSIS.value, "orchestrator")
        analyst = SpecificationAnalyst(model_name=selected_model)
        spec_res = analyst.execute(task, context)
        agent_results.append(spec_res)

        # 2. Codebase Mapper
        graph.transition_to(TaskState.CODE_MAPPING.value, spec_res.role, spec_res)
        mapper = CodebaseMapper(model_name=selected_model)
        mapper_res = mapper.execute(task, context)
        agent_results.append(mapper_res)

        # 3. Planning Agent
        graph.transition_to(TaskState.PLANNING.value, mapper_res.role, mapper_res)
        planner = PlanningAgent(model_name=selected_model)
        plan_res = planner.execute(task, context)
        agent_results.append(plan_res)

        # 4. Implementation Agent
        graph.transition_to(TaskState.IMPLEMENTATION.value, plan_res.role, plan_res)
        implementer = ImplementationAgent(model_name=selected_model)
        impl_res = implementer.execute(task, context)
        agent_results.append(impl_res)

        # 5. Review & Verification Loop
        reviewer = ReviewerAgent(model_name=selected_model)
        repairer = RepairAgent(model_name=selected_model)

        graph.transition_to(TaskState.REVIEW.value, impl_res.role, impl_res)
        verification_passed = False
        final_verdict = "failed"

        while not graph.is_terminal:
            candidate = task.candidate_patch

            # Review stage
            rev_res = reviewer.execute(task, context)
            agent_results.append(rev_res)

            if rev_res.decision == "rejected":
                if graph.increment_retry():
                    graph.transition_to(TaskState.FAILED.value, rev_res.role, rev_res, "Exceeded retry budget on review failure")
                    final_verdict = "review_rejected_max_retries"
                    break
                graph.transition_to(TaskState.REPAIR.value, rev_res.role, rev_res)
                rep_res = repairer.execute(task, {**context, "failed_results": [
                    ValidationResult(stage="review", passed=False, diagnostics=str(rev_res.findings))
                ]})
                agent_results.append(rep_res)
                graph.transition_to(TaskState.REVIEW.value, rep_res.role, rep_res)
                continue

            # Deterministic Verification Stage
            graph.transition_to(TaskState.VERIFICATION.value, rev_res.role, rev_res)
            test_kwargs = {
                "sim_out": task.metadata.get("sim_out", "protocols/i2c/generated/top_sim.vvp"),
                "compile_fn": compile_fn,
                "hardware_check_fn": hardware_check_fn,
                "sim_fn": sim_fn,
            }
            val_results = self.domain.build_and_test(
                candidate,
                **{k: v for k, v in test_kwargs.items() if v is not None}
            )

            # Check compile
            compile_val = next((r for r in val_results if r.stage == "compile"), None)
            if compile_val and not compile_val.passed:
                if compile_val.tool_missing or graph.increment_retry():
                    graph.transition_to(TaskState.FAILED.value, "compiler", None, "Compile failed")
                    final_verdict = "tool_missing" if compile_val.tool_missing else "compile_failed"
                    break
                graph.transition_to(TaskState.REPAIR.value, "compiler")
                rep_res = repairer.execute(task, {**context, "failed_results": [compile_val]})
                agent_results.append(rep_res)
                graph.transition_to(TaskState.REVIEW.value, rep_res.role, rep_res)
                continue

            # Check simulation / judge
            judge_kwargs = {"judge_fn": judge_fn} if judge_fn else {}
            verdict = self.domain.judge(val_results, **judge_kwargs)
            sim_val = next((r for r in val_results if r.stage == "simulation"), None)
            sim_passed = sim_val.passed if sim_val else False

            if verdict.passed and sim_passed:
                graph.transition_to(TaskState.COMPLETED.value, "oracle", None, "All verification checks passed")
                verification_passed = True
                final_verdict = "pass"
                break
            else:
                if graph.increment_retry():
                    graph.transition_to(TaskState.FAILED.value, "oracle", None, "Verification failed retry budget")
                    final_verdict = "sim_checks_failed"
                    break
                graph.transition_to(TaskState.REPAIR.value, "oracle")
                rep_res = repairer.execute(task, {**context, "failed_results": [*val_results, verdict]})
                agent_results.append(rep_res)
                graph.transition_to(TaskState.REVIEW.value, rep_res.role, rep_res)

        elapsed = round(time.time() - started, 3)
        multi_log = {
            "task_id": task.task_id,
            "domain": self.domain.name,
            "final": final_verdict,
            "passed": verification_passed,
            "retry_count": graph.retry_count,
            "elapsed_sec": elapsed,
            "transitions": [t.model_dump() for t in graph.transitions],
            "agent_results": [r.model_dump() for r in agent_results],
        }

        if results_log_path:
            os.makedirs(os.path.dirname(results_log_path) or ".", exist_ok=True)
            with open(results_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(multi_log, sort_keys=True) + "\n")

        return multi_log


def run_orchestrated_task(task: Task, **kwargs) -> Dict[str, Any]:
    """Top-level helper to run a task on its registered domain adapter."""
    domain = get_domain(task.domain)
    orchestrator = Orchestrator(domain)
    return orchestrator.execute_task(task, **kwargs)
