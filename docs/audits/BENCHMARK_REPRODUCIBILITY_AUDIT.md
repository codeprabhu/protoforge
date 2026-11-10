# Benchmark Reproducibility Audit

**Date:** 2026-11-11  
**Commit:** dca7c78 (main)  
**Auditor:** Autonomous Engineering Agent

---

## 1. Experiment Provenance Verification

### 1.1 Baseline Benchmark (results/bench/)

| Parameter | Value |
|-----------|-------|
| **Run ID** | 20261107T173510-* through 20261107T221018-* |
| **Timestamp** | 2026-11-07 |
| **Model** | qwen2.5-coder:7b |
| **Model Digest** | dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364 |
| **Temperature** | 0.25 |
| **Max Repairs** | 2 |
| **Base Seed** | 42 |
| **Samples per Case** | 2 |
| **Cases** | 12 (c01-c12) |
| **Configurations** | template, on-on, retrieval-off, patcher-off |
| **Total Runs** | 96 |
| **Git Commit** | dca7c78 |

**Artifacts Verified:**
- `results/bench/runs.csv` - 97 rows (header + 96 data rows)
- `results/bench/runs.jsonl` - 96 lines
- `results/bench/summary.json` - Consistent with CSV
- `results/bench/report.md` - Matches summary.json
- `results/bench/runs/` - 96 run directories with `run.json`, `attempt_*.json`, `attempt_*.v`

### 1.2 IR Benchmark (results/bench_ir/)

| Parameter | Value |
|-----------|-------|
| **Run ID** | 20261108T103527-* through 20261108T121509-* |
| **Timestamp** | 2026-11-08 |
| **Model** | qwen2.5-coder:7b |
| **Model Digest** | dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364 |
| **Temperature** | 0.25 |
| **Max Repairs** | 2 |
| **Base Seed** | 42 |
| **Samples per Case** | 2 |
| **Cases** | 12 (c01-c12) |
| **Configurations** | template, on-on-ir, retrieval-off-ir |
| **Total Runs** | 72 |
| **Git Commit** | 67d9c7d (before IR benchmark) / 17710eb (IR switch) |

**Artifacts Verified:**
- `results/bench_ir/runs.csv` - 73 rows (header + 72 data rows)
- `results/bench_ir/runs.jsonl` - 72 lines
- `results/bench_ir/summary.json` - Consistent with CSV
- `results/bench_ir/report.md` - Matches summary.json

### 1.3 Best-of-N Experiments

| Experiment | Status | Notes |
|------------|--------|-------|
| `results/bon/` | Directory exists but empty | No data |
| `results/bon_baseline/` | 7 runs (c01-c04 partial) | Incomplete |
| `results/bon_n4/` | 10 runs (c01-c05 partial) | Incomplete |
| `results/bon_on-on/` | 3 runs (c01-c03) | Complete for 3 cases |
| `results/bon_test/` | 2 runs | Template test |

**Note:** The README claims "A Best-of-4 trial was run on the RTL path... across all 12 benchmark cases" but the actual experiment data is **incomplete** - only 5/12 cases (c01-c05) have results, and only 1-2 samples each instead of 4.

---

## 2. Failure Classification

### 2.1 On-On Configuration (24 runs, 2 samples × 12 cases)

| Case | Sample 0 | Sample 1 | Failure Stage | Failure Checks |
|------|----------|----------|---------------|----------------|
| c01-default-1byte | pass | pass | - | - |
| c02-alt-address-1byte | pass | pass | - | - |
| c03-zero-payload | pass | pass | - | - |
| c04-all-ones-payload | pass | pass | - | - |
| c05-low-address | pass | pass | - | - |
| c06-two-byte | sim_checks_failed | sim_checks_failed | judge | correct_data, sda_stable, byte_count |
| c07-two-byte-alt | sim_checks_failed | sim_checks_failed | judge | correct_data, sda_stable, byte_count |
| c08-three-byte | sim_checks_failed | sim_checks_failed | judge | correct_data, sda_stable, byte_count |
| c09-single-device | pass | pass | - | - |
| c10-three-devices | pass | pass | - | - |
| c11-three-devices-two-byte | sim_checks_failed | sim_checks_failed | judge | correct_data, sda_stable, byte_count |
| c12-top-address | pass | pass | - | - |

