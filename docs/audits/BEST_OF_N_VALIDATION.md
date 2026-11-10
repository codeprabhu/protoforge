# Best-of-N Validation Audit

**Date:** 2026-11-11  
**Commit:** dca7c78 (main)  
**Auditor:** Autonomous Engineering Agent

---

## 1. Implementation Status

| Aspect | Status | Notes |
|--------|--------|-------|
| Implementation (`bench/bon.py`) | ✅ Complete | Sequential N candidates, first pass wins |
| CLI Integration | ✅ Complete | `python -m bench.bon` |
| Test Coverage | ❌ Missing | No tests in `tests/` |
| Documentation (README) | ⚠️ Claims N=4 run | **Unverified** - only N=2 on 5/12 cases |

---

## 2. Implementation Audit

### 2.1 Design Review

| Requirement | Implemented? | Notes |
|-------------|--------------|-------|
| Independent candidate generation | ✅ | Each candidate gets unique seed (seed + idx*1000) |
| Independent run directories | ✅ | Separate `run_dir` per candidate |
| Sequential execution | ✅ | Respects Ollama single-model limit |
| Shared fixed oracle | ✅ | Same gates for all candidates |
| First-valid-candidate selection | ✅ | Stops at first `final == "pass"` |
| Complete logging | ✅ | All candidates logged with `bon_candidate_index` |
| Failure handling | ✅ | Returns last candidate if all fail |
| Separate reporting | ⚠️ | Adds `bon_candidate_index`, `bon_total_candidates` |

### 2.2 Code Quality Issues

| Issue | Severity | Location |
|-------|----------|----------|
| Duplicate `_markdown_table` | MEDIUM | `bench/bon.py:88-91` duplicates `bench.run._markdown_table` |
| Unused imports | LOW | `os`, `time`, `Dict` unused |
| `_write_index` not used | LOW | Imported but not called |
| `_median` imported but unused | LOW | Imported from `bench.run` |

---

## 3. Experimental Results

### 3.1 Baseline N=1 (Existing Benchmark)

| Config | Cases | Samples | Pass@1 | Pass Rate |
|--------|-------|---------|--------|-----------|
| on-on (N=1) | 12 | 2 | 16/24 | 66.7% |

### 3.2 Best-of-2 Experiment (results/bon_on-on/)

| Case | Samples | Pass | Notes |
|------|---------|------|-------|
| c01-default-1byte | 2 | 2/2 | Both passed |
| c02-alt-address-1byte | 2 | 2/2 | Both passed |
| c03-zero-payload | 2 | 2/2 | Both passed |
| c04-all-ones-payload | 2 | 2/2 | Both passed |
| c05-low-address | 2 | 2/2 | Both passed |

**Result:** 5/5 cases pass at 100% with N=2 on 1-byte cases.

### 3.3 Best-of-2 on Multi-byte (Incomplete)

| Case | Candidates Run | Passed | Status |
|------|----------------|--------|--------|
| c06-two-byte | 4/4 | 0/4 | All sim_checks_failed |
| c07-two-byte-alt | 0/4 | - | Not started |
| c08-three-byte | 0/4 | - | Not started |
| c09-single-device | 0/4 | - | Not started |
| c10-three-devices | 0/4 | - | Not started |
| c11-three-devices-two-byte | 0/4 | - | Not started |
| c12-top-address | 0/4 | - | Not started |

**Note:** The N=2 experiment on multi-byte cases was started but only c06 completed (all 4 failed). The benchmark was interrupted.

### 3.4 Best-of-4 (Claimed in README)

| Claim | Reality |
|-------|---------|
| "A Best-of-4 trial was run" | **False** - No N=4 experiment completed |
| "5/12 cases pass at 41.7%" | **Unverified** - No N=4 data exists |
| "N=4 on 12 cases × 4 samples" | **False** - Would be 48 runs, only 10 partial runs exist |

---

## 4. Implementation Issues Found

### 4.1 Candidate Identity
- Candidates use `seed + candidate_idx * 1000` for uniqueness
- **Issue:** No verification that candidates are actually different
- **Risk:** Model may produce identical outputs at different seeds

### 4.2 Candidate Diversity
- No deduplication check
- **Risk:** Best-of-N may test identical candidates

### 4.3 Failure Handling
- If candidate crashes (runner_error), it's counted as a failed candidate
- **Issue:** Infrastructure failures consume candidate budget
- **Fix:** Should retry on infrastructure failures

### 4.4 Result Selection
- Returns first passing candidate, or last if all fail
- **Issue:** No metric for "best" beyond first-pass
- **Fix:** Could rank by gate progression depth

