# ProtoForge — RTL generation harness (I²C v1)

ProtoForge evaluates a local LLM on a constrained RTL-generation task. The model creates an I²C master wrapper; a separate, fixed testbench observes the external SDA/SCL bus, and the pipeline checks the code structurally, compiles it, simulates it, judges protocol events, and repairs failures.

## What is wired into the live loop

The live path is:

```
main.py / agent_core.agent
  -> domains/i2c/adapter.py
  -> core/orchestrator.py
  -> agent/rtl_generator.py + tools/* gates
```

Everything else is deliberately **not** part of a generation. These are kept
because `docs/WORKFLOW.md` phases 7-9 need them; do not assume they run:

- `ui/dashboard.py` — trace rendering for the run timeline (phase 9, step 96).
- `mcp_server/` — standalone MCP entry point (phase 9, step 97).
- `bench/`, `baseline/`, `tools/replay.py`, `tools/make_spec.py` — measurement
  and audit tooling invoked explicitly, never from inside a run.

Removed and not coming back without a workflow step that asks for them:
`agents/`, `archive/`, `core/task_graph.py`, `indexing/`, `mutations/`,
`tools/error_context.py` (commit `086bf99`). They had no importers in the live
loop; `archive/multi_agent_draft.py` did not even parse.

## Status

| Check | Command | Result |
| --- | --- | --- |
| Unit + integration tests | `.venv/bin/python -q -m pytest tests` | 118 passed |
| Static lint | `.venv/bin/python -m pyflakes $(git ls-files '*.py')` | clean |
| Oracle mutants | `tests/test_oracle_mutation.py` (real Icarus) | all mutants killed |
| Template upper bound | `results/bench_template_20x/` | 240/240 pass |
| Real model, one run | `.venv/bin/python -m agent_core.agent` | pass after 1 repair (2 attempts, 416 s) |
| Replay of that run | `.venv/bin/python tools/replay.py <run_dir>` | verdicts match |

See `docs/WORKFLOW.md` for the phase-by-phase status board and
`docs/BASELINE.md` for the measured benchmark table.


## Changes in this iteration

- **Small-model-first generation:** the default model is `qwen2.5-coder:7b`. The model is asked for only the FSM declarations and sequential block; the harness deterministically supplies the top-level ports, parsed device instances, and open-drain bus assignments. Full-module outputs remain accepted for compatibility. This reduces the surface area where a small model can corrupt the wrapper. Generation temperature is `0` by default and Ollama receives a fixed seed. Override with `PROTOFORGE_MODEL`, `PROTOFORGE_TEMPERATURE`, `PROTOFORGE_SEED`, `PROTOFORGE_NUM_CTX`, and `PROTOFORGE_NUM_PREDICT`.
- **Verified-example retrieval:** a small offline RAG/few-shot layer starts from a reset-safe few-shot copy derived from the existing known-good I²C oracle and learns additional examples only after they pass the compile + simulation oracle. Failed designs are archived separately and never retrieved as positive examples. No embedding model or vector database is required.
- **Real repair loop:** two repairs are attempted by default after the initial generation (`PROTOFORGE_MAX_REPAIRS=2`). Each repair prompt includes the failed code, the specific structural/compiler/protocol diagnosis, and verified reference context.
- **Stronger structural gate:** validates the required top-level port contract, open-drain SDA/SCL drivers, sequential completion logic, required device instances, and rejects procedural bus writes, `initial`, `wait`, delays, and malformed statement grouping before compiling.
- **Fixed simulator wrapper:** simulation results are now returned on successful runs as well as timeouts. Compile/runtime tool absence is surfaced explicitly rather than treated as a model defect.
- **Smaller batch artifacts:** VCD output is opt-in (`vvp generated/sim.out +dump_vcd`) so repeated evaluations do not create huge waveform files.
- **Reset-safe few-shot fixtures:** reset-safe copies of the known-good and missing-STOP references initialize state through `rst_n`; the original oracle reference files are preserved unchanged.

## Changes in iteration 3 (oracle hardening; verified with a real Icarus Verilog run)

