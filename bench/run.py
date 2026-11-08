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

Retrieval memory is frozen during a benchmark by default: benchmark runs do
not add new passing/failed examples, so later runs in the same batch do not see
examples learned from earlier benchmark cases. Pass ``--learn`` to opt out.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.agent import run_once  # noqa: E402
from agent_core.config import Config  # noqa: E402
from baseline.template_master import generate_template_rtl  # noqa: E402
from tools.make_spec import (  # noqa: E402
    DEFAULT_CASES,
    case_device_paths,
    load_cases,
    write_case_spec,
)

CONFIGS: dict[str, dict[str, Any]] = {
    "template": {"kind": "template", "retrieval": False, "patcher": True},
    "on-on": {"kind": "model", "retrieval": True, "patcher": True},
    "retrieval-off": {"kind": "model", "retrieval": False, "patcher": True},
    "patcher-off": {"kind": "model", "retrieval": True, "patcher": False},
}
ALIASES = {
    "retrieval-on": "on-on",
    "patcher-on": "on-on",
    "default": "on-on",
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
]


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


def resolve_configs(names: Iterable[str]) -> list[tuple[str, dict[str, Any]]]:
    """Expand aliases, keep order, and drop duplicate configurations."""
    resolved: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[bool, bool, str]] = set()
    for name in names:
        canonical = ALIASES.get(name, name)
        if canonical not in CONFIGS:
            raise ValueError(
                f"unknown config {name!r}; choose from {sorted(CONFIGS)} "
                f"(aliases: {sorted(ALIASES)})"
            )
        spec = CONFIGS[canonical]
        key = (spec["retrieval"], spec["patcher"], spec["kind"])
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
    """Aggregate rows per configuration."""
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
        outcomes: dict[str, int] = {}
        without_candidate = 0
        for row in group:
            outcomes[row["final"]] = outcomes.get(row["final"], 0) + 1
            if not int(row.get("attempts") or 0):
                without_candidate += 1
        summary["configs"][name] = {
            "runs": n,
            "cases": len({row["case_id"] for row in group}),
            "samples": len({row["sample"] for row in group}),
            # A run that never staged a candidate (generation_error /
            # runner_error) says nothing about model quality; keep it out of
            # the candidate denominator instead of silently counting it as a
            # failed generation.
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
            "outcomes": dict(sorted(outcomes.items())),
        }
    return summary


def _markdown_table(summary: dict[str, Any]) -> str:
    header = (
        "| Config | Runs | no candidate | pass@1 | pass@1 95% CI | pass after repair | "
        "after-repair 95% CI | recovered | distinct first | median latency (s) | median tokens |\n"
        "| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |"
    )
    lines = [header]
    for name, stats in summary["configs"].items():
        ci1 = stats["pass_at_1_ci95"]
        cia = stats["pass_after_repair_ci95"]
        lines.append(
            f"| {name} | {stats['runs']} | "
            f"{stats['runs_without_candidate']} | "
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
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases(args.cases)
    if args.case_limit:
        cases = cases[: args.case_limit]
    configs = resolve_configs(
        [name.strip() for name in args.configs.split(",") if name.strip()]
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_dir = out_dir / "specs"
    spec_paths = {case["id"]: write_case_spec(case, spec_dir) for case in cases}

    rows: list[dict[str, Any]] = []
    total = len(configs) * len(cases) * args.samples
    done = 0
    csv_path = out_dir / "runs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # The orchestrator appends to results_log, while the csv is truncated on
    # every invocation. Truncate the jsonl here too so csv, jsonl and
    # summary.json always describe the same set of runs.
    (out_dir / "runs.jsonl").write_text("", encoding="utf-8")

    def _write_summary() -> None:
        summary = summarize(rows)
        summary.update({
            "model": args.model,
            "temperature": args.temperature,
            "max_repairs": args.max_repairs,
            "base_seed": args.base_seed,
            "samples_per_case": args.samples,
            "cases": [case["id"] for case in cases],
            "retrieval_memory_frozen": not args.learn,
        })
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (out_dir / "report.md").write_text(_markdown_table(summary), encoding="utf-8")
        return summary

    # One CSV row per run, flushed immediately so an interrupted batch still
    # leaves auditable partial results.
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        handle.flush()
        for config_name, config_spec in configs:
            for case in cases:
                for sample in range(args.samples):
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

    summary = _write_summary()
    if not args.quiet:
        print()
        print(_markdown_table(summary), end="")
        print(f"\nCSV: {out_dir / 'runs.csv'}")
        print(f"Summary: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
