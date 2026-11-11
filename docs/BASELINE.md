# Baseline: template upper bound and the small-model benchmark

This is the Phase 6 measurement table. It is the number later phases must beat.
Nothing here is a mocked or substituted result: every row comes from the real
pipeline (Ollama + Icarus Verilog + Verilator + Yosys) or from the deterministic
template.

> **Historical artifact.** These results were recorded before the runner
> recorded a retrieval corpus hash or an explicit failure classification.
> The provenance-complete baseline is in
> `docs/research/VALID_BASELINE_REPORT.md` and `results/bench_baseline_v2/`:
> 0/24 pass@1 and 16/24 (66.7%) after repair, 0 infrastructure failures. Do not
> pool the two batches.

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
| GPU | RTX 3050 Laptop, 4 GiB (partial CPU offload) |

## Task set

`benchmarks/cases.json` has 12 cases varying:

- 7-bit address: `0x03`, `0x10`, `0x21`, `0x2A`, `0x48`, `0x50`, `0x77`, `0x7F`;
- payload length: 1, 2 and 3 data bytes;
- device count: 1, 2 and 3 instances (`device_sensor`, `device_controller`,
  `device_timer`).

`tools/make_spec.py` turns each case into a spec that the existing
`tools.i2c_spec` parser and the model prompt consume. The oracle is the Phase 4
hardened testbench/judge (START/STOP, ACK/NACK, address/data, `sda_stable`,
`byte_count`, plus `+I2C_ADDR`/`+I2C_DATA`/`+I2C_NBYTES`).

## Configurations

The 2x2 ablation shares the on/on cell, so there are four measured rows rather
than five duplicate runs:

| Config | Retrieval | Regex patcher | Model |
| --- | --- | --- | --- |
| `template` | n/a | n/a | none (deterministic emitter) |
| `on-on` | on | on | `qwen2.5-coder:7b` |
| `retrieval-off` | off | on | `qwen2.5-coder:7b` |
| `patcher-off` | on | off | `qwen2.5-coder:7b` |

Retrieval memory is frozen during the benchmark: benchmark runs do not add
passing/failed examples, so later runs cannot learn from earlier benchmark
cases. The on/on row therefore uses the existing seed corpus only.

## Commands

```bash
# Template upper bound, 20 samples per case (240 runs).
.venv/bin/python -m bench.run --configs template --samples 20 --out-dir results/bench_template_20x

# Real-model matrix, 2 samples per case (24 trials per configuration).
.venv/bin/python -m bench.run \
  --configs template,on-on,retrieval-off,patcher-off \
  --samples 2 --out-dir results/bench
```

Each row records config, case, sample, seed, model, temperature, max repairs,
retrieval/patcher flags, final outcome, first-attempt pass, repair recovery,
attempt count, latency, prompt/completion/total tokens, first-attempt hash and
run directory. The runner flushes every row, so an interrupted batch still has
auditable partial results.

## Template upper bound (240 runs)

Source: `results/bench_template_20x/`.

| Config | Runs | pass@1 | pass@1 95% CI | pass after repair | distinct first designs | median latency |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| `template` | 240 | 240/240 (100%) | [98.4%, 100%] | 240/240 (100%) | 12/240 (one per case) | 0.086 s |

The template is deterministic, so its 12 unique first designs are the 12 case
specs, not 240 independent successes. This row proves the oracle accepts a
correct design for every benchmark case, including the 2- and 3-byte tasks.

## Real-model matrix

Source: `results/bench/` (`runs.csv`, `runs.jsonl`, `summary.json`, `report.md`). All four files are regenerated from the same 96 run directories (`results/bench/runs/*/run.json`); a merge had left conflict markers in the index and a stale 36-row CSV, so the index was rebuilt from the per-run evidence and the table below is now internally consistent.

