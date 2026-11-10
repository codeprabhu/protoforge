# ProtoForge — Local LLM I²C RTL Generation Harness

ProtoForge is a measurement harness that asks a local LLM (default `qwen2.5-coder:7b`)
to generate only the behaviour-bearing fragment of an I²C multi-device `top_glue`
wrapper. The harness supplies the fixed ports, device instances, and open-drain
bus drivers; the model provides only the FSM declarations and sequential logic.
Every candidate is run through a fixed oracle: structural gate → Icarus compile
→ Verilator/Yosys lint → Icarus simulation with a bus-functional testbench →
protocol judge → up to two bounded repairs.

## Status

| Check | Command | Result |
| --- | --- | --- |
| Unit + integration tests | `.venv/bin/python -q -m pytest tests` | **189 passed** |
| Static lint | `.venv/bin/python -m pyflakes $(git ls-files '*.py')` | clean |
| Oracle mutants | `tests/test_oracle_mutation.py` (real Icarus) | all 10 mutants killed |
| Template upper bound | `results/bench_template_20x/` | 240/240 pass |
| Real-model baseline (on-on) | `results/bench/` | 16/24 (66.7%) pass@1 |
| Retrieval ablation | `results/bench/` | 0/24 pass@1 |
| Patcher ablation | `results/bench/` | 0/24 pass@1 (19 runner_error) |
| IR benchmark | `results/bench_ir/` | **0/24** pass@1 (does not beat RTL baseline) |
| Replay of a run | `.venv/bin/python tools/replay.py <run_dir>` | verdicts match |

See `docs/WORKFLOW.md` for the phase-by-phase status board and
`docs/BASELINE.md` for the measured benchmark table with Wilson 95% CIs.

## Quick Start

1. Install Icarus Verilog (`iverilog` and `vvp`) and Ollama.
2. Pull a code-oriented local model:

   ```bash
   ollama pull qwen2.5-coder:7b
   ```

3. Install Python requirements:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Generate and evaluate one design:

   ```bash
   python -m agent_core.agent
   ```

5. Run a reproducibility batch (20 runs, temp 0.25, 2 repairs):

   ```bash
   python main.py 20 qwen2.5-coder:7b
   ```

   The batch reports both first-try pass rate and final pass rate after the
   configured repair budget. Each run logs its attempt stages, failed checks,
   source hashes, seed, elapsed time, and final status to `results/runs.jsonl`.

## Deterministic Baseline vs. Sampled Runs

The default model is `qwen2.5-coder:7b`. Temperature defaults to `0` (deterministic).
At temperature `0` all runs with the same seed are identical, so the report
prints `Distinct first designs: k/N` and warns when a batch is effectively one
sample.

```bash
# Deterministic baseline (usually one unique first design repeated)
PROTOFORGE_BATCH_TEMPERATURE=0 python main.py 5 qwen2.5-coder:7b

# Diversity / repair stress test
PROTOFORGE_BATCH_TEMPERATURE=0.25 PROTOFORGE_MAX_REPAIRS=2 \
  python main.py 20 qwen2.5-coder:7b

# Retrieval ablation: same setup, but no known-good reference in the prompt
PROTOFORGE_NO_RETRIEVAL=1 PROTOFORGE_BATCH_TEMPERATURE=0.25 \
  python main.py 20 qwen2.5-coder:7b
```

Compare first-try pass rate, post-repair pass rate, distinct candidate hashes,
and recovery count. The retrieval-enabled condition includes a known-good
reference, so it measures reference-conditioned synthesis rather than
from-scratch RTL competence. Keep that distinction explicit in reports.

## Best-of-N Experiment (Phase 7, Step 93-95)

**Status: Not yet validly measured.**

An earlier version claimed a Best-of-4 trial was run (5/12 cases at 41.7%). **This claim was incorrect.** The file `results/bon/bon_results.json` (archived at `results/archive/invalid_bon_runs/bon/bon_results.json`) contains 48 recorded runs (12 cases × 4 samples), but **all 48 failed with `generation_error: "The Ollama Python package is missing"`** — these are infrastructure failures, not model failures. No model was actually invoked.

| Config | Cases | Samples | First-pass cases | Pass rate |
| --- | --- | --- | --- | --- |
| on-on (Best-of-4) | 12 | 4 | **Not measured** | **Not measured** |
| on-on (baseline, N=1) | 12 | 2 | 8/12 | **66.7%** |

**Result:** Best-of-4 has **not been validly measured**. The 48 runs in `bon_results.json` are infrastructure failures (Ollama Python missing). The baseline remains the on-on single-sample path at 66.7%.

