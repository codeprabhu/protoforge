"""Run a batch against the fixed I2C simulation oracle.

Sampling note: with temperature 0 and a fixed seed every run in a batch is
expected to produce the *same* RTL, so N runs measure one sample, not N. Set
PROTOFORGE_BATCH_TEMPERATURE (e.g. 0.2-0.7) to get independent samples; each
run then uses seed = PROTOFORGE_SEED + run_index. The report prints how many
distinct first-attempt designs were generated so this is never hidden.

Set PROTOFORGE_NO_RETRIEVAL=1 to measure generation without the reference
example in the prompt (ablation).
"""

import json
import os
import sys
import traceback
from collections import Counter

from agent_core.agent import run_once
from agent.ollama_client import DEFAULT_MODEL, DEFAULT_SEED
from tools.rtl_memory import memory_summary

KNOWN_FINALS = {"pass", "compile_failed", "structural_failed", "sim_checks_failed", "tool_missing", "generation_error"}


def run_batch(n_runs: int, model: str | None = None, run_fn=run_once,
              temperature: float | None = None, base_seed: int = DEFAULT_SEED) -> dict:
    """Run n_runs attempts; one failing run never aborts the batch."""
    if temperature is None:
        temperature = float(os.getenv("PROTOFORGE_BATCH_TEMPERATURE", "0"))
    results = []
    for i in range(n_runs):
        try:
            results.append(run_fn(model=model, seed=base_seed + i, temperature=temperature))
        except Exception as exc:  # e.g. Ollama not running / model not pulled
            results.append({
                "final": "generation_error",
                "error": f"{type(exc).__name__}: {exc}",
                "attempts": [],
                "max_repairs": None,
            })
            traceback.print_exc(limit=1)
            if i == 0 and isinstance(exc, RuntimeError):
                # Configuration errors (missing package) will repeat identically.
                break
    counts = Counter(r.get("final", "unknown") for r in results)
    first_hashes = [r["attempts"][0]["sha256"] for r in results if r.get("attempts")]
    n = max(len(results), 1)
    summary = {
        "model": model or DEFAULT_MODEL,
        "runs_requested": n_runs,
        "runs_completed": len(results),
        "temperature": temperature,
        "base_seed": base_seed,
        "retrieval_enabled": os.getenv("PROTOFORGE_NO_RETRIEVAL", "").strip().lower() not in {"1", "true", "yes", "on"},
        "pass_after_repair": counts["pass"],
        "first_try_pass": sum(bool(r.get("first_attempt_pass")) for r in results),
        "recovered_by_repair": sum(bool(r.get("recovered_after_repair")) for r in results),
        "outcomes": dict(counts),
        "other_outcomes": sum(v for k, v in counts.items() if k not in KNOWN_FINALS),
        "distinct_first_attempts": len(set(first_hashes)),
        "first_attempts_recorded": len(first_hashes),
        "errors": sorted({r["error"] for r in results if r.get("error")}),
    }
    summary["pass_rate_after_repair"] = counts["pass"] / n
    summary["first_try_pass_rate"] = summary["first_try_pass"] / n
    return summary


def format_summary(s: dict) -> str:
    n = max(s["runs_completed"], 1)
    lines = [
        f"Model:                  {s['model']}",
        f"Runs:                   {s['runs_completed']}/{s['runs_requested']}  (temperature {s['temperature']}, base seed {s['base_seed']})",
        f"Retrieval in prompt:    {'on' if s['retrieval_enabled'] else 'OFF (ablation)'}",
        f"Pass rate after repair: {s['pass_after_repair']}/{n} ({s['pass_rate_after_repair']:.1%})",
        f"First-try pass rate:    {s['first_try_pass']}/{n} ({s['first_try_pass_rate']:.1%})",
        f"Recovered by repair:    {s['recovered_by_repair']}/{n}",
        f"Compile-failed:         {s['outcomes'].get('compile_failed', 0)}/{n}",
        f"Structural-failed:      {s['outcomes'].get('structural_failed', 0)}/{n}",
        f"Simulation-failed:      {s['outcomes'].get('sim_checks_failed', 0)}/{n}",
        f"Tool-missing:           {s['outcomes'].get('tool_missing', 0)}/{n}",
        f"Generation errors:      {s['outcomes'].get('generation_error', 0)}/{n}",
        f"Other outcomes:         {s['other_outcomes']}",
        f"Distinct first designs: {s['distinct_first_attempts']}/{s['first_attempts_recorded']}",
        f"RTL memory:             {memory_summary()}",
        "Full log:               results/runs.jsonl   Summary: results/batch_summary.json",
    ]
    if s["first_attempts_recorded"] > 1 and s["distinct_first_attempts"] == 1:
        lines.insert(0, "NOTE: every first attempt was identical, so this batch is effectively ONE sample. "
                        "Raise PROTOFORGE_BATCH_TEMPERATURE for independent samples.")
    for err in s["errors"]:
        lines.append(f"Error: {err}")
    return "\n".join(lines)


if __name__ == "__main__":
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    model = sys.argv[2] if len(sys.argv) > 2 else None
    summary = run_batch(n_runs, model)
    os.makedirs("results", exist_ok=True)
    with open("results/batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    print(format_summary(summary))