| Config | Runs | no candidate | pass@1 | pass@1 95% CI | pass after repair | after-repair 95% CI | recovered | distinct first | median latency (s) | median tokens |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |
| `template` | 24 | 0 | 24/24 (100.0%) | [86.2%, 100.0%] | 24/24 (100.0%) | [86.2%, 100.0%] | 0 | 12/24 | 0.081 | 0.0 |
| `on-on` | 24 | 0 | 16/24 (66.7%) | [46.7%, 82.0%] | 16/24 (66.7%) | [46.7%, 82.0%] | 0 | 12/24 | 187.238 | 2899.0 |
| `retrieval-off` | 24 | 0 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 24/24 | 243.605 | 7744.0 |
| `patcher-off` | 24 | 19 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 1/5 | 0.0 | 0.0 |
| `on-on-ir` | 24 | 18 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 6/6 | 173.364 | 0.0 |
| `retrieval-off-ir` | 24 | 18 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 6/6 | 203.782 | 0.0 |

**Reading the table.** `on-on` is the baseline to beat: 16 of 24 first
attempts pass and repair recovers nothing, so the model's first answer is the
whole result. Turning retrieval off drops to 0/24 with 24 distinct first
designs, i.e. the model still writes 24 different answers but none is a valid
master. `patcher-off` is dominated by a harness failure: 19 runs raised before
the first gate and are recorded as `runner_error` (no candidate), and the
remaining 5 failed structural checks. Those 19 are **not** model failures and
must not be read as a 0% generation rate; the repair-crash guard added in
steps 86–89 now records the same fault as `repair_error` with the triggering
stage's outcome instead of losing the run.

**IR comparison (steps 91–92).** The `--ir` benchmark (72 runs, same 12 cases,
2 samples, same model/seed/temperature) shows the JSON path fails to close the
gap: `on-on-ir` is 0/24 with 18 runs that never produced a candidate because
the model omitted `drive sda="bit"` — the one thing the compiler cannot
invent. Enrichment fills `otherwise`/`load_address`/`restart_bits`/`done`, but
the six candidates that did compile still failed the judge on address/data/ACK/
STOP timing, and repair then produced invalid tables (recorded as `repair_error`).
The RTL path's 66.7% pass rate is not improved; the IR path is **not kept**.

**Note:** The `on-on-ir` and `retrieval-off-ir` configurations in the table above
each completed 24 runs (12 cases × 2 samples), for 48 IR-path runs total.
`on-on-ir`: 18 generation errors (model omitted `drive sda="bit"`), 6 simulation
failures (compiled but failed judge). `retrieval-off-ir`: 18 generation errors,
6 simulation failures. These 48 IR runs are complete for their configurations
but the IR path was abandoned (0/24 pass@1) and is not a validated benchmark.

## Metric definitions

- **pass@1** — the first generated candidate passed every gate with no repair.
- **pass after repair** — the final candidate passed within the repair budget
  (`max_repairs=2`, so at most 3 attempts).
- **recovered by repair** — passed only after at least one repair.
- **distinct first designs** — unique SHA-256 hashes of first-attempt source.
  Repeated outputs are not counted as independent successes.
- **Wilson 95% CI** — binomial score interval; with small samples the interval
  is wide and the point estimate should not be over-read.
- **tokens** — sum of Ollama `prompt_eval_count` + `eval_count` across all
  attempts in a run, read from the per-attempt records.

## Caveats

- The model still receives a one-byte-oriented prompt scaffold; the spec text
  describes multi-byte payloads, but the generator does not yet specialize all
  data literals. Multi-byte cases therefore measure the current gap rather than
  a solved capability.
- The 4 GiB laptop GPU is slow and partially CPU-offloaded; latency is not
  representative of a larger machine.
- Wilson intervals at 24 trials per configuration are wide. Treat the rates as
  point estimates and compare configurations by the same cases and seeds.
- The benchmark freezes retrieval memory; a live run that learns from earlier
  successes may behave differently.
