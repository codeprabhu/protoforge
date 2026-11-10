# Archived Invalid Best-of-N Results (Model Never Invoked)

## Why These Runs Are Invalid

The Best-of-N experiment directory in this archive contains **invalid experimental data** that cannot be used for performance conclusions because **the model was never actually invoked**.

### Affected Directory

| Directory | Original Purpose | Why Invalid |
|-----------|------------------|-------------|
| `bon/` | Best-of-N experiment output (claimed N=4) | Contains `bon_results.json` with 48 entries (12 cases × 4 samples), but **all 48 failed with `generation_error: "The Ollama Python package is missing"`**. The model was never invoked. No run directories with actual candidate outputs exist. |

### Why This Is Invalid

1. **Model never invoked** — All 48 runs failed at the generation step with `generation_error: "The Ollama Python package is missing"`. This is an infrastructure failure (missing Ollama Python package), not a model failure.

2. **No candidate artifacts** — There are no Verilog files, no simulation outputs, no judge results. The model simply never ran.

3. **Claimed results are fabricated** — Earlier documentation claimed "A Best-of-4 trial was run... 5/12 cases pass at 41.7%". This claim has **no experimental basis** — the 48 runs in `bon_results.json` are all infrastructure failures.

### What This Means

- **No valid Best-of-4 data exists** — The claimed "41.7% pass rate at Best-of-4" is unsupported by any experimental evidence.
- **Previous claims are retracted** — The README's claim of "5/12 cases pass at 41.7% with Best-of-4" is retracted.
- **Baseline remains N=1** — The valid baseline is the single-sample (N=1) on-on configuration in `results/bench/`:
  - 24 runs (12 cases × 2 samples)
  - 16/24 pass@1 (66.7%)
  - Wilson 95% CI: [46.7%, 82.0%]
  - All artifacts consistent (CSV, JSONL, summary.json, report.md)

### Correct Baseline

The only valid, complete baseline is the **single-sample (N=1) on-on configuration** in `results/bench/` (not in this archive).

---

*Archived on: 2026-11-11*  
*Commit: 3ff1e87*  
*Auditor: Autonomous Engineering Agent*