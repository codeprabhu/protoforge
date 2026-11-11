# Valid Baseline Report

**Experiment date:** 2026-11-11
**Baseline commit:** `6f3fc1575e7fc716b3c70c3e243ae505859f6887`
**Branch:** `research/valid-baseline`
**Corrected baseline:** `results/bench_baseline_v3/`
**Pre-fix baseline (evidence only):** `results/bench_baseline_v2/`

---

## 1. Provenance of the corrected baseline

| Parameter | Value |
|-----------|-------|
| Git commit | `6f3fc1575e7fc716b3c70c3e243ae505859f6887` |
| Model | `qwen2.5-coder:7b` |
| Model digest | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| Temperature | `0.25` |
| Max repairs | `2` |
| Base seed | `42` (samples use seeds 42, 43) |
| Samples per case | `2` |
| Config | `on-on` (retrieval on, patcher on, RTL generator) |
| Cases | 12 (`c01`–`c12`) |
| Runs | 24 (12 cases × 2 samples) |
| Retrieval memory | Frozen (no learning during the batch) |
| Retrieval corpus hash | `ee388ce1da2e30c28b229fdba561743a063147d961d3267a0384be310d6eeb22` |
| Preflight | ollama import, server, model, `iverilog`, `vvp`, `verilator`, `yosys` all passed |

Command:

```bash
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_baseline_v3 --quiet
```

Preflight passed, so the model was invoked on every run and the batch is valid:

- runs: 24, `infrastructure_failures`: 0, `model_invoked`: true,
  `batch_valid`: true, `runs_without_candidate`: 0.

---

## 2. Corrected baseline results

| Metric | Value |
|--------|-------|
| Runs | 24 |
| Infrastructure failures | 0 |
| Candidate verification failures | 8 |
| **pass@1 (first attempt)** | **16/24 (66.7%)**, Wilson 95% CI [46.7%, 82.0%] |
| **Pass after repair** | **16/24 (66.7%)**, Wilson 95% CI [46.7%, 82.0%] |
| Recovered by repair | **0** |
| Distinct first designs | 22/24 |
| Median latency | 170.461 s |
| Median tokens per run | 3,487 |
| Total tokens | 174,550 |

Explicit classifications: `pass` 16, `simulation_failure` 4,
`compile_failure` 2, `structural_failure` 2; `generation_error`,
`runner_error`, `lint_failure` and `oracle_failure` are 0.

Pass@1 and pass-after-repair are reported separately. **Every success in this
baseline came on the first attempt; the repair loop recovered nothing.**
The earlier 427–463 s median latency and 16 recoveries were the signature of
the scaffold defect described in §4, not of a model that learns per attempt.

---

## 3. Per-case results (first attempt and after repair reported separately)

### Single-byte (c01–c05, c09, c10, c12) — 16 runs, all first-attempt passes

| Case | Description | Runs | pass@1 | After repair | Attempts |
|------|-------------|-----:|-------:|-------------:|----------|
| c01 | default 1-byte | 2 | 2/2 | 2/2 | 1 |
| c02 | alternate address | 2 | 2/2 | 2/2 | 1 |
| c03 | zero payload | 2 | 2/2 | 2/2 | 1 |
| c04 | all-ones payload | 2 | 2/2 | 2/2 | 1 |
| c05 | low address | 2 | 2/2 | 2/2 | 1 |
| c09 | single device | 2 | 2/2 | 2/2 | 1 |
| c10 | three devices | 2 | 2/2 | 2/2 | 1 |
| c12 | top address | 2 | 2/2 | 2/2 | 1 |

### Multi-byte (c06–c08, c11) — 8 runs, no pass on the first attempt or after repair

| Case | Runs | pass@1 | After repair | Final classification |
|------|-----:|-------:|-------------:|----------------------|
| c06 two-byte | 2 | 0/2 | 0/2 | `compile_failure` (undefined `DATA_BYTE1`) |
| c07 two-byte alt | 2 | 0/2 | 0/2 | `simulation_failure`, `structural_failure` |
| c08 three-byte | 2 | 0/2 | 0/2 | `simulation_failure` |
| c11 three devices two-byte | 2 | 0/2 | 0/2 | `simulation_failure`, `structural_failure` |

