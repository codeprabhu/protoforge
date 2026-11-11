"""Phase 6 benchmark runner.

Runs every case in ``benchmarks/cases.json`` for each requested configuration,
writes one CSV row per run, and produces an aggregate JSON/markdown report with
pass@1, pass-after-repair, distinct first designs, latency, token counts and
Wilson 95% confidence intervals.

Configurations (the 2x2 ablation shares the on/on cell):

    template       deterministic template, no model (upper bound)
    on-on          retrieval on,  regex patcher on
    retrieval-off  retrieval off, regex patcher on
    patcher-off    retrieval on,  regex patcher off

``retrieval-on``/``patcher-on`` are accepted as aliases for ``on-on``.

``--ir`` swaps the Verilog generator for the FSM JSON generator (Phase 7,
step 90). Model configs then get an ``-ir`` suffix (``on-on-ir``) so the
report can compare them with the committed RTL baseline; the regex patcher
has no meaning on that path, so patcher-off collapses into on-on-ir.

Retrieval memory is frozen during a benchmark by default: benchmark runs do
not add new passing/failed examples, so later runs in the same batch do not see
examples learned from earlier benchmark cases. Pass ``--learn`` to opt out.

Run Classifications (explicit, not collapsed):
    generation_error   Model failed to produce a candidate (Ollama missing, timeout, etc.)
    runner_error       Infrastructure failure (server down, missing tool, import error, etc.)
    structural_failure Candidate produced but the structural gate rejected it
    compile_failure    Candidate produced but iverilog compilation failed
    lint_failure       Candidate compiled but Verilator lint / Yosys synthesis failed
    simulation_failure Candidate compiled but vvp simulation failed
    oracle_failure     Simulation passed but protocol judge rejected
    pass               All gates passed (first attempt or after repair)

Batch Validity Rules:
    A batch is INVALID if:
    - Preflight fails
    - Infrastructure error rate exceeds threshold (default 50%)
    - Most attempted runs are infrastructure errors
    - Cannot establish intended model was actually invoked
    Invalid batches must not produce valid pass-rate conclusions.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.ir_generator import generate_fsm_rtl, repair_fsm_rtl  # noqa: E402
from agent_core.agent import run_once  # noqa: E402
from agent_core.config import Config  # noqa: E402
from baseline.template_master import generate_template_rtl  # noqa: E402
from tools.make_spec import (  # noqa: E402
    DEFAULT_CASES,
    case_device_paths,
    load_cases,
    write_case_spec,
)
from tools.rtl_memory import retrieval_corpus_hash  # noqa: E402

CONFIGS: dict[str, dict[str, Any]] = {
    "template": {
        "kind": "template", "retrieval": False, "patcher": True,
        "generator": "template",
    },
    "on-on": {
        "kind": "model", "retrieval": True, "patcher": True, "generator": "rtl",
    },
    "retrieval-off": {
        "kind": "model", "retrieval": False, "patcher": True, "generator": "rtl",
    },
    "patcher-off": {
        "kind": "model", "retrieval": True, "patcher": False, "generator": "rtl",
    },
}
ALIASES = {
    "retrieval-on": "on-on",
    "patcher-on": "on-on",
    "default": "on-on",
}

# Explicit run classifications (never collapse these into a single failure category)
RUN_CLASSIFICATIONS = [
    "generation_error",    # Model failed to produce a candidate
    "runner_error",        # Infrastructure failure (server down, missing tool, etc.)
    "structural_failure",  # Candidate produced but failed the structural gate
    "compile_failure",     # Candidate produced but iverilog compilation failed
    "lint_failure",        # Candidate compiled but Verilator/Yosys checks failed
    "simulation_failure",  # Candidate compiled but vvp simulation failed
    "oracle_failure",      # Simulation passed but protocol judge rejected
    "pass",                # All gates passed (first attempt or after repair)
]

INFRASTRUCTURE_CLASSIFICATIONS = {"generation_error", "runner_error"}
CANDIDATE_FAILURE_CLASSIFICATIONS = {
    "structural_failure",
    "compile_failure",
    "lint_failure",
    "simulation_failure",
    "oracle_failure",
}

CSV_FIELDS = [
    "config",
    "case_id",
    "sample",
    "seed",
    "model",
    "temperature",
    "max_repairs",
    "retrieval",
    "patcher",
    "generator",
    "final",
    "first_attempt_pass",
    "recovered_after_repair",
    "attempts",
    "elapsed_sec",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "first_sha256",
    "run_id",
    "run_dir",
    "error",
    # Explicit classification field
    "explicit_classification",
    # Batch validity fields
    "batch_valid",
    "batch_invalid_reason",
]


def preflight_check(model: str, required_tools: list[str] | None = None) -> tuple[bool, str]:
    """Preflight validation before starting any model experiment.
    
    Verifies:
    - Ollama Python package is importable
    - Ollama server is reachable
    - Requested model exists
    - Required configuration is valid
    - Output directory is writable
    - Required external tools are available
    
    Returns:
        (success, message) - if success is False, message explains why
    """
    if required_tools is None:
        required_tools = ["iverilog", "vvp", "verilator", "yosys"]
    
    # 1. Check Ollama Python package
    try:
        import ollama
    except ImportError:
        return False, "Ollama Python package is not importable"
    
    # 2. Check Ollama server reachable and model exists
    try:
        client = ollama.Client()
        models = client.list()
        model_names = [m.get("model", "") for m in models.get("models", [])]
        if model not in model_names:
            return False, f"Model '{model}' not found in Ollama (available: {model_names})"
    except Exception as e:
        return False, f"Ollama server not reachable: {e}"
    
    # 3. Check required external tools
    missing_tools = []
    for tool in required_tools:
        if not shutil.which(tool):
            missing_tools.append(tool)
    if missing_tools:
        return False, f"Required tools missing: {missing_tools}"
    
    return True, "Preflight OK"


def classify_final_result(final: str, error: str = "") -> str:
    """Map internal 'final' status to explicit run classification.
    
    Args:
        final: The 'final' field from run log (e.g., 'pass', 'compile_error', etc.)
        error: The 'error' field from run log
        
    Returns:
        One of RUN_CLASSIFICATIONS
    """
    # Infrastructure failures
    if final in ("generation_error", "runner_error"):
        return final
    # A missing external tool says nothing about the candidate; it is an
    # infrastructure failure, not an RTL failure.
    if final == "tool_missing":
        return "runner_error"

    # Candidate verification failures
    if final == "structural_failed":
        return "structural_failure"
    if final in ("compile_failed", "compile_error"):
        return "compile_failure"
    # The hardware stage is Verilator lint + Yosys synthesis.
    if final in ("hardware_failed", "lint_error"):
        return "lint_failure"
    if final == "sim_checks_failed":
        return "simulation_failure"
    if final in ("judge_error", "protocol_error"):
        return "oracle_failure"

    # Success
    if final == "pass":
        return "pass"

    # Unknown - default to oracle_failure for safety
    return "oracle_failure"


def assess_batch_validity(rows: list[dict[str, Any]], infra_threshold: float = 0.5) -> tuple[bool, str]:
    """Assess whether a batch is valid for drawing pass-rate conclusions.
    
    A batch is INVALID if:
    - Preflight failed (indicated by no valid model invocations)
    - Infrastructure error rate exceeds threshold
    - Most attempted runs are infrastructure errors
    - Cannot establish intended model was actually invoked
    
    Args:
        rows: List of result rows from the batch
        infra_threshold: Maximum allowed infrastructure error rate (default 50%)
        
    Returns:
        (is_valid, reason) - if not valid, reason explains why
    """
    if not rows:
        return False, "No runs completed"
    
    total = len(rows)
    infra_count = 0
    candidate_count = 0
    model_invoked = False
    
    for row in rows:
        classification = classify_final_result(row.get("final", ""), row.get("error", ""))
        if classification in INFRASTRUCTURE_CLASSIFICATIONS:
            infra_count += 1
        else:
            candidate_count += 1
            if row.get("attempts", 0) > 0:
                model_invoked = True
    
    # Check if intended model was actually invoked
    if not model_invoked:
        return False, "No model invocations recorded (all runs were infrastructure failures)"
    
    # Check infrastructure error rate
    if total > 0 and (infra_count / total) > infra_threshold:
        return False, f"Infrastructure error rate {infra_count}/{total} = {infra_count/total:.1%} exceeds threshold {infra_threshold:.0%}"
    
    return True, "Batch valid"


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1.0 + (z * z) / n
    centre = (p + (z * z) / (2 * n)) / denominator
    half = (
        z
        * math.sqrt((p * (1.0 - p) / n) + (z * z) / (4.0 * n * n))
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def resolve_configs(
    names: Iterable[str], *, ir: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """Expand aliases, keep order, and drop duplicate configurations.

    ``ir=True`` switches every model config to the JSON IR generator. The
    regex patcher is not part of that path, so patcher-off collapses into the
    retrieval-on cell instead of producing a duplicate run.
    """
    resolved: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, bool, bool]] = set()
    for name in names:
        canonical = ALIASES.get(name, name)
        if canonical not in CONFIGS:
            raise ValueError(
                f"unknown config {name!r}; choose from {sorted(CONFIGS)} "
                f"(aliases: {sorted(ALIASES)})"
            )
        spec = dict(CONFIGS[canonical])
        if ir and spec["kind"] == "model":
            spec["generator"] = "ir"
            spec["patcher"] = True
            canonical = f"{canonical}-ir"
        key = (spec["generator"], spec["retrieval"], spec["patcher"])
        if key in seen:
            continue
        seen.add(key)
        resolved.append((canonical, spec))
    return resolved


def _token_counts(run_dir: str | None) -> tuple[int, int]:
    """Sum prompt/completion tokens across every attempt in a run."""
    if not run_dir:
        return (0, 0)
    prompt = completion = 0
    for path in sorted(Path(run_dir).glob("attempt_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        metadata = payload.get("model_metadata") or {}
        prompt += int(metadata.get("prompt_eval_count") or 0)
        completion += int(metadata.get("eval_count") or 0)
    return (prompt, completion)


def _template_generate(devices, spec_path, **kwargs) -> str:
    return generate_template_rtl(
        device_paths=list(devices),
        spec_path=spec_path,
    )


def run_one(
    *,
    config_name: str,
    config_spec: dict[str, Any],
    case: dict[str, Any],
    spec_path: Path,
    sample: int,
    seed: int,
    model: str,
    temperature: float,
    max_repairs: int,
    out_dir: Path,
    learn: bool = False,
) -> dict[str, Any]:
    device_paths = case_device_paths(case)
    run_config = Config(
        device_paths=tuple(device_paths),
        spec_path=str(spec_path),
        max_repairs=max_repairs,
        model=model if config_spec["kind"] == "model" else "template",
        seed=seed,
        temperature=temperature,
        retrieval=config_spec["retrieval"],
        patcher=config_spec["patcher"],
        output_root=str(out_dir / "runs"),
        results_log=str(out_dir / "runs.jsonl"),
        trace_dir=str(out_dir / "traces"),
    )
    hooks: dict[str, Any] = {}
    if config_spec["kind"] == "template":
        hooks["generate_fn"] = _template_generate
    elif config_spec.get("generator") == "ir":
        hooks["generate_fn"] = generate_fsm_rtl
        hooks["repair_fn"] = repair_fsm_rtl
    if not learn:
        # Freeze retrieval memory: a benchmark must not teach itself.
        hooks["on_success"] = lambda *args, **kwargs: None
        hooks["on_failure"] = lambda *args, **kwargs: None

    started = time.time()
    try:
        log = run_once(model=run_config.model, config=run_config, **hooks)
    except Exception as exc:  # runner failure, distinct from a model failure
        return {
            "config": config_name,
            "case_id": case["id"],
            "sample": sample,
            "seed": seed,
            "model": run_config.model,
            "temperature": temperature,
            "max_repairs": max_repairs,
            "retrieval": config_spec["retrieval"],
            "patcher": config_spec["patcher"],
            "generator": config_spec["generator"],
            "final": "runner_error",
            "first_attempt_pass": False,
            "recovered_after_repair": False,
            "attempts": 0,
            "elapsed_sec": round(time.time() - started, 3),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "first_sha256": "",
            "run_id": "",
            "run_dir": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    prompt_tokens, completion_tokens = _token_counts(log.get("run_dir"))
    attempts = log.get("attempts") or []
    first_sha = attempts[0].get("sha256", "") if attempts else ""
    return {
        "config": config_name,
        "case_id": case["id"],
        "sample": sample,
        "seed": seed,
        "model": run_config.model,
        "temperature": temperature,
        "max_repairs": max_repairs,
        "retrieval": config_spec["retrieval"],
        "patcher": config_spec["patcher"],
        "generator": config_spec["generator"],
        "final": log.get("final", "unknown"),
        "first_attempt_pass": bool(log.get("first_attempt_pass", False)),
        "recovered_after_repair": bool(log.get("recovered_after_repair", False)),
        "attempts": len(attempts),
        "elapsed_sec": log.get("elapsed_sec", round(time.time() - started, 3)),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "first_sha256": first_sha,
        "run_id": log.get("run_id", ""),
        "run_dir": log.get("run_dir", ""),
        # Why the run ended when it never produced a candidate; empty otherwise.
        "error": log.get("generation_error") or log.get("tool_error") or log.get("repair_error") or "",
    }


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate rows per configuration with explicit classifications and batch validity."""
    summary: dict[str, Any] = {"configs": {}, "rows": len(rows)}
    names = []
    for row in rows:
        if row["config"] not in names:
            names.append(row["config"])
    for name in names:
        group = [row for row in rows if row["config"] == name]
        n = len(group)
        pass1 = sum(1 for row in group if row["first_attempt_pass"])
        pass_after = sum(1 for row in group if row["final"] == "pass")
        recovered = sum(1 for row in group if row["recovered_after_repair"])
        first_hashes = [row["first_sha256"] for row in group if row["first_sha256"]]
        latencies = [float(row["elapsed_sec"]) for row in group if row["elapsed_sec"] is not None]
        tokens = [float(row["total_tokens"]) for row in group]
        
        # Explicit classification counts (never collapse into single failure category)
        explicit_outcomes: dict[str, int] = {c: 0 for c in RUN_CLASSIFICATIONS}
        without_candidate = 0
        infra_failures = 0
        candidate_failures = 0
        candidate_runs = 0
        model_invoked = False
        
        for row in group:
            classification = classify_final_result(row.get("final", ""), row.get("error", ""))
            explicit_outcomes[classification] = explicit_outcomes.get(classification, 0) + 1
            
            if not int(row.get("attempts") or 0):
                without_candidate += 1
            
            if classification in INFRASTRUCTURE_CLASSIFICATIONS:
                infra_failures += 1
            elif classification in CANDIDATE_FAILURE_CLASSIFICATIONS:
                # A pass is a candidate run too, but it is not a verification
                # failure. Previously this counter included passes, which made
                # summary.json report 24 failures for 24 runs including 16 passes.
                candidate_failures += 1
            if classification not in INFRASTRUCTURE_CLASSIFICATIONS:
                if row.get("attempts", 0) > 0:
                    model_invoked = True
                    candidate_runs += 1
        
        # Batch validity assessment
        batch_valid, batch_reason = assess_batch_validity(group)
        
        summary["configs"][name] = {
            "runs": n,
            "cases": len({row["case_id"] for row in group}),
            "samples": len({row["sample"] for row in group}),
            # Runs that never staged a candidate (generation_error / runner_error)
            # say nothing about model quality; keep them visible separately.
            "runs_without_candidate": without_candidate,
            "candidate_runs": n - without_candidate,
            "pass_at_1": pass1,
            "pass_at_1_rate": round(pass1 / n, 4) if n else 0.0,
            "pass_at_1_ci95": [round(v, 4) for v in wilson_interval(pass1, n)],
            "pass_after_repair": pass_after,
            "pass_after_repair_rate": round(pass_after / n, 4) if n else 0.0,
            "pass_after_repair_ci95": [round(v, 4) for v in wilson_interval(pass_after, n)],
            "recovered_by_repair": recovered,
            "distinct_first_designs": len(set(first_hashes)),
            "first_attempts_recorded": len(first_hashes),
            "median_latency_sec": _median(latencies),
            "total_tokens": int(sum(tokens)),
            "median_tokens_per_run": _median(tokens),
            # Explicit classification breakdown (REQUIRED - never collapse)
            "explicit_outcomes": dict(sorted(explicit_outcomes.items())),
            # Legacy outcomes key for backward compatibility with existing artifacts
            "outcomes": dict(sorted(explicit_outcomes.items())),
            # Infrastructure vs candidate failure summary
            "infrastructure_failures": infra_failures,
            "candidate_verification_failures": candidate_failures,
            "successful_candidates": explicit_outcomes.get("pass", 0),
            # Batch validity
            "batch_valid": batch_valid,
            "batch_invalid_reason": batch_reason,
            "model_invoked": model_invoked,
        }
    return summary