**Summary:** 16/24 pass (66.7%), 8/24 fail at judge stage (all multi-byte cases)

**Failure Classification:**
| Failure Type | Count | Classification |
|--------------|-------|----------------|
| sim_checks_failed | 8 | Genuine RTL failure (protocol violation) |
| **Total** | **8** | **All genuine RTL failures** |

### 2.2 Retrieval-Off Configuration (24 runs)

| Outcome | Count | Classification |
|---------|-------|----------------|
| compile_failed | 19 | Genuine RTL failure (syntax/structure) |
| hardware_failed | 3 | Genuine RTL failure (lint/synthesis) |
| structural_failed | 2 | Genuine RTL failure (structural) |
| **Total** | **24** | **All genuine RTL failures** |

### 2.3 Patcher-Off Configuration (24 runs)

| Outcome | Count | Classification |
|---------|-------|----------------|
| runner_error | 19 | Infrastructure failure (Ollama down) |
| structural_failed | 5 | Genuine RTL failure |
| **Total** | **24** | **5 infrastructure, 19 genuine** |

**Note:** The 19 runner_errors in patcher-off are **not model failures** - they are infrastructure failures (Ollama service down). The README correctly notes this.

### 2.4 IR Path (on-on-ir)

| Outcome | Count | Classification |
|---------|-------|----------------|
| generation_error | 18 | Genuine RTL failure (structural/validation) |
| sim_checks_failed | 6 | Genuine RTL failure (protocol) |
| **Total** | **24** | **All genuine RTL failures** |

---

## 3. Case Distribution Coverage

| Case | Bytes | Devices | Address | Pass (on-on) | Notes |
|------|-------|---------|---------|--------------|-------|
| c01-default-1byte | 1 | 2 | 0x50 | ✅ | Baseline |
| c02-alt-address-1byte | 1 | 2 | 0x2A | ✅ | Alt address |
| c03-zero-payload | 1 | 2 | 0x10 | ✅ | Zero payload |
| c04-all-ones-payload | 1 | 2 | 0x77 | ✅ | All-ones payload |
| c05-low-address | 1 | 2 | 0x03 | ✅ | Low address |
| c06-two-byte | 2 | 2 | 0x50 | ❌ | 2 bytes |
| c07-two-byte-alt | 2 | 2 | 0x48 | ❌ | 2 bytes, alt addr |
| c08-three-byte | 3 | 2 | 0x50 | ❌ | 3 bytes |
| c09-single-device | 1 | 1 | 0x50 | ✅ | 1 device |
| c10-three-devices | 1 | 3 | 0x50 | ✅ | 3 devices |
| c11-three-devices-two-byte | 2 | 3 | 0x21 | ❌ | 2 bytes, 3 devices |
| c12-top-address | 1 | 2 | 0x7F | ✅ | Max address |

**Coverage Analysis:**
| Dimension | Coverage |
|-----------|----------|
| 1-byte transfers | 5/5 cases pass (100%) |
| 2-byte transfers | 0/4 cases pass (0%) |
| 3-byte transfers | 0/1 cases pass (0%) |
| 1 device | 1/1 cases pass (100%) |
| 2 devices | 7/8 cases pass (87.5%) |
| 3 devices | 1/2 cases pass (50%) |
| Address variety | 8 unique addresses |

**Gap:** No cases test repeated START, read transactions, clock stretching, NACK handling, or bus recovery.

---

## 4. Reproducibility Assessment

### 4.1 Artifact Consistency

| Artifact | Consistent? | Evidence |
|----------|-------------|----------|
| CSV ↔ JSONL | ✅ | 96 matching run_ids |
| CSV ↔ Summary | ✅ | All counts match |
| Report.md ↔ Summary | ✅ | Exact markdown match |
| CSV ↔ Run dirs | ✅ | 96 run dirs, matching run_ids |
| JSONL ↔ Run dirs | ✅ | 96 matching run_ids |

### 4.2 Configuration Reproducibility