A proper Best-of-4 experiment (12 cases × 4 samples = 48 runs) is planned but has not yet been executed due to the multi-byte generation bottleneck (see Known Limitations).

## IR Benchmark (Phase 7, Steps 91-92)

The FSM JSON intermediate representation was benchmarked (72 runs: on-on-ir,
retrieval-off-ir, template × 12 cases × 2 samples). The JSON path achieved
**0/24 pass@1** vs. the RTL baseline **16/24 (66.7%)**.

Root cause: the model consistently omitted `drive sda="bit"` (data-bit drive),
which the compiler cannot invent. Enrichment fills mechanical boilerplate
(`otherwise`, `load_address`, `restart_bits`, terminal `done`), but the six
candidates that compiled still failed the judge on address/data/ACK/STOP
timing, and repair then produced invalid tables. The free-form RTL path at
66.7% remains the baseline to beat. The IR path is **not kept**.

## Key Architectural Decisions

- **Small-model-first generation:** The model writes only the FSM fragment.
  The harness deterministically supplies ports, device instances, and open-drain
  assigns. This reduces the surface area where a small model can corrupt the
  wrapper. Generation temperature defaults to `0`; Ollama receives a fixed seed.
- **Verified-example retrieval:** A small offline RAG layer starts from a
  reset-safe few-shot copy derived from the known-good I²C oracle and learns
  additional examples only after they pass all gates. Failed designs are
  archived separately and never retrieved as positive examples.
- **Bounded repair loop:** Two repairs are attempted by default. Each repair
  prompt includes the failed code, the specific gate diagnosis, and verified
  reference context.
- **Stronger structural gate:** Validates required ports, open-drain drivers,
  sequential completion logic, required instances, and rejects procedural bus
  writes, `initial`, `wait`, delays, and malformed statement grouping.
- **Per-run reproducibility:** Every run writes to `results/runs/<run_id>/`
  with `run.json`, `attempt_<n>.v`, `attempt_<n>_prompt.txt`,
  `attempt_<n>.json`, and the compiled `.vvp`. The one-line index
  `results/runs.jsonl` is a summary pointer to the run directory.
- **Replay:** `python tools/replay.py results/runs/<run_id>` re-runs the
  structural, compile, hardware, simulation and judge gates on the stored
  attempt and compares verdicts.
- **Event traces:** Each run emits `run_started`, `model_invoked`,
  `stage_finished`, and `run_finished` through the `EventStore`.

## MCP Server

A FastMCP server exposes tools for compilation, linting, simulation, and the
full pipeline:

```bash
python -m mcp_server.server
```

Tools:
- `compile_rtl(filepaths)` — compile Verilog with Icarus
- `lint_rtl(filepath)` — lint with Verilator
- `simulate_and_judge(executable)` — run vvp and apply the protocol judge
- `run_pipeline(model)` — run the full generate → compile → repair → judge loop
- `get_run(run_id)` — retrieve timeline, gate verdicts, and attempt records

## Tests

```bash
.venv/bin/python -q -m pytest tests
.venv/bin/python -m pyflakes $(git ls-files '*.py')
```

**189 tests**, no `xfail`, no `skip` (Icarus, Verilator, Yosys, and Ollama
are all installed). Four suites carry `skipif` guards and skip only where a
tool is genuinely absent: `test_oracle_mutation`, `test_e2e_real_sim`,
`test_logging_replay`, `test_template_master`.

## Known Limitations

- **Retrieval dependence:** Retrieval-on is 66.7% pass@1; retrieval-off is
  0/24. The model is not generating I²C masters from scratch; it is adapting
  a known-good reference.
- **Multi-byte weakness:** The prompt scaffold is one-byte-oriented; the
  generator does not yet specialize all data literals for 2/3-byte payloads.
- **IR path does not beat RTL:** The JSON IR path (0/24) fails on the one
  thing the compiler cannot invent: `drive sda="bit"`.
- **Hardware validation is simulation-only:** A physical board driver or FPGA
  target is not included. Hardware validation currently means synthesizability
  and open-drain RTL checks, while I²C electrical behavior is exercised by
  the BFM testbench.
- **Single-model GPU:** The 4 GiB RTX 3050 runs one 7B model at a time with
  CPU offload (~90–200 s per call). Concurrency is limited to 1.

## Reproducibility

Every number in `docs/BASELINE.md` comes from a run recorded in this repo,
with its command, commit, and model digest (`qwen2.5-coder:7b` digest
`dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364`).

The test suite is the gate: if it is not green, the change is not ready.