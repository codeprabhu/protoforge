"""Best-of-N runner for ProtoForge.

Runs N independent candidates per (config, case, sample) in separate run directories,
executes their gates sequentially, and reports the first passing candidate.
If all N candidates fail, the last candidate's failure is recorded.

This follows the Phase 8 Best-of-N protocol (WORKFLOW.md steps 93-95).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.agent import run_once  # noqa: E402
from agent_core.config import Config  # noqa: E402
from bench.run import (  # noqa: E402
    CSV_FIELDS,
    _token_counts,
    _write_csv,
    case_device_paths,
    load_cases,
    resolve_configs,
    summarize,
    write_case_spec,
    preflight_check,
    classify_final_result,
)
from tools.make_spec import DEFAULT_CASES  # noqa: E402


def _write_summary(out_dir: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    """Write summary.json, report.md, and runs.jsonl from rows."""
    summary = summarize(rows)
    summary.update({
        "model": args.model,
        "temperature": args.temperature,
        "max_repairs": args.max_repairs,
        "base_seed": args.base_seed,
        "samples_per_case": args.samples,
        "cases": [case["id"] for case in args.cases],
        "retrieval_memory_frozen": not args.learn,
        "bon": args.n,
    })
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_markdown_table(summary), encoding="utf-8")
    (out_dir / "runs.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in _entries_from_rows(rows)),
        encoding="utf-8",
    )
    return summary


def _entries_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert rows to JSONL index entries."""
    from bench.run import _run_started_at
    return [
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


def _markdown_table(summary: dict[str, Any]) -> str:
    """Reuse the markdown table from bench.run."""
    from bench.run import _markdown_table
    return _markdown_table(summary)


def run_one_bon(
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
    learn: bool,
    n: int,
) -> dict[str, Any]:
    """Run Best-of-N for a single (config, case, sample).

    Generates up to N independent candidates, runs gates on each until one passes
    or all are exhausted. Returns the first passing candidate's result, or the
    last candidate's failure if all fail.
    """
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
        from baseline.template_master import generate_template_rtl
        hooks["generate_fn"] = lambda devices, spec_path, **kw: generate_template_rtl(
            device_paths=list(devices), spec_path=spec_path
        )
    elif config_spec.get("generator") == "ir":
        from agent.ir_generator import generate_fsm_rtl, repair_fsm_rtl
        hooks["generate_fn"] = generate_fsm_rtl
        hooks["repair_fn"] = repair_fsm_rtl
    if not learn:
        hooks["on_success"] = lambda *args, **kwargs: None
        hooks["on_failure"] = lambda *args, **kwargs: None

    first_pass_row: Optional[dict[str, Any]] = None
    all_rows: List[dict[str, Any]] = []

    for candidate_idx in range(n):
        # Create a unique run_id for this candidate by adding candidate index to seed
        candidate_seed = seed + candidate_idx * 1000
        candidate_config = Config(
            device_paths=run_config.device_paths,
            spec_path=run_config.spec_path,
            max_repairs=run_config.max_repairs,
            model=run_config.model,
            seed=candidate_seed,
            temperature=temperature,
            retrieval=run_config.retrieval,
            patcher=run_config.patcher,
            output_root=str(out_dir / "runs"),
            results_log=str(out_dir / "runs.jsonl"),
            trace_dir=str(out_dir / "traces"),
        )

        # Run the candidate
        try:
            log = run_once(
                model=candidate_config.model,
                config=candidate_config,
                **hooks,
            )
        except Exception as exc:
            row = {
                "config": config_name,
                "case_id": case["id"],
                "sample": sample,
                "seed": candidate_seed,
                "model": candidate_config.model,
                "temperature": temperature,
                "max_repairs": max_repairs,
                "retrieval": config_spec["retrieval"],
                "patcher": config_spec["patcher"],
                "generator": config_spec["generator"],
                "final": "runner_error",
                "first_attempt_pass": False,
                "recovered_after_repair": False,
                "attempts": 0,
                "elapsed_sec": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "first_sha256": "",
                "run_id": "",
                "run_dir": "",
                "error": f"{type(exc).__name__}: {exc}",
                # Explicit classification
                "explicit_classification": "runner_error",
            }
            all_rows.append(row)
            if first_pass_row is None and candidate_idx == n - 1:
                first_pass_row = row
            continue

        prompt_tokens, completion_tokens = _token_counts(log.get("run_dir"))
        attempts = log.get("attempts") or []
        first_sha = attempts[0].get("sha256", "") if attempts else ""

        final_status = log.get("final", "unknown")
        explicit_class = classify_final_result(final_status, log.get("generation_error") or log.get("tool_error") or log.get("repair_error") or "")
        
        row = {
            "config": config_name,
            "case_id": case["id"],
            "sample": sample,
            "seed": candidate_seed,
            "model": candidate_config.model,
            "temperature": temperature,
            "max_repairs": max_repairs,
            "retrieval": config_spec["retrieval"],
            "patcher": config_spec["patcher"],
            "generator": config_spec["generator"],
            "final": final_status,
            "first_attempt_pass": bool(log.get("first_attempt_pass", False)),
            "recovered_after_repair": bool(log.get("recovered_after_repair", False)),
            "attempts": len(attempts),
            "elapsed_sec": log.get("elapsed_sec", 0.0),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "first_sha256": first_sha,
            "run_id": log.get("run_id", ""),
            "run_dir": log.get("run_dir", ""),
            "error": log.get("generation_error") or log.get("tool_error") or log.get("repair_error") or "",
            # Explicit classification
            "explicit_classification": explicit_class,
        }
        all_rows.append(row)

        # Check if this candidate passed
        if log.get("final") == "pass":
            if first_pass_row is None:
                first_pass_row = row
            # First pass wins - stop generating more candidates
            break

    # If no candidate passed, return the last candidate's result
    if first_pass_row is None:
        first_pass_row = all_rows[-1]

    # Mark which candidate was the winner
    first_pass_row["bon_candidate_index"] = all_rows.index(first_pass_row)
    first_pass_row["bon_total_candidates"] = len(all_rows)

    return first_pass_row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Best-of-N benchmark runner")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--samples", type=int, default=2, help="runs per case (per candidate)")
    parser.add_argument("--base-seed", type=int, default=42)
    parser.add_argument("--temperature", type=float, default=0.25)
    parser.add_argument("--max-repairs", type=int, default=2)
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument(
        "--configs",
        default="on-on",
        help="comma-separated config names (default: on-on only)",
    )
    parser.add_argument("--out-dir", default="results/bon")
    parser.add_argument(
        "--n",
        type=int,
        default=4,
        help="number of candidates per case (Best-of-N)",
    )
    parser.add_argument(
        "--case-limit",
        type=int,
        default=0,
        help="only the first N cases (0 = all)",
    )
    parser.add_argument(
        "--learn",
        action="store_true",
        help="let benchmark runs update retrieval memory (default: frozen)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.case_limit:
        cases = cases[: args.case_limit]
    args.cases = cases  # Store for summary

    configs = resolve_configs(
        [name.strip() for name in args.configs.split(",") if name.strip()],
        ir=False,  # Bon only on RTL path for now
    )

    # PREFLIGHT CHECK - Run before any model experiments
    # Skip preflight for template-only configs (no model needed)
    model_configs = [name for name, spec in configs if spec["kind"] == "model"]
    if model_configs:
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
    spec_dir.mkdir(exist_ok=True)
    spec_paths = {case["id"]: write_case_spec(case, spec_dir) for case in cases}

    rows: list[dict[str, Any]] = []
    total = len(configs) * len(cases) * args.samples * args.n
    done = 0
    csv_path = out_dir / "runs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        handle.flush()

        for config_name, config_spec in configs:
            for case in cases:
                for sample in range(args.samples):
                    seed = args.base_seed + sample
                    row = run_one_bon(
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
                        n=args.n,
                    )
                    rows.append(row)
                    writer.writerow(row)
                    handle.flush()
                    done += 1
                    if not args.quiet:
                        print(
                            f"[{done}/{total}] {config_name} {case['id']} "
                            f"sample={sample} candidate={row.get('bon_candidate_index', '?')+1}/{row.get('bon_total_candidates', '?')} "
                            f"seed={seed} -> {row['final']} ({row.get('explicit_classification', '?')})",
                            flush=True,
                        )

    _write_csv(csv_path, rows)
    summary = _write_summary(out_dir, rows, args)

    # Print batch validity summary
    if not args.quiet:
        print()
        print(_markdown_table(summary), end="")
        print(f"\nCSV: {out_dir / 'runs.csv'}")
        print(f"Summary: {out_dir / 'summary.json'}")
        
        # Print batch validity for each config
        for config_name, config_stats in summary.get("configs", {}).items():
            batch_valid = config_stats.get("batch_valid", True)
            batch_reason = config_stats.get("batch_invalid_reason", "Batch valid")
            status = "VALID" if batch_valid else "INVALID"
            print(f"Batch validity [{config_name}]: {status} - {batch_reason}")
            
            # Print explicit outcome breakdown
            explicit = config_stats.get("explicit_outcomes", {})
            if explicit:
                print(f"  Explicit outcomes: {dict(sorted(explicit.items()))}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())