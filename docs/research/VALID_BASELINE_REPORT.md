# Valid Baseline Report

**Experiment date:** 2026-11-11  
**Experiment commit:** `580323b` (runner hardening; recorded in every `run.json`)  
**Branch:** `research/valid-baseline`  
**Output directory:** `results/bench_baseline_v2/`

This is the first baseline in which the model was actually invoked for every
run. It replaces the earlier "16/24 baseline" as the *provenance-complete*
baseline while preserving the historical artifact in `results/bench/`.

---

## 1. Experiment Provenance

| Parameter | Value |
|-----------|-------|
| **Git commit at run time** | `580323b82ea74259dd7d7f0f0c3ce5f1fe8c8e12` |
| **Model** | `qwen2.5-coder:7b` |
| **Model digest** | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| **Temperature** | `0.25` |
| **Max repairs** | `2` |
| **Base seed** | `42` (samples use seeds 42, 43) |
| **Samples per case** | `2` |
| **Configurations** | `on-on` (retrieval on, patcher on, RTL generator) |
| **Cases** | 12 (`c01`–`c12`) |
| **Total runs** | 24 (12 cases × 2 samples) |
| **Retrieval memory** | Frozen during the batch (`--learn` not passed) |
| **Retrieval corpus hash** | `ee388ce1da2e30c28b229fdba561743a063147d961d3267a0384be310d6eeb22` |
| **Preflight** | Ollama import, server, model, `iverilog`, `vvp`, `verilator`, `yosys` all passed |

The corpus hash covers every example retrieval could serve: the two checked-in
seed references plus four verified learned examples in `results/rtl_memory/`.
It is emitted in `summary.json` by `tools.rtl_memory.retrieval_corpus_hash()` so
future batches can be checked for corpus drift.

**Reproduction command**

```bash
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_baseline_v2 --quiet
```

The first invocation stalled after 15 rows (external process interruption, not a
model failure). The run was completed with the same command plus `--resume`;
the CSV, JSONL and summary were regenerated from the 24 audited rows.

---

## 2. Baseline Results

| Metric | Value |
|--------|-------|
| Total runs | 24 |
| Infrastructure failures | 0 |
| Candidate verification failures | 8 |
| Successful candidates | 16 |
| **pass@1** | **0/24 (0.0%)**, Wilson 95% CI [0.0%, 13.8%] |
| **pass after repair** | **16/24 (66.7%)**, Wilson 95% CI [46.7%, 82.0%] |
| Recovered by repair | 16 |
| Distinct first designs | 23/24 |
| Median latency | 427.709 s |
| Median tokens per run | 9,942.5 |
| Total tokens | 289,827 |
| **Batch valid** | **Yes** |
| **Model invoked** | **Yes** |

### Batch validity

`assess_batch_validity()` returned `(True, "Batch valid")`:

- 0 of 24 runs were infrastructure failures;
- all 24 recorded at least one model attempt;
- the intended model and digest were recorded in each `run.json`.

---

## 3. Per-Case Results

### Single-byte cases (c01–c05, c09, c10, c12): 16/16 after repair

| Case | Description | Runs | pass@1 | Pass after repair |
|------|-------------|-----:|-------:|------------------:|
| c01 | default 1-byte | 2 | 0/2 | 2/2 |
| c02 | alternate address, 1-byte | 2 | 0/2 | 2/2 |
| c03 | zero payload | 2 | 0/2 | 2/2 |
| c04 | all-ones payload | 2 | 0/2 | 2/2 |
| c05 | low address | 2 | 0/2 | 2/2 |
| c09 | single device | 2 | 0/2 | 2/2 |
| c10 | three devices | 2 | 0/2 | 2/2 |
| c12 | top address | 2 | 0/2 | 2/2 |

**Single-byte total: 16/16 (100%) after repair.**

### Multi-byte cases (c06–c08, c11): 0/8 after repair

| Case | Description | Runs | pass@1 | Pass after repair | Failure |
|------|-------------|-----:|-------:|------------------:|---------|
| c06 | two-byte | 2 | 0/2 | 0/2 | simulation_failure (`sim_checks_failed`) |
| c07 | two-byte, alternate address | 2 | 0/2 | 0/2 | simulation_failure (`sim_checks_failed`) |
| c08 | three-byte | 2 | 0/2 | 0/2 | structural_failure (`structural_failed`) |
| c11 | three devices, two-byte | 2 | 0/2 | 0/2 | simulation_failure (`sim_checks_failed`) |

**Multi-byte total: 0/8 (0%) after repair.**

---

## 4. Explicit Outcome Classification

