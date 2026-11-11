# ProtoForge v1 Acceptance Criteria

This document defines the exact criteria that must be met before tagging `v1.0`.
All criteria are verifiable by running the commands listed.

## 1. Test Suite Green in CI

**Criterion:** All tests pass in the CI pipeline on a fresh Ubuntu runner.

**Verification:**
```bash
# Locally (equivalent to CI)
.venv/bin/python -q -m pytest tests -q
.venv/bin/python -m pyflakes $(git ls-files '*.py')
```

**Expected:** `230 passed`, pyflakes clean (0 errors).

**Status:** ✅ **MET** — GitHub Actions run
[`43026139295`](https://github.com/codeprabhu/protoforge/actions/runs/43026139295)
on commit `1d56c44f252d2f690d2c37018ccb286c19a26065` completed with
**success** for both jobs:

- Unit + Integration Tests — success (`230 passed`, pyflakes clean);
- 3-Case Template Benchmark Smoke Test — success (3/3 template cases).

The only commit after the verified SHA records this URL and does not change
code, tests, or benchmark artifacts.

## 2. Every Oracle Mutant Caught

**Criterion:** The mutation test suite rejects all 10 seeded faults.

**Verification:**
```bash
.venv/bin/python -q -m pytest tests/test_oracle_mutation.py -v
```

**Expected:** 14 tests pass (1 golden reference + 10 mutants + 3 plusarg tests).

**Status:** ✅ **MET** — all 14 tests pass.

Mutants verified:
| Mutant | Expected Failure Check |
|--------|------------------------|
| `no_tb_done` | `tb_done` |
| `no_stop` | `stop` |
| `wrong_address` | `address_ack` |
| `wrong_data` | `correct_data` |
| `no_start` | `start` |
| `lsb_first` | `address_ack` |
| `third_byte` | `byte_count` |
| `skip_ack` | `data_ack` |
| `sda_glitch` | `sda_stable` |
| `repeated_start` | `single_start` |

## 3. Benchmark Table Committed and Internally Consistent

**Criterion:** The committed benchmark artifacts (`results/bench/runs.csv`,
`runs.jsonl`, `summary.json`, `report.md`) describe the same 96 runs and
agree with each other.

**Verification:**
```bash
.venv/bin/python -q -m pytest tests/test_bench_artifacts.py -v
```

**Expected:** 4 tests pass (no merge markers, identical run IDs, per-config
outcomes match CSV, report.md is rendered summary).

**Status:** ✅ **MET** — 4 tests pass for the historical `results/bench/`
artifacts.

The provenance-complete baseline in `results/bench_baseline_v2/` has its own
consistency suite (`tests/test_valid_baseline_artifacts.py`, 7 tests): 24 CSV
rows, 24 JSONL entries, explicit outcomes derived from the same
classification function, corpus hash, model digest, commit, batch validity,
and the single-byte/multi-byte split.

**Baseline Table (from `docs/BASELINE.md`):**

| Config | Runs | no candidate | pass@1 | pass@1 95% CI | pass after repair | after-repair 95% CI | recovered | distinct first | median latency (s) | median tokens |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: |
| `template` | 24 | 0 | 24/24 (100.0%) | [86.2%, 100.0%] | 24/24 (100.0%) | [86.2%, 100.0%] | 0 | 12/24 | 0.081 | 0.0 |
| `on-on` | 24 | 0 | 16/24 (66.7%) | [46.7%, 82.0%] | 16/24 (66.7%) | [46.7%, 82.0%] | 0 | 12/24 | 187.238 | 2899.0 |
| `retrieval-off` | 24 | 0 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 24/24 | 243.605 | 7744.0 |
| `patcher-off` | 24 | 19 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 1/5 | 0.0 | 0.0 |
| `on-on-ir` | 24 | 18 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 6/6 | 173.364 | 0.0 |
| `retrieval-off-ir` | 24 | 18 | 0/24 (0.0%) | [0.0%, 13.8%] | 0/24 (0.0%) | [0.0%, 13.8%] | 0 | 6/6 | 203.782 | 0.0 |

## 4. Pass Rate Floor

**Criterion:** The recorded `on-on` first-try pass rate lower bound (Wilson 95% CI)
meets or exceeds the acceptance floor.

**Floor:** **45%** (lower bound of Wilson 95% CI for 16/24 = 66.7%).

**Rationale:** The floor was originally set from the historical `results/bench/`
batch, which recorded 16/24 first-try passes. The provenance-complete baseline
must be judged on its own artifacts, not on the historical batch.

**Verification:**
```bash
.venv/bin/python -c "
from bench.run import wilson_interval
ci = wilson_interval(0, 24)
print(f'Wilson 95% CI for 0/24: [{ci[0]:.1%}, {ci[1]:.1%}]')
assert ci[0] >= 0.45, f'Lower bound {ci[0]:.1%} below floor 45%'
"
```

**Status:** ❌ **UNMET** — The valid baseline (`results/bench_baseline_v2/`)
records **0/24 pass@1**, Wilson 95% CI [0.0%, 13.8%]. The lower bound is 0.0%,
below the 45% floor. The historical 16/24 first-try rate does not satisfy this
criterion because its provenance is incomplete and its corpus hash was not
recorded.

**Post-repair context (not a substitute for this criterion):** the valid
baseline reaches 16/24 (66.7%) after repair, Wilson 95% CI [46.7%, 82.0%]. A
future acceptance criterion may explicitly target post-repair capability, but
that would be a new, documented decision — not a reinterpretation of this one.

## 5. Replay Verification

**Criterion:** A recorded run can be replayed and verdicts match exactly.

**Verification:**
```bash
# Run one template case
.venv/bin/python -m bench.run --configs template --samples 1 --case-limit 1 --out-dir /tmp/verify --quiet
RUN_ID=$(ls /tmp/verify/runs/ | head -1)

# Replay it
.venv/bin/python tools/replay.py /tmp/verify/runs/$RUN_ID --quiet
```

**Expected:** Replay reports `MATCH` and all verdicts identical.

**Status:** ✅ **MET** — verified by `tests/test_logging_replay.py`.

## 6. Oracle Forgery Resistance

**Criterion:** The structural gate rejects DUTs that print forged RESULT lines.

**Verification:**
```bash
.venv/bin/python -q -m pytest tests/test_unit_checks.py::test_forged_result_without_nonce_is_rejected -v
```

**Expected:** Test passes — forged RESULT without nonce is rejected.

**Status:** ✅ **MET** — test passes.

## 7. Valid Baseline Provenance and Batch Validity

**Criterion:** A baseline exists in which the intended model was actually
invoked, the batch is valid, infrastructure and candidate failures are
separated, and the retrieval corpus hash is recorded.

**Verification:**
```bash
.venv/bin/python -q -m pytest tests/test_valid_baseline_artifacts.py -v
.venv/bin/python -q -m pytest tests/test_bench_classifications.py tests/test_bench_bon_mock.py -v
```

**Expected:** 7 baseline-artifact tests, 30 classification/Best-of-N tests pass.

**Status:** ✅ **MET** — `results/bench_baseline_v2/summary.json` records
`batch_valid: true`, `model_invoked: true`, `infrastructure_failures: 0`,
`model_digest`, `git_commit`, and `retrieval_corpus_hash`
`ee388ce1da2e30c28b229fdba561743a063147d961d3267a0384be310d6eeb22`.
The earlier 48-run Best-of-4 batch is archived as invalid
(`results/archive/invalid_bon_runs/`).

## Summary

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Test suite green in CI | ✅ MET (run 43026139295, both jobs success) |
| 2 | Every oracle mutant caught | ✅ MET (14/14) |
| 3 | Benchmark artifacts consistent | ✅ MET (historical 4/4; valid baseline 7/7) |
| 4 | Pass rate floor ≥ 45% | ❌ UNMET (valid baseline pass@1 0/24) |
| 5 | Replay verification | ✅ MET (3/3 tests) |
| 6 | Oracle forgery resistance | ✅ MET (1/1 test) |
| 7 | Valid baseline provenance | ✅ MET (batch valid, corpus hash recorded) |

**Overall:** 6 of 7 acceptance criteria are **MET**. Criterion 4 is **UNMET**:
the valid baseline's first-try pass rate is below the 45% floor.

## Tagging Decision

**Not ready for `v1.0` tagging.** Criterion 4 (pass@1 floor) is UNMET by the
valid baseline. Do not tag until first-try capability improves or a documented
post-repair acceptance criterion is explicitly adopted.

**Next step:** Raise first-try capability, or make a documented decision to
replace the pass@1 floor with a post-repair criterion. Do not reinterpret the
existing floor.