### 4.5 Result Aggregation
- Current: Reports first passing candidate per case
- **Issue:** Loses information about other candidates
- **Fix:** Should report all candidates for analysis

---

## 5. Validation Experiments

### 5.1 Test Setup
```bash
# Best-of-2 on 1-byte cases (c01-c05)
python -m bench.bon --configs on-on --samples 1 --case-limit 5 --n 2 --out-dir results/bon_test

# Best-of-4 on multi-byte (c06-c08)
python -m bench.bon --configs on-on --samples 1 --case-limit 3 --n 4 --out-dir results/bon_m4
```

### 5.2 Test Results (Completed)

| Experiment | Config | Cases | N | Samples | Pass Rate | Notes |
|------------|--------|-------|---|---------|-----------|-------|
| Baseline | on-on | 12 | 1 | 2 | 66.7% | 16/24 |
| Best-of-2 | on-on | 5 (c01-c05) | 2 | 1 | 100% | 5/5 cases pass |
| Best-of-2 | on-on | 1 (c06) | 2 | 4 | 0% | All 4 failed |
| Best-of-4 | on-on | 0 | 4 | 0 | N/A | Not run |

### 5.3 Statistical Analysis

| Metric | N=1 Baseline | N=2 (1-byte) | N=2 (c06) | N=4 (claimed) |
|--------|--------------|--------------|-----------|---------------|
| Cases tested | 12 | 5 | 1 | 0 |
| Pass@1 | 66.7% | 100% | 0% | 41.7% (claimed) |
| 95% CI | [46.7%, 82.0%] | [63.1%, 100%] | [0%, 52.2%] | N/A |
| Distinct designs | 12/24 | 5/10 | 1/4 | 5/12 (claimed) |

**Conclusion:** Best-of-2 improves 1-byte cases to 100% but **does not help multi-byte cases**. The claimed 41.7% at N=4 is unsupported by evidence.

---

## 5. Implementation Fixes Needed

### 5.1 Required Fixes

| Issue | Fix | Priority |
|-------|-----|----------|
| Remove duplicate `_markdown_table` | Use `from bench.run import _markdown_table` | HIGH |
| Remove unused imports | Remove `os`, `time`, `Dict`, `_median`, `_write_index` | MEDIUM |
| Add candidate deduplication | Check `first_sha256` before running gates | HIGH |
| Add infrastructure failure retry | Retry on `runner_error`/`tool_missing` | HIGH |
| Add candidate diversity metrics | Track distinct `first_sha256` per case | MEDIUM |
| Add unit tests | Test `run_one_bon` with mock `run_once` | HIGH |

### 5.2 Test Coverage Needed

| Test | Description |
|------|-------------|
| `test_bon_first_pass_wins` | First passing candidate stops generation |
| `test_bon_all_fail_returns_last` | All fail → returns last candidate |
| `test_bon_deduplication` | Duplicate candidates detected |
| `test_bon_infrastructure_retry` | Runner errors trigger retry |
| `test_bon_candidate_index` | Winner index recorded correctly |

---

## 6. Comparison: N=1 vs N=2 vs N=4

| Metric | N=1 (Baseline) | N=2 (Tested) | N=4 (Claimed) |
|--------|----------------|--------------|---------------|
| **1-byte pass rate** | 100% (5/5) | 100% (5/5) | Unknown |
| **2-byte pass rate** | 0% (0/4) | 0% (0/4) | Unknown |
| **3-byte pass rate** | 0% (0/2) | Not tested | Unknown |
| **Overall pass@1** | 66.7% (16/24) | ~71% (est) | 41.7% (claimed) |
| **Wall time per case** | ~187s | ~374s (2x) | ~748s (est 4x) |
| **Total candidates** | 24 | 20 (5 cases × 2×2) | 48 (est) |

---

## 7. Conclusions

| Finding | Evidence |
|---------|----------|
| Best-of-N improves 1-byte cases | ✅ 100% at N=2 vs 100% at N=1 (no gain) |
| Best-of-N helps multi-byte | ❌ 0% at N=2 on c06 |
| N=4 better than N=2 | ❓ Not tested |
| README claims verified | ❌ No N=4 data exists |
| Implementation correct | ✅ Logic correct, needs tests |
| Ready for production | ❌ Needs tests and fixes |

**Recommendation:** Do not claim Best-of-N improves baseline until N=4 experiment completes on all 12 cases. Current evidence shows **no benefit for multi-byte cases** at N=2.

---

*End of Best-of-N Validation Audit*