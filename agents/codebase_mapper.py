"""Codebase Mapper specialist agent."""

from __future__ import annotations

import os
from typing import Any, Dict, List
from agents.base_agent import BaseAgent
from core.schemas import AgentResult, SourceReference, Task
from indexing.dependency_graph import DependencyGraph


class CodebaseMapper(BaseAgent):
    """Maps task requirements to concrete source files, symbols, callers, callees, and tests."""

    def __init__(self, model_name: str = "default"):
        super().__init__(role="codebase_mapper", model_name=model_name)

    def execute(self, task: Task, context: Dict[str, Any]) -> AgentResult:
        dep_graph: DependencyGraph = context.get("dependency_graph") or DependencyGraph()
        target_symbols: List[str] = context.get("target_symbols") or task.metadata.get("target_symbols", [])

        source_refs: List[SourceReference] = []
        findings: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = []

        for sym_name in target_symbols:
            info = dep_graph.query_symbol(sym_name)
            if "error" not in info:
                sym_data = info["symbol"]
                ref = SourceReference(
                    file_path=sym_data.get("file_path", ""),
                    line_start=sym_data.get("line_start"),
                    line_end=sym_data.get("line_end"),
                    symbol=sym_name,
                )
                source_refs.append(ref)
                findings.append({
                    "symbol": sym_name,
                    "callees": [c["name"] for c in info.get("called_functions", [])],
                    "callers": [c["name"] for c in info.get("caller_functions", [])],
                    "structures": [s["name"] for s in info.get("data_structures", [])],
                    "tests": [t["name"] for t in info.get("associated_tests", [])],
                })
                evidence.append({"symbol": sym_name, "file": sym_data.get("file_path", "")})

        # If no explicit symbols, retrieve source references from domain adapter or context
        if not source_refs:
            try:
                from domains.registry import get_domain
                domain = get_domain(task.domain)
                source_refs = domain.index_sources(context.get("device_paths") or [])
                for ref in source_refs:
                    findings.append({"target_file": ref.file_path, "status": "indexed", "symbol": ref.symbol})
                    evidence.append({"file": ref.file_path, "symbol": ref.symbol})
            except Exception:
                files_to_touch = context.get("device_paths") or [context.get("tb_path")] or []
                for f in files_to_touch:
                    if f and os.path.exists(f):
                        source_refs.append(SourceReference(file_path=f))
                        findings.append({"target_file": f, "status": "located"})

        assumptions = [
            "Source files referenced match current repository head",
            "Dependencies are scoped to the target protocol or driver module",
        ]

        dec = self.build_decision_record(
            task=task,
            objective=f"Map code dependencies for {task.task_id}",
            assumptions=assumptions,
            evidence=evidence,
            findings=findings,
            conclusion=f"Mapped {len(source_refs)} source references and dependencies.",
            rejected_alternatives=["Modifying unrelated shared libraries or vendor headers"],
            next_action="planning",
        )

        return AgentResult(
            task_id=task.task_id,
            role=self.role,
            objective="Map codebase dependencies",
            requirements=[r.req_id for r in task.requirements],
            source_references=source_refs,
            assumptions=assumptions,
            findings=findings,
            confidence=1.0,
            decision="codebase_mapped",
            next_action="planning",
            decision_record=dec,
            raw_output=f"Mapped {len(source_refs)} source units.",
        )