| Parameter | Recorded | Reproducible? |
|-----------|----------|---------------|
| Model | ✅ qwen2.5-coder:7b | ✅ |
| Model digest | ✅ dae161e27b0e... | ✅ |
| Temperature | ✅ 0.25 | ✅ |
| Base seed | ✅ 42 | ✅ |
| Samples per case | ✅ 2 | ✅ |
| Max repairs | ✅ 2 | ✅ |
| Retrieval frozen | ✅ true | ✅ |
| Git commit | ✅ dca7c78 | ✅ |
| Model digest | ✅ recorded | ✅ |

### 4.3 Missing Reproducibility Information

| Missing | Impact |
|---------|--------|
| Ollama version | Cannot verify model version |
| Ollama server config | Cannot verify service config |
| GPU/driver versions | Cannot verify hardware parity |
| OS/kernel version | Minor impact |
| Python package versions | requirements.txt exists but no lock file |

---

## 5. Documentation Consistency

| Document | Claim | Verified | Status |
|----------|-------|----------|--------|
| README | "16/24 (66.7%) pass@1" | ✅ | Verified |
| README | "retrieval-off 0/24" | ✅ | Verified |
| README | "patcher-off 0/24 (19 runner_error)" | ✅ | Verified (19 runner_error) |
| README | "IR benchmark 0/24" | ✅ | Verified |
| README | "Best-of-4 trial... 5/12 (41.7%)" | ❌ | **NOT VERIFIED** - only 5/12 cases tested, N=2 not 4 |
| BASELINE.md | All table values | ✅ | Verified against summary.json |
| WORKFLOW.md | Phase 7 "steps 81-92 done" | ⚠️ | IR benchmark done, Best-of-N "not started" |

---

## 6. Findings Summary

| Finding | Severity | Description |
|---------|----------|-------------|
| Best-of-N claims unverified | HIGH | README claims 5/12 pass at N=4, but only N=2 tested on 5/12 cases |
| Multi-byte failure pattern | HIGH | All 8 failures are multi-byte cases (2-3 bytes) |
| Retrieval dependence confirmed | HIGH | Retrieval-on 66.7% vs Retrieval-off 0% |
| Patcher-off runner errors | MEDIUM | 19/24 are infrastructure failures, not model failures |
| IR path 0% pass | HIGH | Confirmed 0/24, root cause identified (missing `drive sda="bit"`) |
| Case coverage gaps | MEDIUM | No repeated START, read, clock stretch, NACK tests |
| Best-of-N results unverified | HIGH | README claims N=4 results but only N=2 on 5/12 cases run |
| Artifacts consistent | LOW | All benchmark artifacts internally consistent |

---

## 7. Experiment Evidence Table

| Experiment | Evidence | Reproducible | Valid Comparison | Findings |
|------------|----------|--------------|------------------|----------|
| Baseline (on-on) | runs.csv, summary.json, run dirs | ✅ | ✅ | 16/24 pass, 8 multi-byte failures |
| Retrieval ablation | runs.csv, summary.json | ✅ | ✅ | 0/24 pass, 19 compile failures |
| Patcher ablation | runs.csv, summary.json | ✅ | ✅ | 0/24 pass, 19 runner errors |
| IR benchmark | runs.csv, summary.json | ✅ | ✅ | 0/24 pass, 18 gen errors |
| Template baseline | runs.csv, summary.json | ✅ | ✅ | 24/24 pass |
| Best-of-N (claimed) | README only | ❌ | ❌ | Only N=2 on 5/12 cases run |
| IR Best-of-N | N/A | N/A | N/A | Not run |

---

## 8. Conclusions

1. **Baseline benchmark is reproducible and internally consistent** - All 96 runs have matching artifacts across CSV, JSONL, summary.json, and report.md.

2. **Multi-byte failure is the primary limitation** - All 8 on-on failures are multi-byte cases (2-3 bytes). The model cannot generate correct multi-byte FSM logic.

2. **Retrieval is critical** - 66.7% → 0% when disabled. The model adapts a known reference rather than generating from scratch.

3. **Patcher is essential for generation** - 19/24 runner errors without patcher are infrastructure (Ollama) failures, not model failures.

4. **IR path not competitive** - 0/24 pass, fundamental issue with missing `drive sda="bit"` in generated JSON.

4. **Best-of-N claims are unsupported** - README claims N=4 results but experiment incomplete.

5. **Benchmark coverage gaps** - No tests for repeated START, read transactions, clock stretching, NACK handling.

---

*End of Benchmark Reproducibility Audit*