- **Testbench bug fixed:** an unconditional `$time > 5000` check printed `EVENT:TIMEOUT` on *every* run, passing ones included. It now prints only if `tb_done` never rose.
- **Oracle checks more of the spec:** `RESULT:` now also reports `tb_done`, `starts`, `stops`. The judge fails designs that never assert `tb_done`, or that show a repeated START / extra STOP. Old-format RESULT lines still parse (the new checks are skipped for them).
- **Structural gate less brittle:** the open-drain check accepts `~oe ? 1'bz : 1'b0`, parenthesised/compound conditions, and either polarity, instead of only `assign sda = ident ? 1'b0 : 1'bz`. Driving a hard `1'b1` is still rejected.
- **Mutation tests (`tests/test_oracle_mutation.py`):** the golden reference must pass; mutants (no tb_done, no STOP, no START, wrong address, repeated START) must each fail on the matching check. Skipped automatically when `iverilog` is absent.
- **Honest batches (`main.py`):** each run gets its own seed (`PROTOFORGE_SEED + i`) and the sampling temperature is explicit (`PROTOFORGE_BATCH_TEMPERATURE`, default 0). At temperature 0 all runs are expected to be identical, so the report prints `Distinct first designs: k/N` and warns when a batch is effectively one sample. A failing run (e.g. Ollama down) is recorded as `generation_error` instead of aborting the batch; a first-run configuration error stops early. A machine-readable summary is written to `results/batch_summary.json`.
- **Retrieval ablation:** `PROTOFORGE_NO_RETRIEVAL=1` removes the reference example from the prompt. The seed example is derived from the known-good oracle, so compare pass rates with it on and off before claiming the model can generate an I²C master.
- **Real-simulator end-to-end tests (`tests/test_e2e_real_sim.py`):** only the LLM is stubbed; structural gate, iverilog, vvp, judge and repair routing are real (broken -> repaired passes, correct passes first try, persistent fault exhausts the repair budget).
- At the time this iteration was written the suite reported 22 tool-independent
  passes and 9 skips because Icarus was unavailable; that environment gap is
  closed (see `docs/ENV.md`), and the current counts are in the Status table
  above.

## Changes in iteration 4 (auditable, replayable runs)

- **Per-run directory:** every `run_once` writes to `results/runs/<run_id>/`.
  It contains `run.json` (run id, git commit, model, model digest, seed,
  temperature, config), `attempt_<n>.v`, `attempt_<n>_prompt.txt`,
  `attempt_<n>.json`, and the compiled `.vvp`.
- **Attempt records:** each attempt JSON stores the prompt, raw model output,
  cleaned code, final code, code hash, model token/timing metadata, retrieval
  details (which example, seed vs learned, score), every gate verdict, and
  compiler stderr / simulator stdout. Full code is kept, not only hashes.
- **One-line index:** `results/runs.jsonl` is now a summary pointer to the run
  directory; the bulky payload stays in the run directory.
- **Replay:** `python tools/replay.py results/runs/<run_id>` re-runs the
  structural, compile, hardware, simulation and judge gates on the stored
  attempt and compares the verdicts. `results/replay_v1/report.txt` records
  three replayed runs (pass, missing STOP, structural failure), all matching.
- **Event traces:** each run emits `run_started`, `model_invoked`,
  `stage_finished` and `run_finished` through the existing `EventStore`
  (`results/traces/<run_id>.json`).

## Changes in iteration 5 (oracle-v1)

- **SDA stability check:** the testbench counts SDA transitions while SCL is
  high outside a legal START/STOP and reports `sda_glitches`; the judge fails
  designs with a non-zero count (`sda_stable`).
- **Byte-count check:** the testbench reports completed `bytes`; the judge
  requires exactly one address byte plus the expected data bytes
  (`byte_count`), rejecting extra or missing bytes.
- **More mutants:** LSB-first, SDA-while-SCL-high, third byte, and skipped ACK
  clock mutants are rejected, with the skipped-ACK case diagnosed downstream
  (documented in `docs/ORACLE.md`).
- **Plusarg coverage:** the testbench is exercised with three address/data
  pairs via `+I2C_ADDR`/`+I2C_DATA`, and a mismatched pair is rejected.
- **Warnings visible:** Verilator warning count is recorded in the hardware
  stage without failing yet; `docs/ORACLE.md` lists the warning classes that
  should fail once the policy is validated.

## Changes in iteration 6 (template baseline and the two-byte task)

- **Deterministic template:** `baseline/template_master.py` emits a complete
  `top_glue` from the parsed address, data bytes and device list. It is the
  upper-bound baseline: it never calls a model, and it passes structural,
  compile, Verilator/Yosys and simulation gates.
- **Coverage:** the template is tested with five address/data pairs, with one
  device and with three devices, and with the one-byte and two-byte specs.
- **Two-byte task:** `protocols/i2c/spec_2byte.md` asks for data bytes
  `0x11`, `0x22`. The testbench accepts `+I2C_NBYTES` (default 1), ACKs each
  data byte, and reports every `EVENT:DATA` byte; the judge compares the full
  payload and requires `address + N` bytes. Old one-byte RESULT lines still
  parse.
