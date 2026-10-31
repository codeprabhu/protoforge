"""Mutation test runner and oracle sensitivity evaluator."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional
from mutations.schema import MutantCandidate, MutationResult


class MutationRunner:
    """Runs verification against mutant candidates to compute oracle kill rate and diagnostic accuracy."""

    def evaluate_mutant(
        self,
        mutant: MutantCandidate,
        verifier_fn: Callable[[str], Dict[str, Any]],
    ) -> MutationResult:
        """Run verification on a single mutant candidate and compare against expected failure."""
        res = verifier_fn(mutant.mutated_content)
        passed = res.get("pass", False) if "pass" in res else res.get("passed", False)
        failed_checks = res.get("failed_checks", [])
        stage = res.get("stage", "unknown")
        diag = res.get("diagnostics", "") or res.get("stderr", "")

        detected = not passed
        expected_check = mutant.mutation.expected_failed_check
        matched_expected = expected_check in failed_checks or (
            mutant.mutation.expected_failed_stage == stage and not passed
        )

        return MutationResult(
            mutant_id=mutant.mutant_id,
            mutation_id=mutant.mutation.mutation_id,
            detected=detected,
            actual_failed_stage=stage,
            actual_failed_checks=failed_checks,
            diagnostic_output=diag,
            oracle_matched_expected=matched_expected,
            metadata={"verifier_raw": res},
        )

    def run_suite(
        self,
        mutants: List[MutantCandidate],
        verifier_fn: Callable[[str], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute a full suite of mutants and compute mutation score."""
        results: List[MutationResult] = []
        for m in mutants:
            result = self.evaluate_mutant(m, verifier_fn)
            results.append(result)

        total = len(results)
        killed = sum(1 for r in results if r.detected)
        exact_matches = sum(1 for r in results if r.oracle_matched_expected)
        kill_rate = (killed / total * 100.0) if total > 0 else 0.0
        exact_rate = (exact_matches / total * 100.0) if total > 0 else 0.0

        summary = {
            "total_mutants": total,
            "killed_mutants": killed,
            "survived_mutants": total - killed,
            "mutation_score_pct": round(kill_rate, 2),
            "exact_oracle_match_pct": round(exact_rate, 2),
            "results": [r.model_dump() for r in results],
        }
        return summary

    def save_results(self, summary: Dict[str, Any], path: str) -> None:
        """Persist mutation run results with stable IDs to JSON."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
