# ProtoForge — RTL generation harness (I²C v1)

ProtoForge evaluates a local LLM on a constrained RTL-generation task. The model creates an I²C master wrapper; a separate, fixed testbench observes the external SDA/SCL bus, and the pipeline checks the code structurally, compiles it, simulates it, judges protocol events, and repairs failures.

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
- The current environment check reports 22 passing tool-independent tests and 9 simulator-dependent tests skipped because Icarus is unavailable. The real LLM generation path (Ollama) has not been exercised in this environment.

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

Fast tests that do not require Ollama or Icarus Verilog:

```bash
python -m pytest -q tests
```

This covers structural checks, result parsing, verified-example memory behavior, and a mocked end-to-end repair cycle. The real model/compiler/simulator path still needs Ollama and Icarus installed:

```bash
python manual_agent_integration.py
python manual_structural_gate.py
python manual_reported_bug.py
```

## Current validation status

The deterministic multi-device wrapper path and the checked-in reference pass
Icarus, Verilator, and Yosys validation. Real Qwen 7B trials are recorded in
`results/real_trials.jsonl`; they currently show protocol failures after repair
despite passing compile, lint, and synthesis. Those candidates are not promoted
as verified examples. A physical board driver or FPGA target is not included in
this repository; hardware validation currently means synthesizability and
open-drain RTL checks, while the I2C electrical behavior is exercised by the
BFM testbench.
