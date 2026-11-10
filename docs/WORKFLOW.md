# ProtoForge v1: 100-step plan

Baby steps from the current repo to a measured, reproducible v1.

This is the single canonical workflow document. An earlier copy lived at the
repo root as `worklfow.md` (misspelt, byte-identical); it was removed in
commit `086bf99`.

## Status board

Last verified: 2026-11-10, `188 passed` from
`.venv/bin/python -m pytest -q tests` (steps 81–92 **complete**).

| Phase | Steps | State | Evidence |
| --- | --- | --- | --- |
| 0 Snapshot and environment | 1–10 | **done** | tag `pre-cleanup` (`9acd76d`), tag `baseline-v0` (`2a28978`), `docs/ENV.md` |
| 1 Clean the orchestrator | 11–20 | **done** | `01cddcf`; `agents/`, `archive/`, `core/task_graph.py` removed for good in `086bf99` |
| 2 Remove file handoff / import-time config | 21–34 | **done** | `cb15497`, `tests/test_run_isolation.py` |
| 3 Replayable logging | 35–46 | **done** | tag `logging-v1` (`6038b46`), `tests/test_logging_replay.py`, `tools/replay.py` |
| 4 Harden the oracle | 47–60 | **done** | tag `oracle-v1` (`c0f2f54`), `tests/test_oracle_mutation.py`, `docs/ORACLE.md` |
| 5 Template baseline + 2-byte task | 61–70 | **done** | tag `template-v1` (`a0c783d`), `tests/test_template_master.py` |
| 6 Benchmark runner | 71–80 | **done** | `5fb83dd` + harness fixes in `ccca20e`, `tests/test_bench.py`, `docs/BASELINE.md` |
| 7 FSM JSON intermediate representation | 81–92 | **done** | `ir/`, `agent/ir_generator.py` (`4d06b5b`, `0b86710`, `6e4f074`), `tests/test_fsm_ir.py`, `tests/test_ir_generator.py`, `bench.run --ir`, `results/bench_ir/`, `docs/BASELINE.md` (IR comparison table) |
| 8 Best-of-N | 93–95 | not started | — |
| 9 Traces and v1 packaging | 96–100 | partially unblocked | `ui/dashboard.py` now renders real `stage_finished` events (`086bf99`) |

## Definition of done for a phase

A phase counts as done only when **all** of the following hold. Anything less
is a checkpoint you may return from, not a completion.

1. `.venv/bin/python -q -m pytest tests` is green with no `xfail` that exists
   only to hide a red test.
2. `python -m pyflakes $(git ls-files '*.py')` is clean.
3. No tracked file under `results/` disagrees with another (a report's row
   count equals its CSV's and its index's).
4. Any number quoted in `docs/` came from a run recorded in this repository,
   with its command, commit and model digest.
5. The phase is committed with a message naming the phase and steps.

## Rules that override convenience

- **Never** edit the golden references or the testbench to make a result pass
  (`AGENTS.md`, oracle integrity). If the oracle is wrong, add a separate test
  that proves it.
- A test that cannot fail is not evidence. Before adding one, ask whether
  deleting the code under test would make it red.
- Report compiler errors, protocol failures, timeouts and missing tools as
  four different things. A missing tool is never a model failure.
- A run that produced no candidate (`attempts == 0`) is a harness event and
  must be labelled as such, never folded into a pass rate.

## How to use this plan

