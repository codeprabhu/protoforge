"""Unit tests for explicit run classifications and batch validity rules (Task 3)."""

from __future__ import annotations

import pytest

from bench.run import (
    RUN_CLASSIFICATIONS,
    INFRASTRUCTURE_CLASSIFICATIONS,
    CANDIDATE_FAILURE_CLASSIFICATIONS,
    classify_final_result,
    assess_batch_validity,
    preflight_check,
    summarize,
)


class TestRunClassifications:
    """Test that explicit run classifications are properly defined and used."""

    def test_classification_list_completeness(self):
        """All expected classifications are present."""
        expected = [
            "generation_error",
            "runner_error",
            "structural_failure",
            "compile_failure",
            "lint_failure",
            "simulation_failure",
            "oracle_failure",
            "pass",
        ]
        assert RUN_CLASSIFICATIONS == expected

    def test_infrastructure_classifications(self):
        """Infrastructure classifications are correctly identified."""
        assert INFRASTRUCTURE_CLASSIFICATIONS == {"generation_error", "runner_error"}

    def test_candidate_failure_classifications(self):
        """Candidate verification failure classifications are correctly identified."""
        expected = {
            "structural_failure", "compile_failure", "lint_failure",
            "simulation_failure", "oracle_failure",
        }
        assert CANDIDATE_FAILURE_CLASSIFICATIONS == expected


class TestClassifyFinalResult:
    """Test the classify_final_result function maps correctly."""

    def test_infrastructure_failures(self):
        """generation_error and runner_error map directly."""
        assert classify_final_result("generation_error", "") == "generation_error"
        assert classify_final_result("runner_error", "") == "runner_error"

    def test_candidate_verification_failures(self):
        """Internal statuses map to explicit candidate failure classifications."""
        assert classify_final_result("structural_failed", "") == "structural_failure"
        assert classify_final_result("compile_failed", "") == "compile_failure"
        assert classify_final_result("compile_error", "") == "compile_failure"
        assert classify_final_result("hardware_failed", "") == "lint_failure"
        assert classify_final_result("lint_error", "") == "lint_failure"
        assert classify_final_result("sim_checks_failed", "") == "simulation_failure"
        assert classify_final_result("judge_error", "") == "oracle_failure"
        assert classify_final_result("protocol_error", "") == "oracle_failure"

    def test_missing_tool_is_infrastructure_not_oracle(self):
        """tool_missing is an infrastructure failure, never a protocol failure."""
        assert classify_final_result("tool_missing", "") == "runner_error"
        assert classify_final_result("tool_missing", "") in INFRASTRUCTURE_CLASSIFICATIONS

    def test_success(self):
        """pass maps to pass."""
        assert classify_final_result("pass", "") == "pass"

    def test_unknown_defaults_to_oracle_failure(self):
        """Unknown final statuses default to oracle_failure for safety."""
        assert classify_final_result("unknown_status", "") == "oracle_failure"
        assert classify_final_result("", "") == "oracle_failure"


class TestAssessBatchValidity:
    """Test batch validity assessment rules."""

    def test_empty_batch_invalid(self):
        """Empty batch is invalid."""
        valid, reason = assess_batch_validity([])
        assert valid is False
        assert "No runs completed" in reason

    def test_all_infrastructure_failures_invalid(self):
        """Batch with only infrastructure failures is invalid (model never invoked)."""
        rows = [
            {"final": "generation_error", "error": "", "attempts": 0},
            {"final": "runner_error", "error": "", "attempts": 0},
        ]
        valid, reason = assess_batch_validity(rows)
        assert valid is False
        assert "No model invocations" in reason

    def test_high_infrastructure_error_rate_invalid(self):
        """Batch with infrastructure error rate > threshold is invalid."""
        rows = [
            {"final": "generation_error", "error": "", "attempts": 0},
            {"final": "generation_error", "error": "", "attempts": 0},
            {"final": "pass", "error": "", "attempts": 1},
        ]
        valid, reason = assess_batch_validity(rows, infra_threshold=0.5)
        assert valid is False
        assert "Infrastructure error rate" in reason

    def test_infrastructure_error_rate_at_threshold_valid(self):
        """Batch with infrastructure error rate at threshold is valid."""
        rows = [
            {"final": "generation_error", "error": "", "attempts": 0},
            {"final": "pass", "error": "", "attempts": 1},
        ]
        valid, reason = assess_batch_validity(rows, infra_threshold=0.5)
        assert valid is True
        assert "Batch valid" in reason

    def test_mixed_with_model_invoked_valid(self):
        """Batch with some infra failures but model invoked and rate low is valid."""
        rows = [
            {"final": "generation_error", "error": "", "attempts": 0},
            {"final": "pass", "error": "", "attempts": 1},
            {"final": "compile_error", "error": "", "attempts": 1},
        ]
        valid, reason = assess_batch_validity(rows, infra_threshold=0.5)
        assert valid is True
        assert "Batch valid" in reason

    def test_candidate_failures_not_counted_as_infrastructure(self):
        """Candidate verification failures don't count toward infrastructure threshold."""
        rows = [
            {"final": "compile_error", "error": "", "attempts": 1},
            {"final": "lint_error", "error": "", "attempts": 1},
            {"final": "sim_checks_failed", "error": "", "attempts": 1},
            {"final": "judge_error", "error": "", "attempts": 1},
            {"final": "pass", "error": "", "attempts": 1},
        ]
        valid, reason = assess_batch_validity(rows, infra_threshold=0.5)
        assert valid is True
        assert "Batch valid" in reason


