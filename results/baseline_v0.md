# Baseline v0 (real model, 5 runs)

This is the first measured baseline for ProtoForge's I2C task. It is a real
model run through the live pipeline (Ollama + Icarus Verilog), not a template
and not a mocked test. Keep it as the "before" number for later phases.

## What was run

```bash
PROTOFORGE_BATCH_TEMPERATURE=0.25 .venv/bin/python main.py 5
```

The batch process was launched from the Phase 0 working tree (commit
`c55c065`, plus the in-progress Phase 1 edits that were present when the
process started). Later Phase 1-5 commits did not affect the already-running
process. It completed with exit 0.

## Environment

| Component | Value |
| --- | --- |
| Python | 3.14.8 |
| Icarus Verilog / vvp | 13.0 (stable) |
| Verilator | 5.052 2026-09-05 |
| Yosys | 0.66 |
| Ollama | 0.24.0 |
| Model | `qwen2.5-coder:7b` |
| Model digest | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| GPU | RTX 3050 Laptop, 4 GiB (model partly offloaded; slow) |

## Sampling

- Runs: 5, seeds 42, 43, 44, 45, 46.
- Temperature: 0.25 (explicit batch temperature; generation default is 0).
- Retrieval: on (seed + learned examples in the prompt).
- Max repairs: 2 per run (3 attempts maximum).
- Oracle: the pre-Phase 4 testbench/judge (no `sda_stable`/`byte_count`
  checks), so this is the historical baseline, not a template result.

## Per-run results

| Run | Seed | Final | Attempts | First-attempt outcome | Failure checks on repaired attempts |
| --- | ---: | --- | ---: | --- | --- |
| 1 | 42 | `sim_checks_failed` | 3 | compile error | `stop`, `tb_done`, `single_stop` |
| 2 | 43 | `sim_checks_failed` | 3 | protocol failure | `stop`, `single_start`, `single_stop` |
| 3 | 44 | `sim_checks_failed` | 3 | compile error | `stop`, `single_start`, `single_stop` |
| 4 | 45 | `sim_checks_failed` | 3 | compile error | `stop`, `single_start`, `single_stop` |
| 5 | 46 | `pass` | 2 | compile error | recovered after one repair |

First-attempt candidate hashes (16 hex chars):
`04f683cb029c8664`, `bd125176e3d22784`, `bd125176e3d22784`,
`b6b82bfab6466f51`, `9567000be2975d7f`.

## Aggregate

- First-try pass rate: **0/5 (0%)**.
- Pass rate after repair: **1/5 (20%)**.
- Recovered by repair: **1/5**.
- Distinct first-attempt designs: **4/5** (two runs produced the same first
  design, so the batch is not five independent successes even at
  temperature 0.25).
- Compile failures on first attempt: 3/5 (undeclared fragment signals such as
  `half_cnt`/`state`, repaired away).
- Hardware-gate failures: 0/5.
- Generation errors: 0/5.
- Median run latency: ~493 s (range 266-539 s).

## Failure analysis

- Three first attempts did not compile because the model fragment referenced
  signals it had not declared. The repair prompt fixed compilation, but the
  repaired designs still failed the protocol oracle.
- The dominant functional failure is a missing or premature STOP
  (`stop`, `single_stop`), often combined with a repeated START
  (`single_start`) or a missing `tb_done`. This is exactly the kind of
  almost-right design the Phase 4 oracle hardening is meant to catch.
- One run (seed 46) reached a passing design after one repair. That pass is a
  real simulation pass, but it is post-repair success, not first-attempt
  quality.

## Artifacts

- `results/baseline_v0.json` — machine-readable batch summary.
- `results/baseline_v0_runs.jsonl` — the five full run records.
- `results/baseline_batch_stdout.txt` — console summary from the batch.
- `results/baseline_run_once.json` — earlier single real run (compile-fail,
  then repair pass), kept as a smoke-test artifact.

## Caveats

- This baseline predates per-run attempt directories and the Phase 4 oracle
  checks. Do not compare its pass rate directly against runs made with the
  hardened oracle; re-run the baseline after the benchmark runner (Phase 6)
  exists.
- The model ran on a 4 GiB laptop GPU with partial CPU offload, so latency is
  not representative of a larger GPU.
- Five runs are a small sample. The Wilson interval for 1/5 is wide; treat
  20% as a point estimate, not a precise rate.