• Work top to bottom. Each phase ends with a checkpoint; do not start the next phase until it is met.
• Commit after every phase and tag the checkpoints.
• Never edit the golden references or the testbench just to make a result pass (the AGENTS.md rule). If the oracle is wrong, add a separate test that proves it.
• Change one thing at a time. If a step surprises you, stop and write down what you saw before continuing.
• Pass-rate targets come from your own benchmark in Phase 6, not from guesses.
Phase 0: Snapshot and environment (steps 1 to 10)
• Step 1: Create the branch v1-stabilize and tag the current state pre-cleanup.
• Step 2: Install Icarus Verilog, Verilator and Yosys, and note each version in docs/ENV.md.
• Step 3: Install Ollama, run ollama pull qwen2.5-coder:7b, and record the model digest in docs/ENV.md.
• Step 4: Create a virtualenv, install requirements.txt, and add pydantic to it explicitly (the code imports it).
• Step 5: Run python -m pytest -q tests and save the output to results/baseline_tests.txt.
• Step 6: Confirm the tool-missing failures are gone. Expect one failing test left: test_orchestrator_multi_agent_workflow.
• Step 7: Run python -m agent_core.agent once for real and save the JSON it prints.
• Step 8: Run PROTOFORGE_BATCH_TEMPERATURE=0.25 python main.py 5 and copy results/batch_summary.json to results/baseline_v0.json.
• Step 9: Write the commit hash, seeds, temperature and tool versions into results/baseline_v0.md.
• Step 10: Commit and tag baseline-v0.
Checkpoint: only notes changed. You can always return to this point.
Phase 1: Clean the orchestrator (steps 11 to 20)
• Step 11: Open core/orchestrator.py and find the return log at the end of execute_task.
• Step 12: Copy everything between that line and run_orchestrated_task into archive/multi_agent_draft.py so the ideas are not lost.
• Step 13: Delete that unreachable block from orchestrator.py.
• Step 14: Remove the now-unused imports (agents, task_graph, trace_schema, event_store).
• Step 15: Run the tests. Only the multi-agent test should differ.
• Step 16: Mark test_orchestrator_multi_agent_workflow as xfail with the reason that the multi-agent flow is archived until Phase 9.
• Step 17: Choose one name for the generation-failure outcome (generation_error) and use it in both core/orchestrator.py and main.py.
• Step 18: Add a test that a batch whose generate_fn raises is counted under a known outcome, not under other.
• Step 19: Add a README note to agents/, ui/ and core/task_graph.py saying they are not wired into the live loop.
• Step 20: Run the full tests and commit with the message cleanup of orchestrator.
Checkpoint: the code now does what its docs say.
Phase 2: Remove file handoff and import-time config (steps 21 to 34)
• Step 21: Write a failing test: two run_once calls with different temp dirs must not overwrite each other's candidate file.
• Step 22: Add a run_id and a per-run directory results/runs/<run_id>/.
• Step 23: Make generate_multi_device_glue_rtl return the code string (writing the file becomes optional).
• Step 24: Make repair_rtl return the code string the same way.
• Step 25: In I2CDomainAdapter.generate_candidate, use the returned string instead of re-reading the file.
• Step 26: Do the same in repair_candidate.
• Step 27: Write each candidate to run_dir/attempt_<n>.v right before compiling, and point iverilog at it.
• Step 28: Put the compiled .vvp file in run_dir too.
• Step 29: Move generated/last_prompt.txt to run_dir/attempt_<n>_prompt.txt.
• Step 30: Create a Config dataclass: device paths, max repairs, model, seed, temperature, retrieval flag.
• Step 31: Add Config.from_env() and build it inside run_once instead of at import time in agent_core/agent.py.
• Step 32: Pass the config explicitly into the adapter and orchestrator, and update the tests.
• Step 33: Confirm the test from step 21 now passes.
• Step 34: Run everything and commit.
Checkpoint: two runs can happen side by side.
Phase 3: Logging that can replay (steps 35 to 46)
• Step 35: Define an AttemptRecord model: prompt, raw model output, cleaned code, final code, each stage's verdict, timings.
• Step 36: Add a function in agent/ollama_client.py that returns the text plus metadata (token counts and durations from the Ollama response).
• Step 37: Save one AttemptRecord JSON per attempt in run_dir.
• Step 38: Save the full code of every attempt, not only its hash.
• Step 39: Save simulator stdout and compiler stderr per attempt.
• Step 40: Write a run header: run_id, git commit, model, model digest, seed, temperature, config.
• Step 41: Log which example retrieval chose, its origin (seed or learned) and its score.
• Step 42: Keep results/runs.jsonl as a one-line summary that points to run_dir.
• Step 43: Write tools/replay.py: re-run gates 1 to 5 on a stored attempt and compare with the stored verdict.
• Step 44: Replay three old runs. Verdicts must match, which also shows the oracle is deterministic.
• Step 45: Emit minimal RunEvents (run started, model invoked, stage finished, run finished) through the existing EventStore.
• Step 46: Add a test that a run produces those events, then commit and tag logging-v1.
Checkpoint: any run can be reconstructed from disk.
Phase 4: Harden the oracle (steps 47 to 60)
• Step 47: Run tests/test_oracle_mutation.py with the real tools and confirm every mutant fails on its matching check.
• Step 48: Write docs/ORACLE.md listing exactly what the testbench and judge check, and what they do not.
• Step 49: Add a mutant that sends bytes LSB first.
• Step 50: Add a mutant that changes SDA while SCL is high during a data bit.
• Step 51: Add a mutant that sends a third byte.
• Step 52: Add a mutant that never samples the ACK clock.
• Step 53: Run the new mutants. Any the oracle misses go on your to-do list.
• Step 54: Extend the testbench to flag SDA changing while SCL is high outside START and STOP, and print it in the RESULT: line.
• Step 55: Update parse_result_line and judge for the new field, keeping old-format lines parseable.
• Step 56: Re-run the mutants. The SDA-glitch mutant should now fail.
• Step 57: Run the golden reference with three other address and data pairs using the existing +I2C_ADDR and +I2C_DATA plusargs.
• Step 58: Make Verilator warnings visible: record the warning count in the stage result without failing on it yet.
• Step 59: Review the warnings on the golden design and decide which kinds (latches, width mismatch) should fail later.
• Step 60: Commit and tag oracle-v1.
Checkpoint: you know what a pass actually means.
Phase 5: Template baseline (steps 61 to 70)
• Step 61: Read protocols/i2c/reference/good_master_reset_safe.v and mark every line that depends on the address, data byte or timing.
• Step 62: Write baseline/template_master.py, which emits top_glue from those parameters and the device list.
• Step 63: Test it: the default spec output must pass all five gates.
• Step 64: Run it on five address and data pairs.
• Step 65: Run it with 1 device and with 3 devices.
• Step 66: If anything fails, decide whether the template or the oracle is wrong, and fix it with a test.
• Step 67: Add a harder spec: a 2-byte write in protocols/i2c/spec_2byte.md.
• Step 68: Extend the spec parser and the testbench to expect N data bytes (default 1, so old behaviour is unchanged).
• Step 69: Hand-write a 2-byte golden reference and confirm it passes. Confirm a 1-byte design fails against the 2-byte spec.
• Step 70: Extend the template to N bytes and commit.
Checkpoint: you have an upper-bound baseline and a harder task.
Phase 6: Benchmark runner (steps 71 to 80)
• Step 71: Create benchmarks/cases.json with fields: id, address, data bytes, device list, notes.
• Step 72: Fill it with at least 10 cases that vary address, data, device count and byte count.
• Step 73: Write tools/make_spec.py, which creates a spec file for a case.
• Step 74: Write bench/run.py: loop over cases and samples, writing one CSV row per run.
• Step 75: Add switches for retrieval on or off, temperature, max repairs and model.
• Step 76: Compute pass@1, pass-after-repair, distinct first designs, median latency and tokens per configuration.
• Step 77: Add Wilson 95 percent confidence intervals to the report.
• Step 78: Add an ablation switch that turns the regex patcher off.
• Step 79: Run 20 or more trials per configuration: retrieval on and off, patcher on and off, plus the template baseline.
• Step 80: Write docs/BASELINE.md with the table, the commit and the model digest, then commit.
Checkpoint: this table is the number you must beat.
Phase 7: FSM JSON intermediate representation (steps 81 to 92)
• Step 81: Define the FSM schema in pydantic: states, per-state SDA and SCL drive, hold cycles, transitions, actions.
• Step 82: Write the validator: unreachable states, missing default, undefined state names.
• Step 83: Write the compiler from FSM JSON to the always block, using the fixed wrapper.
• Step 84: Hand-write the JSON for the golden transaction and confirm it passes all five gates.
• Step 85: Hand-write the 2-byte version and confirm it passes.
• Step 86: Write the prompt that asks for the JSON only. Keep it short and include the schema.
• Step 87: Use Ollama's format option with the schema if your version supports it. Otherwise parse and validate the output yourself.
• Step 88: Handle bad JSON: one retry with the validator's error message, then count it as a failed attempt.
• Step 89: Make repair edit the JSON using the judge's sentences, then recompile.
• Step 90: Add an IR switch to the runner and run the same benchmark with 20 or more trials per configuration.
• Step 91: Compare with docs/BASELINE.md. Keep the IR path only if the first-try pass rate improves.
• Step 92: If it wins, delete the regex patcher and its tests. If not, write up why and stop here before adding anything else.
Checkpoint: the model writes less and the pass rate is higher, or you know why not.
Phase 8: Best-of-N (steps 93 to 95)
• Step 93: Sample N candidates per case (start with N = 4) into separate run directories.
• Step 94: Run their gates in parallel, take the first pass, and cancel the rest.
• Step 95: Compare pass rate per minute with repair-only, and keep whichever is better.
Checkpoint: you have a measured choice between repair and sampling.
Phase 9: Traces and v1 packaging (steps 96 to 100)
• Step 96: Point ui/dashboard.py at the real event traces and render a run timeline: prompt, retrieved example, raw output, verdicts.
• Step 97: Add an MCP tool get_run(run_id) that returns that trace.
• Step 98: Rewrite README.md to match reality: what it does, the measured numbers from docs/BASELINE.md, and known limitations.
• Step 99: Add CI that installs the tools, runs the tests, and runs a 3-case benchmark smoke test.
• Step 100: Define the v1 acceptance criteria and tag v1.0 only when they are met: tests green in CI, every oracle mutant caught, the benchmark table committed, and the pass rate on at least 10 cases at or above the target you set after step 80.
After v1
A second protocol (SPI or UART), LLM agents for messy specs, multiple models, and LoRA trained on the verified dataset. Do these only once the v1 benchmark can tell you whether each one helps.