| Classification | Count | Share |
|----------------|------:|------:|
| `pass` | 16 | 66.7% |
| `simulation_failure` | 6 | 25.0% |
| `structural_failure` | 2 | 8.3% |
| `generation_error` | 0 | 0.0% |
| `runner_error` | 0 | 0.0% |
| `compile_failure` | 0 | 0.0% |
| `lint_failure` | 0 | 0.0% |
| `oracle_failure` | 0 | 0.0% |

All eight failures are candidate verification failures. None are
infrastructure failures, and none are counted as model failures caused by the
harness.

---

## 5. Artifact Locations

```
results/bench_baseline_v2/
├── runs.csv              # 24 audited rows (header + 24)
├── runs.jsonl            # 24 derived index entries
├── summary.json          # summary + provenance + corpus hash
├── report.md             # rendered markdown table
├── specs/                # 12 generated case specs (gitignored)
├── runs/                 # 24 run directories (gitignored)
└── traces/               # event traces (gitignored)
```

Each run directory contains `run.json`, `attempt_N.json`, `attempt_N.v`,
`attempt_N.vvp` (when compiled), the prompt, compiler stderr, and simulation
stdout. Raw artifacts stay local; the tracked CSV/JSONL/summary/report are
sufficient to reproduce the aggregate.

---

## 6. Comparison with the Historical Baseline

| Metric | Historical `results/bench/` | Current `results/bench_baseline_v2/` |
|--------|-----------------------------|--------------------------------------|
| Runs | 24 | 24 |
| pass@1 | 16/24 (66.7%) | 0/24 (0.0%) |
| pass after repair | 16/24 (66.7%) | 16/24 (66.7%) |
| Single-byte after repair | 16/16 (100%) | 16/16 (100%) |
| Multi-byte after repair | 0/8 (0%) | 0/8 (0%) |
| Infrastructure failures | 0 | 0 |
| Retrieval corpus hash recorded | No | Yes |

The final pass-after-repair rate is identical, and the split between single-byte
(100%) and multi-byte (0%) cases is identical. The difference is in *when* the
pass happened: the historical batch had 16 first-attempt passes, while this
batch had none and recovered all 16 through repair. Candidate, prompt and
corpus provenance for the historical batch is incomplete, so the two batches
should not be pooled into a single pass@1 estimate.

---

## 7. Conclusions and Limits

1. This is a valid baseline: the model was invoked in all 24 runs and no
   infrastructure failure contributed to the result.
2. Single-byte I²C generation is reliable after repair (16/16); multi-byte
   generation remains the bottleneck (0/8).
3. pass@1 is 0/24 in this batch. The harness should not claim a 66.7% first-try
   rate for this corpus without another valid batch.
4. Best-of-N remains unmeasured. The earlier 48 "Best-of-4" runs are
   infrastructure failures and are archived under
   `results/archive/invalid_bon_runs/`.
5. Before a Best-of-N experiment, Best-of-N orchestration must pass the
   deterministic mock-generator tests in `tests/test_bench_bon_mock.py`.

---

## 8. Reproducibility Check

```bash
cd /home/shrivathssp/Desktop/protoforge
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_repro --quiet
```

Expected: 24 runs, 0 infrastructure failures, batch valid, 16/24 pass after
repair, 0/24 pass@1 on the recorded corpus. Sampling variation may change which
candidates pass, so compare the corpus hash and classification breakdown before
comparing rates.

---

## 9. Validation

| Check | Command | Result |
|-------|---------|--------|
| Full suite | `.venv/bin/python -q -m pytest tests -q` | **230 passed** |
| Static lint | `.venv/bin/python -m pyflakes $(git ls-files '*.py')` | clean |
| Baseline artifacts | `pytest tests/test_valid_baseline_artifacts.py` | 7 passed |
| Best-of-N mocks | `pytest tests/test_bench_bon_mock.py` | 7 passed |
| Template smoke | `bench.run --configs template --samples 1 --case-limit 3` | 3/3 passed |
| RTL verification | `test_oracle_mutation`, `test_e2e_real_sim`, `test_logging_replay`, `test_template_master`, `test_structural_gate`, `test_unit_checks` | 44 passed |

**GitHub Actions:** run
[`43026139295`](https://github.com/codeprabhu/protoforge/actions/runs/43026139295)
on commit `1d56c44f252d2f690d2c37018ccb286c19a26065` — both the test job and
the template smoke job succeeded.

The committed baseline is `results/bench_baseline_v2/`; the archived invalid
Best-of-4 data remains at `results/archive/invalid_bon_runs/bon/`.