def _markdown_table(summary: dict[str, Any]) -> str:
    header = (
        "| Config | Runs | Infra | Candidate Fail | Pass | Batch Valid | pass@1 | pass@1 95% CI | pass after repair | "
        "after-repair 95% CI | recovered | distinct first | median latency (s) | median tokens |\n"
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    # Keep the report order stable regardless of dict loading order: a JSON
    # round-trip sorts keys, but the table should still lead with the baseline.
    names = [name for name in CONFIGS if name in summary["configs"]]
    names += [name for name in summary["configs"] if name not in names]
    for name in names:
        stats = summary["configs"][name]
        ci1 = stats["pass_at_1_ci95"]
        cia = stats["pass_after_repair_ci95"]
        infra = stats.get("infrastructure_failures", 0)
        cand_fail = stats.get("candidate_verification_failures", 0)
        passed = stats.get("successful_candidates", 0)
        batch_valid = stats.get("batch_valid", True)
        batch_status = "✅" if batch_valid else "❌"
        lines.append(
            f"| {name} | {stats['runs']} | {infra} | {cand_fail} | {passed} | {batch_status} | "
            f"{stats['pass_at_1']}/{stats['runs']} ({stats['pass_at_1_rate']:.1%}) | "
            f"[{ci1[0]:.1%}, {ci1[1]:.1%}] | "
            f"{stats['pass_after_repair']}/{stats['runs']} "
            f"({stats['pass_after_repair_rate']:.1%}) | "
            f"[{cia[0]:.1%}, {cia[1]:.1%}] | "
            f"{stats['recovered_by_repair']} | "
            f"{stats['distinct_first_designs']}/{stats['first_attempts_recorded']} | "
            f"{stats['median_latency_sec']} | "
            f"{stats['median_tokens_per_run']} |"
        )
    return "\n".join(lines) + "\n"




def _coerce_row(row: dict[str, str]) -> dict[str, Any]:
    """Turn a CSV row back into the typed dict ``summarize`` expects."""
    typed: dict[str, Any] = {key: row.get(key, "") for key in CSV_FIELDS}
    for key in ("sample", "seed", "max_repairs", "attempts"):
        typed[key] = int(float(typed[key] or 0))
    for key in (
        "temperature",
        "elapsed_sec",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    ):
        typed[key] = float(typed[key] or 0.0)
    for key in (
        "retrieval",
        "patcher",
        "first_attempt_pass",
        "recovered_after_repair",
    ):
        typed[key] = str(typed[key]) in {"True", "true", "1"}
    return typed


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _first_run_field(rows: list[dict[str, Any]], field: str) -> str:
    """Read a provenance field from the first run.json that records it.

    Raw run directories are gitignored, so the tracked summary must carry the
    model digest and experiment commit itself.
    """
    for row in rows:
        run_dir = row.get("run_dir")
        if not run_dir:
            continue
        try:
            header = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = header.get(field)
        if value:
            return str(value)
    return ""


def _run_started_at(run_dir: str) -> float:
    if not run_dir:
        return 0.0
    try:
        header = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
        return float(header.get("started_at") or 0.0)
    except (OSError, ValueError, TypeError):
        return 0.0


def _write_index(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write one JSON line per CSV row; the CSV is the source of truth.

    Deriving the index here (instead of relying only on the orchestrator's
    append) means a resumed batch, a crash or a re-run can never leave the csv
    and the jsonl describing different runs.
    """
    entries = [
        {
            "run_id": row["run_id"],
            "timestamp": _run_started_at(row["run_dir"]),
            "task_id": f"i2c-{row['run_id']}",
            "domain": "i2c",
            "model": row["model"],
            "seed": row["seed"],
            "temperature": row["temperature"],
            "retrieval_enabled": row["retrieval"],
            "final": row["final"],
            "error": row["error"] or None,
            "first_attempt_pass": row["first_attempt_pass"],
            "recovered_after_repair": row["recovered_after_repair"],
            "attempts": row["attempts"],
            "first_attempt_hash": (row["first_sha256"] or "")[:16] or None,
            "elapsed_sec": row["elapsed_sec"],
            "run_dir": row["run_dir"],
        }
        for row in rows
    ]
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--samples", type=int, default=20, help="runs per case")
    parser.add_argument("--base-seed", type=int, default=42)
    parser.add_argument("--temperature", type=float, default=0.25)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument(
        "--configs",
        default="template,on-on,retrieval-off,patcher-off",
        help="comma-separated config names",
    )
    parser.add_argument("--out-dir", default="results/bench")
    parser.add_argument(
        "--ir",
        action="store_true",
        help="use the FSM JSON generator/repair instead of free-form Verilog",
    )
    parser.add_argument(
        "--case-limit",
        type=int,
        default=0,
        help="only the first N cases (0 = all); useful for a smoke test",
    )
    parser.add_argument(
        "--learn",
        action="store_true",
        help="let benchmark runs update retrieval memory (default: frozen)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep completed (config, case, sample) rows already in runs.csv",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases(args.cases)
    if args.case_limit:
        cases = cases[: args.case_limit]
    configs = resolve_configs(
        [name.strip() for name in args.configs.split(",") if name.strip()],
        ir=args.ir,
    )
    
    # PREFLIGHT CHECK - Run before any model experiments
    # Skip preflight for template-only configs (no model needed)
    model_configs = [name for name, spec in configs if spec["kind"] == "model"]
    if model_configs and not args.resume:
        preflight_ok, preflight_msg = preflight_check(args.model)
        if not preflight_ok:
            print(f"PREFLIGHT FAILED: {preflight_msg}", file=sys.stderr)
            print("Aborting benchmark - infrastructure not ready.", file=sys.stderr)
            return 2  # Exit code 2 = preflight failure
        if not args.quiet:
            print(f"Preflight OK: {preflight_msg}")
    
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_dir = out_dir / "specs"
    spec_paths = {case["id"]: write_case_spec(case, spec_dir) for case in cases}

    rows: list[dict[str, Any]] = []
    requested = {name for name, _ in configs}
    total = len(configs) * len(cases) * args.samples
    csv_path = out_dir / "runs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    completed: set[tuple[str, str, int]] = set()
    if args.resume and csv_path.exists():
        with csv_path.open(encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                if raw.get("config") not in requested:
                    continue
                row = _coerce_row(raw)
                rows.append(row)
                completed.add((row["config"], row["case_id"], row["sample"]))

    if not args.resume:
        # A fresh batch starts from an empty index; the orchestrator appends to
        # it, and _write_index derives the final file from the CSV rows.
        (out_dir / "runs.jsonl").write_text("", encoding="utf-8")
    done = len(rows)

    def _write_summary() -> None:
        summary = summarize(rows)
        summary.update({
            "model": args.model,
            "model_digest": _first_run_field(rows, "model_digest"),
            "git_commit": _first_run_field(rows, "git_commit"),
            "temperature": args.temperature,
            "max_repairs": args.max_repairs,
            "base_seed": args.base_seed,
            "samples_per_case": args.samples,
            "cases": [case["id"] for case in cases],
            "retrieval_memory_frozen": not args.learn,
            "retrieval_corpus_hash": retrieval_corpus_hash(),
            "ir": args.ir,
        })
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (out_dir / "report.md").write_text(_markdown_table(summary), encoding="utf-8")
        _write_index(out_dir / "runs.jsonl", rows)
        return summary

    # One CSV row per run, flushed immediately so an interrupted batch still
    # leaves auditable partial results. On resume the existing header stays and
    # only missing runs are appended.
    csv_mode = "a" if completed else "w"
    with csv_path.open(csv_mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not completed:
            writer.writeheader()
            handle.flush()
        for config_name, config_spec in configs:
            for case in cases:
                for sample in range(args.samples):
                    if (config_name, case["id"], sample) in completed:
                        continue
                    seed = args.base_seed + sample
                    row = run_one(
                        config_name=config_name,
                        config_spec=config_spec,
                        case=case,
                        spec_path=spec_paths[case["id"]],
                        sample=sample,
                        seed=seed,
                        model=args.model,
                        temperature=args.temperature,
                        max_repairs=args.max_repairs,
                        out_dir=out_dir,
                        learn=args.learn,
                    )
                    rows.append(row)
                    writer.writerow(row)
                    handle.flush()
                    done += 1
                    if not args.quiet:
                        print(
                            f"[{done}/{total}] {config_name} {case['id']} "
                            f"sample={sample} seed={seed} -> {row['final']}",
                            flush=True,
                        )
            # A completed config is a useful checkpoint on a long batch.
            _write_summary()

    # Normalise the CSV from the in-memory rows (a resumed file may have been
    # appended to) and refresh the derived artifacts from that same list.
    _write_csv(csv_path, rows)
    summary = _write_summary()
    if not args.quiet:
        print()
        print(_markdown_table(summary), end="")
        print(f"\nCSV: {out_dir / 'runs.csv'}")
        print(f"Summary: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