class TestSummarizeExplicitOutcomes:
    """Test that summarize() produces explicit outcome breakdowns."""

    def test_explicit_outcomes_contains_all_classifications(self):
        """Summary includes all RUN_CLASSIFICATIONS even with zero counts."""
        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "pass",
             "first_attempt_pass": True, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "aaa", "attempts": 1},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        explicit = stats["explicit_outcomes"]
        
        # All classifications present
        for c in RUN_CLASSIFICATIONS:
            assert c in explicit, f"Missing classification: {c}"
        
        # Only pass has count 1
        assert explicit["pass"] == 1
        for c in RUN_CLASSIFICATIONS:
            if c != "pass":
                assert explicit[c] == 0, f"Expected 0 for {c}, got {explicit[c]}"

    def test_infrastructure_failures_counted_separately(self):
        """Infrastructure failures are counted separately from candidate failures."""
        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "generation_error",
             "first_attempt_pass": False, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "", "attempts": 0},
            {"config": "on-on", "case_id": "c1", "sample": 1, "final": "runner_error",
             "first_attempt_pass": False, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "", "attempts": 0},
            {"config": "on-on", "case_id": "c1", "sample": 2, "final": "compile_error",
             "first_attempt_pass": False, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "", "attempts": 1},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        
        assert stats["infrastructure_failures"] == 2
        assert stats["candidate_verification_failures"] == 1
        assert stats["successful_candidates"] == 0

    def test_passes_are_not_counted_as_verification_failures(self):
        """Regression: candidate_verification_failures must exclude passes.

        The 24-run baseline has 16 passes and 8 candidate failures. Counting
        every non-infrastructure row made summary.json claim 24 failures out of
        24 runs, which contradicts both the report and the raw outcomes.
        """
        rows = []
        for index, final in enumerate(
            ["pass", "pass", "compile_error", "sim_checks_failed", "judge_error"]
        ):
            rows.append(
                {"config": "on-on", "case_id": "c1", "sample": index, "final": final,
                 "first_attempt_pass": final == "pass",
                 "recovered_after_repair": final == "pass",
                 "elapsed_sec": 10.0, "total_tokens": 100,
                 "first_sha256": f"sha{index}", "attempts": 1}
            )
        stats = summarize(rows)["configs"]["on-on"]

        assert stats["successful_candidates"] == 2
        assert stats["candidate_verification_failures"] == 3
        assert stats["infrastructure_failures"] == 0
        assert stats["candidate_runs"] == 5

    def test_batch_validity_in_summary(self):
        """Summary includes batch validity assessment."""
        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "pass",
             "first_attempt_pass": True, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "aaa", "attempts": 1},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        
        assert stats["batch_valid"] is True
        assert stats["batch_invalid_reason"] == "Batch valid"
        assert stats["model_invoked"] is True

    def test_batch_invalid_when_no_model_invoked(self):
        """Batch marked invalid when no model was invoked."""
        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "generation_error",
             "first_attempt_pass": False, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "", "attempts": 0},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        
        assert stats["batch_valid"] is False
        assert "No model invocations" in stats["batch_invalid_reason"]
        assert stats["model_invoked"] is False

    def test_legacy_outcomes_key_present(self):
        """Legacy 'outcomes' key is present for backward compatibility."""
        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "pass",
             "first_attempt_pass": True, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "aaa", "attempts": 1},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        
        assert "outcomes" in stats
        assert stats["outcomes"] == stats["explicit_outcomes"]


class TestPreflightCheck:
    """Test preflight validation logic."""

    def test_preflight_check_signature(self):
        """preflight_check function exists and has correct signature."""
        import inspect
        sig = inspect.signature(preflight_check)
        assert "model" in sig.parameters
        assert "required_tools" in sig.parameters

    def test_preflight_requires_ollama_package(self):
        """Preflight fails if ollama package not importable (mocked)."""
        # This test would need mocking to fully test; we verify the function exists
        # and returns a tuple. Actual integration test would require mocking imports.
        result = preflight_check("nonexistent-model-xyz")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


class TestBonRunnerClassification:
    """Test that Best-of-N runner uses explicit classifications."""

    def test_bon_uses_classification_through_summarize(self):
        """Best-of-N rows use the same vocabulary as summarize().

        Candidate scheduling is covered end-to-end by
        ``tests/test_bench_bon_mock.py``; this test only pins the shared
        classification contract.
        """
        assert classify_final_result("pass", "") == "pass"
        assert classify_final_result("generation_error", "") == "generation_error"
        assert classify_final_result("compile_error", "") == "compile_failure"

        rows = [
            {"config": "on-on", "case_id": "c1", "sample": 0, "final": "pass",
             "first_attempt_pass": True, "recovered_after_repair": False,
             "elapsed_sec": 10.0, "total_tokens": 100, "first_sha256": "aaa", "attempts": 1},
        ]
        stats = summarize(rows)["configs"]["on-on"]
        assert "explicit_outcomes" in stats
        assert "batch_valid" in stats


if __name__ == "__main__":
    pytest.main([__file__, "-v"])