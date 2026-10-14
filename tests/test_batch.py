"""Batch runner behaviour with a stubbed run function (no Ollama/iverilog needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def _ok(sha, first=True, final="pass"):
    return {"final": final, "first_attempt_pass": first, "recovered_after_repair": False,
            "max_repairs": 2, "attempts": [{"sha256": sha}]}


def test_batch_survives_a_failing_run_and_counts_it():
    calls = []

    def fake(model=None, seed=None, temperature=0.0):
        calls.append(seed)
        if len(calls) == 2:
            raise ValueError("model hiccup")
        return _ok("a")

    s = main.run_batch(3, run_fn=fake, temperature=0.3, base_seed=10)
    assert calls == [10, 11, 12]  # per-run seeds
    assert s["runs_completed"] == 3
    assert s["outcomes"] == {"pass": 2, "generation_error": 1}
    assert s["errors"] == ["ValueError: model hiccup"]


def test_config_error_on_first_run_stops_early():
    def fake(model=None, seed=None, temperature=0.0):
        raise RuntimeError("The Ollama Python package is missing.")

    s = main.run_batch(20, run_fn=fake, temperature=0.0)
    assert s["runs_completed"] == 1 and s["outcomes"] == {"generation_error": 1}


def test_identical_samples_are_called_out():
    s = main.run_batch(4, run_fn=lambda **kw: _ok("same"), temperature=0.0)
    assert s["distinct_first_attempts"] == 1
    assert "effectively ONE sample" in main.format_summary(s)


def test_distinct_samples_not_flagged():
    shas = iter("abcd")
    s = main.run_batch(4, run_fn=lambda **kw: _ok(next(shas)), temperature=0.5)
    assert s["distinct_first_attempts"] == 4
    assert "ONE sample" not in main.format_summary(s)
