# Archived Incomplete Best-of-N Results (Model Invoked But Incomplete)

## Status

These directories contain **real model invocations** (the LLM was actually called), but the experiments are **incomplete and not comparable** to a full baseline.

### Affected Directories

| Directory | Config | Cases Run | Samples/Case | Total Runs | Status |
|-----------|--------|-----------|--------------|------------|--------|
| `bon_n4/` | on-on (N=4) | 5 (c01-c05) | 2 | 10 | Incomplete — only 1-byte cases |
| `bon_on-on/` | on-on (N=1) | 3 (c01-c03) | 1 | 3 | Incomplete — only 3 cases |
| `bon_baseline/` | on-on (N=1) | 4 (c01-c04) | 2 | 7 | Incomplete — only 4 cases |
| `bon_test/` | template (deterministic) | 1 (c02) | 2 | 2 | Not LLM — template generator |

### Why These Are Incomplete / Not Comparable

1. **Incomplete case coverage** — `bon_n4` only covers 1-byte cases (c01-c05); multi-byte cases (c06-c12) were never tested. `bon_on-on` only has 3 cases. `bon_baseline` only has 4 cases.

2. **Inconsistent sampling** — Different directories use different sample counts (1 or 2), making cross-directory comparisons invalid.

3. **Not full experiments** — None of these represent a complete Best-of-N experiment across all 12 benchmark cases.

4. **`bon_test` uses template, not LLM** — The `bon_test` directory uses the deterministic template generator (`model: "template"`), not the LLM. These runs are not representative of model performance.

### Valid Findings from These Runs

Despite being incomplete, these runs confirm:

- **1-byte cases (c01-c05) pass reliably** — In `bon_n4` (N=2 on 1-byte cases), all 5 cases passed both samples (10/10 pass). This matches the full baseline in `results/bench/` where 1-byte cases pass at 100%.

- **Multi-byte cases were not tested with Best-of-N** — The fundamental bottleneck (model cannot generate correct multi-byte FSM logic) was not evaluated with Best-of-N sampling.

### Comparison to Valid Baseline

| Experiment | Directory | Cases | Samples | Pass@1 |
|------------|-----------|-------|---------|--------|
| Valid baseline (complete) | `results/bench/` | 12 | 2 | 66.7% (16/24) |
| Best-of-2 1-byte (partial) | `bon_n4/` | 5 | 2 | 100% (10/10) |
| Best-of-1 partial | `bon_on-on/` | 3 | 1 | 100% (3/3) |
| Baseline partial | `bon_baseline/` | 4 | 2 | 100% (7/7)* |

*7 runs recorded, but one case may have only 1 sample.

### Conclusion

These runs are **archived for provenance** but **must not be used for performance claims**. The only valid baseline is the complete experiment in `results/bench/` (12 cases × 2 samples = 24 runs, 66.7% pass@1).

---

*Archived on: 2026-11-11*  
*Commit: 3ff1e87*  
*Auditor: Autonomous Engineering Agent*