- **Golden references:** `good_master_2byte_reset_safe.v` is a hand-written
  two-byte reference that passes. The one-byte golden is correctly rejected
  against the two-byte spec (`correct_data`, `sda_stable`, `byte_count`).

## Changes in iteration 7 (benchmark runner)

- **Case set:** `benchmarks/cases.json` has 12 cases varying the 7-bit address,
  the 1/2/3-byte payload and the 1/2/3-device list. `tools/make_spec.py` turns
  a case into a parseable spec.
- **Runner:** `python -m bench.run` loops cases and samples, writes one CSV row
  per run (flushed immediately), and aggregates pass@1, pass-after-repair,
  distinct first designs, latency, token counts and Wilson 95% CIs.
- **Ablations:** retrieval on/off and the regex patcher on/off are real
  switches (`PROTOFORGE_NO_PATCHER` / `Config.patcher`). With the patcher off,
  the deterministic wrapper is still supplied but the model response is embedded
  verbatim. The 2x2 shares the on/on cell.
- **Template upper bound:** 240/240 template runs pass across all 12 cases.
- **Measured table:** `docs/BASELINE.md` records the configuration, commit,
  model digest and the real-model matrix.

## Run it

1. Install Icarus Verilog (`iverilog` and `vvp`) and Ollama.
2. Pull a code-oriented local model:

   ```bash
   ollama pull qwen2.5-coder:7b
   ```

3. Install the Python requirements:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Generate and evaluate one design:

   ```bash
   python -m agent_core.agent
   ```

5. Run a reproducibility batch:

   ```bash
   python main.py 20 qwen2.5-coder:7b
   ```

The batch reports both first-try pass rate and final pass rate after the configured repair budget, so repairs do not hide first-generation quality. Each run logs its attempt stages, failed checks, source hashes, seed, elapsed time, and final status to `results/runs.jsonl`. Passing RTL snapshots are added to `results/rtl_memory/passing/` and can be retrieved in later runs.

The device set is contract-driven and configurable. Each listed Verilog file
must contain exactly one plain-Verilog module with `clk` and active-low `rst_n`:

```bash
PROTOFORGE_DEVICE_PATHS=devices/device_sensor.v,devices/device_controller.v \
  python -m agent_core.agent
```

The harness parses every device interface, generates unique instance and signal
names, checks the generated RTL with Icarus, Verilator, and Yosys, and only then
runs the I²C protocol oracle. Device-local driver signals are not treated as
I²C bus activity.

## Small-model evaluation (recommended first)

The default is the 7B code-specialized Qwen model requested for this evaluation
on consumer hardware. Do not evaluate only with temperature 0 and then call
20 identical generations 20 independent samples. First establish a deterministic
baseline, then sample with a non-zero temperature:

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

## Tests

```bash
.venv/bin/python -q -m pytest tests
.venv/bin/python -m pyflakes $(git ls-files '*.py')
```

118 tests, no `xfail`, no `skip` in this environment (Icarus, Verilator, Yosys
and Ollama are all installed). Four suites carry `skipif` guards and skip only
where a tool is genuinely absent: `test_oracle_mutation`, `test_e2e_real_sim`,
`test_logging_replay`, `test_template_master`.

Coverage includes structural checks, result parsing, verified-example memory
behaviour, per-run isolation, attempt/run records, event traces, replay
comparison, benchmark aggregation and artifact consistency, domain discovery,
and mocked plus real-simulator end-to-end repair cycles.

Tests never write into the tracked `results/rtl_memory/` store; `conftest.py`
redirects it to a temporary directory.

To exercise the real model/compiler/simulator path end to end:

```bash
.venv/bin/python -m agent_core.agent                       # one real run
.venv/bin/python tools/replay.py results/runs/<run_id>     # re-verify it
.venv/bin/python -m bench.run --case-limit 2 --samples 1   # small batch
```

## Current validation status

The deterministic multi-device wrapper path and the checked-in reference pass
Icarus, Verilator, and Yosys validation. A real `qwen2.5-coder:7b` run through
the full loop (structural -> compile -> Verilator/Yosys -> simulation -> judge,
with one bounded repair) passes; its artifacts are reproducible with
`tools/replay.py`, which re-runs gates 1-5 on the stored attempt and compares
verdicts.

Real Qwen 7B batch trials are recorded in `results/real_trials.jsonl` and
`results/bench/`; per-configuration pass rates, distinct first designs and
Wilson intervals live in `docs/BASELINE.md`. Candidates that only pass compile
and lint but fail the protocol oracle are never promoted as verified examples.

A physical board driver or FPGA target is not included in this repository;
hardware validation currently means synthesizability and open-drain RTL checks,
while the I2C electrical behavior is exercised by the BFM testbench.