The multi-byte failures are still candidate verification failures, not
infrastructure failures: the model produced candidates, they compiled or
elaborated, and the simulation judge rejected them.

---

## 4. Why the numbers changed: two scaffold defects, both fixed

### 4.1 `HALF` was omitted from every first attempt (fixed, regression-tested)

`_normalize_fragment_scaffold` decided that a protocol constant was already
declared if its name appeared **anywhere** in a `wire`/`reg` statement. The
model almost always writes `wire half_done = (half_cnt == HALF - 1);`, so the
`localparam HALF = 4;` line was dropped and every first attempt failed with:

```
error: Unable to bind wire/reg/memory `HALF'
```

Evidence: in the pre-fix baseline (`results/bench_baseline_v2/`) 24/24 first
attempts carry that compiler error, 0/24 pass@1, and 16/24 only pass after
repair. In the corrected baseline 0/24 attempts mention `HALF` in a compiler
error and 24/24 first attempts that reference `half_done` carry exactly one
`localparam HALF = 4;`.

Fix: an already-declared constant now requires a real
`localparam|parameter … NAME =` declaration. Test:
`tests/test_fragment_scaffold.py::test_half_referenced_inside_wire_declaration_is_not_treated_as_declared`.

Independent verification of the fix on the recorded artifacts:

- 0/24 first attempts produced a `HALF` compiler error.
- 0/24 first attempts reference `half_done` without `localparam HALF`.
- Structural gate re-run on the recorded RTL with each run's own device list:
  38 checks passed, 2 failed, both on repaired multi-byte attempts where the
  model itself broke the contract (see §4.2).

### 4.2 Contract-violating repair responses duplicated scaffold-owned code (fixed, regression-tested)

Two recorded multi-byte runs ended `structural_failed` because the repair
response re-emitted `device_sensor`/`device_controller` instance blocks and its
own `assign sda` / `assign scl` drivers. The structural gate requires exactly
one open-drain driver and exactly one instance per device, so the assembled
RTL failed and the defect was charged to the candidate even though the model
had written a valid FSM fragment.

Fix: the normalization path drops model-supplied `assign sda`/`assign scl`
drivers and model-supplied device instance blocks before the deterministic
wrapper is added. Tests:
`tests/test_fragment_scaffold.py::test_model_instance_blocks_and_bus_assigns_are_dropped`
and
`::test_real_failing_repair_artifact_normalizes_cleanly`, which replays the two
recorded failing artifacts and asserts the gate now passes.

**Measured effect** (`results/bench_multibyte_v4/`, same corpus hash, commit
`3954966`, 4 multi-byte cases × 2 samples = 8 runs, batch valid, 0
infrastructure failures): the two `structural_failure` outcomes are gone and
all 8 multi-byte failures are genuine candidate failures — 5
`simulation_failure` and 3 `compile_failure`. Multi-byte is still 0/8, which
means the model does not solve these cases; it no longer fails for reasons the
harness owns.

---

## 5. Reproducibility check

```bash
cd /home/shrivathssp/Desktop/protoforge
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_baseline_v4 --quiet
```

Expected on the recorded corpus: 24 runs, 0 infrastructure failures, batch
valid, 16/24 pass@1, 16/24 after repair, 0 recovery by repair, single-byte
16/16 and multi-byte 0/8. Sampling variation moves individual cases, so
compare the corpus hash and classification breakdown before comparing rates.

## 6. What is and is not established

1. A **valid** baseline exists: model invoked in all 24 runs, no
   infrastructure failure, full provenance.
2. Single-byte I²C generation is **reliable on the first attempt** (16/16).
3. Multi-byte remains **unmeasured as solved**: 0/8 after repair, with a
   mixture of simulation, compile and structural failures.
4. The pre-fix 0/24 pass@1 was a harness defect, not model capability. Do not
   quote it as a capability result.
5. Best-of-N is **still unmeasured**. The archived 48-run "Best-of-4" batch is
   invalid (the model was never invoked) and must not be used for comparisons.
