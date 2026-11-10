# Archived Invalid Best-of-N Results

## Why These Runs Are Invalid

The Best-of-N experiment directories in this archive contain **invalid/incomplete experimental data** that cannot be used for performance conclusions.

### Affected Directories

| Directory | Original Purpose | Why Invalid |
|-----------|------------------|-------------|
| `bon/` | Best-of-N experiment output | Contains only `bon_results.json` (48 entries claiming N=4), but **no actual run directories** exist. The experiment was never executed. |
| `bon_baseline/` | Baseline N=1 for comparison | Only 7 runs (c01-c04 partial), incomplete experiment |
| `bon_n4/` | Best-of-4 (N=4) experiment | Only 10 runs (5 cases × 2 samples), not 48 (12 cases × 4 samples). Experiment was interrupted/aborted. |
| `bon_on-on/` | Best-of-N on on-on config | Only 3 runs (c01-c03, 1 sample each), incomplete |
| `bon_test/` | Test run | Only 2 runs, incomplete test |

### Why These Are Invalid

1. **No actual Best-of-4 experiment was completed** — The README claimed "A Best-of-4 trial was run... 5/12 cases pass at 41.7%" but only 10 partial runs exist (5 cases × 2 samples in `bon_n4/`, 3 cases × 1 sample in `bon_on-on/`). The claimed 48 runs (12 cases × 4 samples) were **never executed**.

2. **Incomplete experiments** — All Best-of-N result directories contain partial/incomplete runs. The experiments were interrupted or never started for most cases.

3. **Infrastructure failures not model failures** — The `patcher-off` configuration had 19/24 runs fail with `runner_error` (Ollama service down), which are infrastructure failures, not model failures. These must not be counted as model failures.

3. **Inconsistent sampling** — Different directories use different sample counts (1, 2, 4), making comparisons invalid.

4. **Incomplete cases** — Only 5/12 cases (c01-c05) have any Best-of-N data; multi-byte cases (c06-c12) were never tested with Best-of-N.

### What This Means

- **No valid Best-of-4 data exists** — The claimed "41.7% pass rate at Best-of-4" is unsupported by any experimental evidence.
- **Previous claims are retracted** — The README's claim of "5/12 cases pass at 41.7% with Best-of-4" is retracted.
- **Baseline remains N=1** — The valid baseline is the single-sample (N=1) on-on configuration at 16/24 (66.7%) pass@1.

### What Was Actually Run

| Experiment | Directory | Runs Completed | Status |
|------------|-----------|----------------|--------|
| Baseline (N=1) | `results/bench/` | 96 runs (12 cases × 2 samples × 4 configs) | ✅ Complete |
| Best-of-2 (1-byte) | `results/bon_on-on/` | 10 runs (5 cases × 2 samples) | ✅ Complete |
| Best-of-2 (multi-byte, partial) | `results/bon_n4/` | 10 runs (5 cases × 2 samples) | ⚠️ Partial (c01-c05 only) |
| Best-of-4 | `results/bon_n4/` | 10/48 runs | ❌ Incomplete (aborted) |

### Correct Baseline

The only valid, complete baseline is the **single-sample (N=1) on-on configuration** in `results/bench/`:
- 24 runs (12 cases × 2 samples)
- 16/24 pass@1 (66.7%)
- Wilson 95% CI: [46.7%, 82.0%]
- All artifacts consistent (CSV, JSONL, summary.json, report.md)

---

*Archived on: 2026-11-11*  
*Commit: dca7c78*  
*Auditor: Autonomous Engineering Agent*