# ProtoForge Engineering Rules

## Mission
Make small local language models, initially
qwen2.5-coder:3b, generate valid, reproducible, synthesizable
multi-module I2C RTL through a reliable generation pipeline.

Do real engineering work. Do not stop after inspecting the repository
or proposing a plan.

## Required working loop
1. Inspect the architecture, README, testbenches, golden references,
   generator, repair logic, and current Git diff.
2. Run the existing tests and record the initial failures.
3. Identify one concrete correctness or reliability problem.
4. Add a regression test that exposes it when practical.
5. Make the smallest justified change.
6. Rerun the targeted test and the complete Python test suite.
7. When Ollama and Icarus are available, run real model generation,
   compilation, and protocol simulation.
8. Inspect failure logs and waveforms when needed.
9. Repeat the cycle. Do not stop at the first passing unit test.

## RTL correctness
- Compilation alone does not mean an I2C implementation works.
- Check START, STOP, address transfer, bit ordering, ACK/NACK,
  data transfer, timing, reset, and completion behavior.
- Check synthesizability and legal open-drain SDA/SCL behavior.
- Never accept a candidate solely because it looks structurally valid.
- Report compiler errors, protocol failures, timeouts, and tool
  unavailability distinctly.
- Never substitute a known-good design for a failed model candidate
  and count that as model success.

## Oracle integrity
- Do not weaken testbench assertions, remove tests, falsify metrics,
  or modify golden references simply to produce passing results.
- If an oracle is genuinely defective, explain the evidence and
  add a separate test demonstrating the issue.
- Keep model-generated candidates separate from reference RTL.
- Only mark RTL as verified after all required validation stages pass.

## Small-model strategy
Prioritize approaches that reduce what the model must reason about:
- constrained outputs or an intermediate representation;
- deterministic wrapper and module generation;
- explicit protocol state and timing contracts;
- error-specific repair prompts;
- retrieval of verified examples and relevant failure cases;
- syntax, lint, compile, simulation, and synthesis gates;
- reproducible experiment logs.

Prefer a deterministic compiler from a constrained representation
over asking a small model to invent arbitrary RTL when feasible.
Do not introduce RAG, frameworks, or dependencies without evidence
they improve reliability.

## Evaluation
- Establish a real baseline before changing model performance claims.
- Measure first-attempt success separately from post-repair success.
- When tools permit, evaluate at least 20 actual model trials.
- Record model, parameters, seed, candidate hash, failures, latency,
  repair count, and final simulation outcome.
- Report unique candidate count so repeated outputs are not mistaken
  for diverse successful generations.
- Never claim real simulation or synthesis passed unless it ran.

## Practical restrictions
- Discover the actual project commands before assuming entry points.
- Do not use sudo, delete user data, reset Git history, or run
  destructive cleanup commands.
- Do not overwrite unrelated work or secrets.
- Keep a clear Git diff and commit only coherent checkpoints.
- If a tool is missing, diagnose the blocker and continue with
  independent tasks rather than fabricating results.

## Completion
Continue working through test-driven iterations while progress is
being made. Do not finish after writing a plan or a README.
Finish only after a meaningful, measured improvement, or when an
external blocker genuinely prevents further progress.

Final report must give exact commands run, test counts, real RTL
trial results, remaining failures, and changed files.
