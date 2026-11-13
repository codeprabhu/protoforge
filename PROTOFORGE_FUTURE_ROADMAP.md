# ProtoForge Future Roadmap: Auditable Multi-Agent RTL Generation

**Purpose:** This is the implementation plan for evolving ProtoForge from its current single-model generation-and-repair harness into a reliable, understandable, multi-agent RTL engineering system.

**Primary research question:** Can a coordinated set of specialized reasoning agents produce more correct, synthesizable, protocol-compliant RTL than the existing single-agent baseline, while providing a human-auditable explanation of the decisions and evidence behind the result?

**Non-negotiable principle:** Reliability comes from explicit contracts, independent verification, reproducible experiments, and evidence. It does not come from asking a model to “think harder,” printing long explanations, or adding agents without defined responsibilities.

---

## 1. Target outcome

The future ProtoForge should accept a hardware/protocol task and produce:

1. a structured interpretation of the specification;
2. a traceable list of requirements and their source locations;
3. a proposed architecture and module/FSM plan;
4. generated RTL or constrained intermediate representation;
5. independent review findings;
6. compile, lint, synthesis-oriented, simulation, and protocol-oracle results;
7. bounded, failure-specific repair iterations;
8. a complete run record with candidate hashes and model/tool metadata;
9. a concise, evidence-backed explanation of what each agent decided and why; and
10. a final result classified as verified, failed, or blocked, never “probably correct.”

The system must preserve the current single-agent pipeline as a baseline and fallback. The multi-agent path must be selectable, separately logged, and benchmarked under comparable conditions.

## 2. Research transparency: do not design around hidden chain-of-thought

The requirement is that a human should understand what the agents did, what evidence they used, what assumptions they made, what they chose, what they rejected, and what happened when their output was tested.

Do **not** build the system around extracting or exposing hidden private chain-of-thought. A model may not provide a faithful or supported transcript of its internal reasoning. A long free-form “thinking” field can be incomplete, post-hoc, or fabricated and is not a reliable audit artifact.

Instead, implement **Structured Decision Records (SDRs)** and an event timeline. Each agent must provide a concise, inspectable decision summary with:

- role and objective;
- input artifact IDs and hashes;
- requirements considered;
- evidence references (spec section, file/line, test, tool output, or prior artifact);
- assumptions and unresolved questions;
- findings, with severity and confidence;
- selected approach and concise rationale;
- rejected alternatives and the reason for rejection;
- output artifact IDs and hashes;
- risks and expected failure modes;
- next action / handoff target.

The orchestrator must store the model's structured response as supplied, validate its schema, and distinguish it from tool-observed facts. Tool results must be captured separately. The dashboard must never label an SDR as “the model's exact internal thoughts.” Use labels such as **Decision summary**, **Evidence**, **Assumptions**, **Review findings**, and **Verification results**.

A good audit trail should let a reviewer answer:
- What did this agent receive?
- What requirements did it believe it needed to satisfy?
- Which source evidence supports its interpretation?
- What did it output?
- What changed between versions?
- Which checks passed or failed?
- Why did the orchestrator continue, repair, reject, or stop?

## 3. Target architecture

```text
                       User task / specification
                                  |
                                  v
                       Task + requirement contract
                                  |
                                  v
                       Specification Analyst
                                  |
                       Parsed requirements + SDR
                                  |
                                  v
                         Architecture Planner
                                  |
                   module map / FSM plan / interfaces
                                  |
                                  v
                          Design Reviewer
                                  |
                     review findings + SDR
                                  |
                                  v
                         RTL Implementer
                                  |
                       RTL / IR candidate + SDR
                                  |
                                  v
                       Independent RTL Reviewer
                                  |
                       findings / change requests
                                  |
                                  v
                        Deterministic Toolchain
              structural -> compile -> lint -> Yosys
                      -> simulation -> oracle
                                  |
                    +-------------+-------------+
                    |                           |
                  PASS                         FAIL
                    |                           |
                    v                           v
             Verified artifact           Failure Analyst
                    |                           |
                    v                           v
             Final audit report          Repair Planner
                                                |
                                                v
                                           RTL Repairer
                                                |
                                                +----> rerun all gates

        All stages write immutable artifacts, events and SDRs
        under one run ID; the orchestrator owns transitions.
```

### 3.1 Orchestrator

The orchestrator is the only component allowed to advance workflow state. Agents propose outputs; they do not directly invoke one another or silently change run state.

Responsibilities:
- create run/task IDs;
- load configuration and domain adapter;
- enforce legal state transitions;
- call agents in the prescribed order;
- validate agent output schemas;
- store input/output artifact references;
- invoke deterministic tools;
- apply retry, repair, and stop budgets;
- record events and decisions;
- produce the final verdict.

### 3.2 Specialist agents

Use role-specific prompts and strict input/output contracts. The first implementation may use the same local Ollama model for every role, invoked sequentially. “Multi-agent” means distinct role contexts, outputs, and review responsibilities, not necessarily multiple models or simultaneous inference.

| Agent | Responsibility | Must produce |
|---|---|---|
| Specification Analyst | Extract protocol and design requirements from the supplied specification. | Requirement matrix, ambiguities, source evidence, SDR. |
| Architecture Planner | Convert requirements into module boundaries, interfaces, FSM states, transitions, timing obligations, and data paths. | Architecture plan, interface contract, state-transition table, SDR. |
| Design Reviewer | Independently inspect the proposed architecture against requirements. | Findings with severity/evidence, approval or required changes, SDR. |
| RTL Implementer | Generate RTL or a constrained representation from the approved plan. | Candidate artifact, requirements addressed, implementation notes, SDR. |
| RTL Reviewer | Review candidate independently for structural, synthesizability, protocol, reset, and state-machine risks. | Findings linked to source locations and requirements, SDR. |
| Failure Analyst | Interpret actual compiler/linter/simulator/oracle failures. | Failure classification, root-cause hypothesis, evidence, SDR. |
| Repair Planner | Convert verified failure evidence into a minimal repair plan. | Ordered change list, affected requirements, regression tests, SDR. |
| RTL Repairer | Apply the approved repair plan to the candidate. | New candidate artifact, change summary, SDR. |

The reviewer agents must not be allowed to certify their own output. The RTL Reviewer is distinct from the Implementer. The actual oracle and toolchain remain authoritative over both.

### 3.3 Domain adapter

Keep protocol-specific knowledge in domain adapters. The generic orchestrator and agent contracts should not contain I²C-specific assumptions.

The first release remains I²C. A later SPI adapter should implement the same domain contract, with its own specification parser, timing/transaction model, testbench, oracle, and benchmark cases.

### 3.4 Deterministic compiler and scaffolding

Prefer a constrained intermediate representation when it can express the required behaviour unambiguously. However, the current FSM JSON path did not outperform free-form RTL and achieved 0/24 in the recorded experiment. Do not simply reactivate it and assume it is better.

Any future IR must:
- express every protocol-critical operation the target needs;
- have a versioned schema;
- be validated before compilation;
- reject ambiguous or missing fields;
- compile deterministically;
- preserve traceability from each generated construct to the requirement/plan that motivated it;
- have its own benchmark comparison against free-form RTL.

If the IR cannot represent a required behaviour, fail clearly rather than silently inventing it.

---

## 4. Shared contracts and data model

Before implementing agents, define stable Pydantic contracts. Avoid agents passing arbitrary prose blobs to one another.

### 4.1 Task

A task should include:
- `task_id`, `run_id`, `domain`, `description`;
- specification artifact reference and hash;
- requirements;
- target module/interface constraints;
- benchmark case ID, if applicable;
- model and generation configuration;
- time/token/repair budgets;
- status and current stage.

### 4.2 Requirement

Each requirement should have:
- stable `req_id`;
- requirement text and severity;
- source artifact ID, path, section or line range;
- acceptance condition;
- verification method (structural, compile, lint, simulation, oracle, manual);
- status: active, satisfied, failed, blocked, or ambiguous.

Every generated architecture element and important review finding should refer to requirement IDs.

### 4.3 Agent input

Every agent input should be explicit:
- task contract;
- role objective;
- relevant requirement subset;
- approved prior artifacts;
- permitted tools;
- output schema;
- constraints and budget.

Do not give every agent the entire repository by default. Provide only relevant, versioned context and record exactly what was supplied.

### 4.4 Agent output

Each response should validate against a typed schema containing:
- `agent_run_id`, `role`, `model`, `task_id`;
- `input_artifact_ids`;
- `decision_summary`;
- `evidence[]`;
- `assumptions[]`;
- `findings[]`;
- `selected_approach`;
- `rejected_alternatives[]`;
- `output_artifact_ids`;
- `risks[]`;
- `next_action`;
- token counts, latency, and termination status where available.

Keep raw model response, parsed structured response, and tool observations as separate fields. If parsing fails, record an agent-output failure and do not silently convert it into a success.

### 4.5 Artifact

Every significant artifact should be immutable and content-addressed:
- artifact ID;
- artifact type;
- path;
- SHA-256;
- producing agent/tool;
- parent artifact IDs;
- requirements addressed;
- creation time;
- schema/version.

A repaired candidate is a new artifact with a parent link to the previous candidate. Never overwrite the original candidate.

### 4.6 Decision record

Use the existing `DecisionRecord` as a starting point, but extend it to include artifact IDs, evidence references, output references, confidence calibration, and schema version. Do not treat its current existence as proof of a working agent pipeline.

---

## 5. Workflow state machine

Implement explicit states and legal transitions:

```text
CREATED
  -> SPEC_ANALYSIS
  -> ARCHITECTURE_PLANNING
  -> ARCHITECTURE_REVIEW
  -> RTL_IMPLEMENTATION
  -> RTL_REVIEW
  -> VERIFICATION
       -> VERIFIED
       -> FAILURE_ANALYSIS
           -> REPAIR_PLANNING
           -> REPAIR
           -> VERIFICATION (bounded)
       -> FAILED
       -> BLOCKED
```

Rules:
- Every transition emits an event with timestamp, actor, reason, and input/output artifact references.
- A state transition is rejected if its prerequisites are missing.
- A failed or blocked stage cannot be marked successful by an agent's prose.
- The orchestrator enforces maximum agent calls, model tokens, wall-clock time, and repair attempts.
- No infinite review/repair loops.
- Human approval checkpoints should be configurable for high-impact architecture changes or when ambiguity cannot be resolved from the specification.

---

## 6. Verification and reliability rules

The verification system must remain independent of agent self-assessment.

### 6.1 Required verification gates

For the I²C implementation:
1. specification/requirement completeness checks;
2. structural checks;
3. Icarus compile;
4. Verilator lint;
5. Yosys processing/checks;
6. Icarus simulation;
7. protocol oracle/judge;
8. optional mutation tests of the oracle in the test suite.

A candidate is **verified** only if every required gate for that task passes. A missing tool is `BLOCKED`, not a model failure. Timeout, runner exception, malformed output, compile error, lint failure, simulation failure, and protocol failure must be distinct outcomes.

### 6.2 Independent review is not verification

Agent reviews are useful for catching likely problems early. They are not a substitute for compiler, synthesis, simulation, or oracle evidence.

A reviewer may say “no issue found”; that must never change a failed tool result into a pass.

### 6.3 Oracle integrity

- Never change the golden reference or weaken assertions to make a candidate pass.
- Oracle changes require separate justification, a regression demonstrating the old oracle defect, and mutation tests.
- Keep candidate code, golden/reference code, and testbench code in separate paths.
- Preserve all failed candidates and their diagnostics.
- Never replace a failed model candidate with a template/golden implementation and count it as a model success.

### 6.4 Repair policy

Repairs must be:
- bounded;
- based on actual failure evidence;
- linked to a failure analysis and repair plan;
- minimal rather than broad rewrites;
- saved as new candidate artifacts;
- re-run through the entire required verification pipeline.

Record whether a repair fixed the original failure, introduced a new one, or made no progress.

---

## 7. Human-readable reasoning and audit UI

Build the transparency feature around the run timeline and decision records.

For each agent stage, display:

1. **Objective:** what the role was asked to do.
2. **Inputs:** specification, requirements, and prior artifacts used.
3. **Evidence:** source sections, code locations, tool output, and test IDs.
4. **Decision summary:** concise rationale for the selected approach.
5. **Assumptions:** what the agent inferred but could not verify.
6. **Alternatives:** options considered and why they were rejected.
7. **Findings:** severity, evidence, and affected requirements.
8. **Output:** links to generated artifacts and hashes.
9. **Verification:** actual tool verdicts, not agent claims.
10. **Next action:** why the orchestrator advanced, repaired, blocked, or stopped.

The UI should make it possible to inspect the run from top to bottom and drill into each artifact. It should show differences between successive RTL candidates and identify which requirements changed or remain unsatisfied.

Use clear status labels:
- `PASS`: all required external verification gates passed;
- `FAIL`: a candidate failed a required gate;
- `BLOCKED`: required tool/context was unavailable;
- `ERROR`: harness or agent execution failed;
- `REVIEW_REQUIRED`: unresolved ambiguity or high-severity finding;
- `RUNNING`: stage in progress.

Do not display a generic “confidence 95%” unless it is calibrated against historical outcomes. If confidence is included, document how it is derived and evaluate calibration.

---

## 8. Resource-aware execution strategy

The recorded development environment is a laptop with an RTX 3050 Mobile GPU with 4 GiB VRAM. The README notes that the 7B model is partially CPU-offloaded and that calls take roughly 90–200 seconds. Do not assume multiple model calls can safely run in parallel.

Initial implementation should:
- call the local model sequentially;
- reuse one loaded model where supported;
- avoid loading several models simultaneously;
- give each role a smaller, focused context;
- set per-agent token/time budgets;
- cache immutable inputs and deterministic tool results;
- record actual latency and token usage per role;
- allow a cheap deterministic check to reject an invalid handoff before another LLM call.

Multi-agent coordination can still be meaningful when the same model is called several times with different roles and independent context. The evaluation must state that this is role-separated sequential inference using one model, not independent specialist models.

---

## 9. Evaluation design

The purpose of the multi-agent system is to improve reliable RTL generation, not merely to create more elaborate logs.

### 9.1 Baselines

Retain these comparisons:
- deterministic template upper bound;
- current single-agent RTL path with retrieval and patcher;
- single-agent with repair;
- multi-agent with the same model and comparable total generation budget;
- optional IR path only if its schema/compiler is repaired and it merits a new controlled experiment.

The template is a correctness/coverage reference, not an LLM competitor. The existing `on-on` result of 16/24 (66.7%) is the recorded model baseline. Re-run and freeze a fresh baseline before major architectural changes if the code or model configuration has changed.

### 9.2 Fair comparison

For single-agent versus multi-agent:
- same benchmark cases;
- same golden references and oracle;
- same model digest/version;
- same temperature/seed policy where applicable;
- comparable total token and wall-clock budgets;
- same toolchain;
- same repair budget or explicitly report differences;
- frozen retrieval corpus;
- identical pass/fail definitions.

Do not give the multi-agent system four times the model budget and then attribute all gains to agent coordination. Report both equal-budget and practical-budget comparisons if useful.

### 9.3 Metrics

Report at least:
- pass@1;
- final pass rate after repair;
- repair recovery rate;
- failure stage distribution;
- no-candidate / agent-output / runner-error counts;
- unique candidate hashes;
- number of model calls;
- total prompt/completion tokens;
- median and p95 latency;
- tool execution time;
- cost if applicable;
- per-case and per-protocol success;
- requirement coverage;
- number and severity of reviewer findings;
- reviewer false-positive/false-negative rates where measurable;
- human assessment of explanation usefulness, separately from correctness.

Use confidence intervals and sufficient sample sizes. Twenty-four runs are useful as an initial comparison but are not enough to make very strong general claims. Preserve raw per-run evidence and generated reports.

### 9.4 Held-out protocol

After the I²C multi-agent flow is stable, implement SPI as a held-out generalization test:
- independently sourced/defined SPI specification;
- separate domain adapter;
- independent testbench and oracle;
- no SPI golden RTL in the retrieval corpus;
- clearly documented task coverage;
- baseline and multi-agent results under the same conditions.

Only after this should ProtoForge make claims about protocol transfer or generality.

---

## 10. Phased implementation roadmap

Do these phases in order. Each phase has a gate. Do not begin the next phase merely because files have been created.

### Phase 0 — Stabilize and freeze the current baseline

**Goal:** Ensure the existing single-agent harness is trustworthy before changing its architecture.

Tasks:
1. Inspect the current Git status, branch, latest commit, and uncommitted changes. Do not reset or overwrite user work.
2. Read `AGENTS.md`, README, workflow, baseline, oracle, and acceptance documents.
3. Fix the CI workflow's Python environment. The current snapshot installs dependencies into the runner Python but invokes `.venv/bin/python` without creating `.venv`.
4. Inspect the latest failing GitHub Actions test logs and identify exact test failures.
5. Reproduce failures locally and classify them as application bug, test assumption, missing dependency, or CI environment issue.
6. Fix underlying problems without weakening assertions or skipping tests just to obtain green CI.
7. Run the full test suite and static lint.
8. Run oracle mutation tests with real tools.
9. Run a small real-model generation and record environment/model digest.
10. Reconcile README, `docs/WORKFLOW.md`, and `docs/V1_ACCEPTANCE.md`.
11. Resolve the Best-of-N status contradiction: the workflow board says not started while README reports Best-of-4 results. Verify raw evidence or correct the claim.
12. Commit a frozen baseline checkpoint and record the exact commit.

**Gate:** CI is green on GitHub; local tests and lint pass; oracle mutations are caught; benchmark artifacts agree; the baseline can be replayed. No multi-agent code changes before this gate.

### Phase 1 — Audit and formalize current run records

**Goal:** Make every current single-agent run auditable using the same data model planned for multi-agent runs.

Tasks:
1. Inspect `DecisionRecord`, `RunTrace`, `TraceEvent`, `EventStore`, `AttemptRecord`, and dashboard code.
2. Map which events are actually emitted by the live orchestrator.
3. Identify schema fields that are present but unused.
4. Define versioned `AgentRunRecord`, `DecisionRecord`, `ArtifactRecord`, `EvidenceReference`, and `Finding` schemas.
5. Add schema validation and serialization/deserialization tests.
6. Record input/output artifact IDs and SHA-256 hashes.
7. Ensure raw model response, parsed decision summary, and external tool observations are separate.
8. Add explicit statuses for pass, fail, blocked, error, review-required, and running.
9. Make trace persistence failure visible in diagnostics without allowing it to corrupt candidate verification.
10. Update the dashboard to show the existing single-agent run timeline accurately.

**Gate:** A single-agent run can be reconstructed from stored artifacts and events. The UI does not imply that unused agent roles ran. Trace schema migration is tested against existing trace files.

### Phase 2 — Build the agent execution framework without changing RTL generation

**Goal:** Introduce role-separated agent calls and strict handoff contracts, initially using mocked agents.

Tasks:
1. Define an `Agent` protocol/interface with role, objective, input schema, output schema, model settings, and execute method.
2. Implement an Ollama-backed agent runner.
3. Implement deterministic mock agents for tests.
4. Add structured output parsing and validation.
5. Add timeout, retry, token, and call-count budgets.
6. Persist every input, raw response, parsed output, and SDR.
7. Add artifact lineage and immutable candidate/output storage.
8. Implement an orchestrator state machine with legal transitions.
9. Reject invalid transitions and malformed outputs.
10. Add tests for model timeout, malformed JSON, missing evidence, invalid artifact references, repeated retries, and interrupted runs.
11. Keep all model calls sequential initially.
12. Do not connect the new framework to the live RTL path yet.

**Gate:** The framework can run a mock multi-agent workflow end-to-end, persist a complete trace, recover from expected failures, and stop within configured budgets. Existing single-agent tests remain green.

### Phase 3 — Specification Analyst and requirement traceability

**Goal:** Convert the current I²C spec into an explicit, reviewable requirement set.

Tasks:
1. Implement the Specification Analyst role.
2. Feed it the I²C spec, device contracts, timing contract, and task configuration.
3. Require structured requirement outputs with stable IDs.
4. Require source section/line evidence for each extracted requirement.
5. Distinguish explicit specification statements from assumptions/inferences.
6. Add ambiguity detection and a `REVIEW_REQUIRED` status.
7. Validate requirement coverage against a deterministic checklist derived from the current oracle.
8. Compare the agent output against a manually curated requirement set.
9. Measure omissions, unsupported additions, and incorrect interpretations.
10. Store the requirement matrix and SDR as immutable artifacts.

**Gate:** Every mandatory oracle behaviour maps to at least one requirement; source evidence is present; ambiguous statements are not silently treated as facts.

### Phase 4 — Architecture Planner and independent Design Reviewer

**Goal:** Make the intended RTL architecture explicit before code generation.

Tasks:
1. Implement Architecture Planner input/output schemas.
2. Generate module/interface maps and signal ownership.
3. Generate FSM states, transitions, reset behaviour, and terminal conditions.
4. Specify bit ordering, address/data sequencing, ACK/NACK behaviour, START/STOP, byte counters, and timing obligations.
5. Map each architecture decision to requirement IDs.
6. Implement an independent Design Reviewer with separate prompt/context.
7. Require findings to cite requirements and architecture artifact locations.
8. Add deterministic checks for required states, transitions, signals, and interface consistency where possible.
9. Require the planner to revise its output when a blocking review finding is valid.
10. Bound review iterations and record all rejected versions.

**Gate:** The architecture plan is internally consistent, covers every mandatory requirement, and has no unresolved critical/high-severity design findings. The reviewer cannot approve by changing the oracle or ignoring requirements.

### Phase 5 — RTL Implementer and independent RTL Reviewer

**Goal:** Generate RTL from an approved architecture plan and review it before expensive verification.

Tasks:
1. Implement the RTL Implementer role.
2. Give it the approved requirement matrix and architecture plan, not an unbounded repository dump.
3. Require it to identify which requirements each module/state/transition addresses.
4. Store generated RTL as an immutable candidate artifact with hash and parent references.
5. Implement RTL Reviewer with a separate role prompt and structured findings.
6. Review interface conformance, reset, state transitions, nonblocking assignments, bit order, ACK/NACK, open-drain behaviour, timing, synthesizability, and suspicious hard-coded assumptions.
7. Require source line references for code findings.
8. Run deterministic structural checks regardless of reviewer opinion.
9. Permit one bounded implementer revision per review cycle.
10. Do not let the implementer author its own approval.

**Gate:** The candidate is traceable to the architecture plan and requirements; structural checks pass; no blocking review findings remain. It is still unverified until external tool gates pass.

### Phase 6 — Integrate multi-agent workflow with the existing I²C oracle

**Goal:** Connect the new agent workflow to the existing deterministic verification pipeline.

Tasks:
1. Add a separate multi-agent execution mode; preserve the current single-agent mode unchanged.
2. Connect candidate output to the existing I²C domain adapter.
3. Run the same structural, Icarus, Verilator, Yosys, simulation, and judge gates.
4. Store full stdout/stderr, diagnostics, timings, and verdicts.
5. On failure, invoke Failure Analyst with actual tool output and candidate hash.
6. Require Failure Analyst to classify the failure stage and cite evidence.
7. Require Repair Planner to propose minimal changes tied to failed requirements.
8. Require RTL Repairer to create a new candidate artifact.
9. Re-run all required gates after every repair.
10. Enforce repair and total-call budgets.
11. Preserve every failed candidate and repair version.
12. Never allow an agent to override an external gate verdict.

**Gate:** A multi-agent run completes end-to-end, and every decision and verification result is reconstructable. The current single-agent path still works and its baseline is unchanged.

### Phase 7 — Human-readable audit dashboard

**Goal:** Make the workflow understandable to a developer without reading raw prompts or model logs.

Tasks:
1. Render an ordered run timeline.
2. Show role, objective, input artifacts, output artifacts, and duration for every agent call.
3. Show Decision Summary, Evidence, Assumptions, Findings, Alternatives, and Next Action.
4. Show requirement coverage and unresolved requirements.
5. Show the architecture plan and FSM transition table.
6. Show candidate RTL versions and diffs.
7. Show candidate hashes and artifact lineage.
8. Show all tool gate results with real diagnostics.
9. Show repair history and whether each repair improved the result.
10. Provide export to JSON and Markdown.
11. Clearly distinguish agent claims from tool-observed facts.
12. Do not display private chain-of-thought or label summaries as exact internal thoughts.

**Gate:** A reviewer can inspect one run and answer what was asked, what evidence was used, what changed, why the workflow advanced/repaired/stopped, and whether the final RTL actually passed the oracle.

### Phase 8 — Controlled multi-agent benchmark

**Goal:** Test whether agent coordination improves reliability.

Tasks:
1. Freeze the single-agent baseline and exact model digest.
2. Define multi-agent benchmark configuration and per-role budgets.
3. Compare single-agent and multi-agent on the same 12 I²C cases.
4. Run enough samples to estimate rates; begin with at least 20 per configuration, then increase if resources permit.
5. Compare under equal total token/call budget as well as a practical multi-agent budget.
6. Freeze retrieval memory for controlled comparisons.
7. Record pass@1, final pass rate, repair recovery, failures by stage, unique candidate hashes, model calls, tokens, median/p95 latency, and per-case outcomes.
8. Include no-candidate, malformed-agent-output, timeout, runner-error, and missing-tool categories separately.
9. Calculate confidence intervals and preserve per-run raw artifacts.
10. Evaluate decision-record usefulness separately from RTL correctness.
11. Perform an ablation removing one agent role at a time to determine whether each role contributes.
12. Investigate regressions rather than changing the oracle or excluding difficult cases.

**Gate:** The report contains reproducible raw evidence and a fair comparison. The project may conclude that multi-agent does not improve correctness; that is a valid research result. Do not claim success based only on longer explanations or more agent activity.

### Phase 9 — Held-out SPI generalization

**Goal:** Determine whether the architecture transfers beyond the I²C task it was developed around.

Tasks:
1. Define a separate SPI protocol specification and requirement set.
2. Implement an SPI domain adapter.
3. Build an independent SPI testbench and protocol oracle.
4. Create benchmark cases covering modes, clock polarity/phase, bit order, word length, chip select, and relevant timing.
5. Keep SPI golden RTL out of retrieval memory for held-out evaluation.
6. Establish a deterministic/template and single-agent baseline where feasible.
7. Run the multi-agent workflow on the held-out cases.
8. Report per-feature and overall results.
9. Keep the I²C and SPI results separate; do not aggregate incompatible tasks into one headline score.

**Gate:** The SPI adapter has independent tests and oracle evidence, and held-out results are reproducible.

### Phase 10 — Research packaging and v2 release

**Goal:** Produce a defensible research artifact.

Tasks:
1. Document architecture, agent roles, schemas, and state machine.
2. Document the exact model(s), quantization, runtime, hardware, seeds, prompts/configuration versions, and tool versions.
3. Publish baseline and multi-agent benchmark methodology.
4. Include raw data and scripts required to reproduce tables.
5. Document failures, limitations, and negative results.
6. Include examples of audit traces and candidate diffs.
7. Document security boundaries and model/tool trust assumptions.
8. Tag a release only after the acceptance checklist passes.
9. Preserve v1 single-agent mode as a reproducible comparator.

**Gate:** A third party can install the environment, run tests, replay saved candidates, and reproduce the reported benchmark analysis within documented tolerances.

---

## 11. Suggested initial role prompt contracts

These are role responsibilities, not free-form instructions to “think step by step.”

### Specification Analyst

**System responsibility:** Extract requirements only from the supplied specification and cited source artifacts. Do not invent requirements. Separate explicit statements, derived constraints, and unresolved ambiguities. Every requirement must have source evidence and a proposed verification method. Output the required JSON schema and a concise decision summary.

### Architecture Planner

**System responsibility:** Produce an implementable RTL architecture from the approved requirement set. Define module boundaries, interfaces, signal ownership, FSM states/transitions, reset behaviour, timing, data paths, and terminal conditions. Map every design choice to requirement IDs. List assumptions and unresolved questions. Do not write final RTL unless explicitly requested by the orchestrator.

### Design Reviewer

**System responsibility:** Independently review the architecture against the requirements. Search for omissions, contradictions, illegal transitions, incomplete reset/completion behaviour, timing ambiguity, and interface mismatches. Every finding must cite a requirement and architecture location. Do not rewrite the plan silently. Return approve, revise, or blocked with evidence.

### RTL Implementer

**System responsibility:** Implement only the approved architecture. Preserve required interfaces and deterministic wrapper contracts. Map implementation elements to requirement IDs. Do not modify golden references, testbench, or oracle. Return candidate RTL as an artifact plus a structured decision summary and known limitations.

### RTL Reviewer

**System responsibility:** Independently inspect the candidate RTL against the requirement set and architecture plan. Review protocol correctness risks, synthesizability, state transitions, reset, ACK/NACK, timing, bit order, open-drain rules, and interface compliance. Cite exact source locations. Do not claim the candidate is verified; only external tools can establish that.

### Failure Analyst

**System responsibility:** Analyze only observed failure evidence from the compiler, lint/synthesis tools, simulator, oracle, and harness. Classify the failure and distinguish root cause from hypothesis. Cite exact diagnostics and affected requirement IDs. Do not invent tool output or assume a model failure when the tool was missing or the runner crashed.

### Repair Planner

**System responsibility:** Propose the smallest change that addresses the verified failure. Identify affected files/RTL regions, requirements, expected side effects, and regression checks. Do not change the oracle or golden reference. If evidence is insufficient, request more diagnostics rather than guessing.

### RTL Repairer

**System responsibility:** Apply only the approved repair plan to create a new candidate artifact. Preserve the previous version. Explain the change at a high level, map it to the failure and requirements, and list residual risks. Do not mark the result as passing; return it to the deterministic verification pipeline.

---

## 12. Instruction to the coding agent: how to execute this roadmap

Use the following as the implementation brief. The agent must work in small, verified increments and must not attempt to build the entire multi-agent system in one large change.

### Agent execution brief

You are working on ProtoForge, an existing local-LLM-assisted RTL generation and verification harness. Your objective is to evolve it into an auditable multi-agent RTL engineering system while preserving the current single-agent implementation as a baseline.

Read first:
- `AGENTS.md`
- `README.md`
- `docs/PROTOFORGE_SYSTEM_OVERVIEW.md`
- `docs/PROTOFORGE_FUTURE_ROADMAP.md`
- `docs/WORKFLOW.md`
- `docs/BASELINE.md`
- `docs/ORACLE.md`
- `docs/V1_ACCEPTANCE.md`
- the live orchestrator, domain adapter, generator, trace schemas, attempt records, replay tools, dashboard, and tests.

#### Mandatory rules

1. Do not begin by rewriting the architecture.
2. Inspect Git status and preserve all user changes. Never reset history or overwrite unrelated work.
3. Run the current test suite first and record exact failures.
4. Fix Phase 0 stabilization problems before multi-agent integration.
5. Keep the current single-agent path operational and separately selectable.
6. Do not weaken the oracle, testbench, assertions, or golden references.
7. Do not fabricate test results or mark unexecuted checks as passed.
8. Treat model output as untrusted.
9. Use typed schemas and validate every agent handoff.
10. Use immutable artifacts with hashes and parent lineage.
11. Keep model calls sequential initially because the target laptop has limited VRAM.
12. Bound agent calls, retries, token usage, and wall-clock time.
13. Store raw model responses, structured decision summaries, and tool observations separately.
14. Do not attempt to expose hidden private chain-of-thought. Implement concise, evidence-backed decision records and a chronological audit trail instead.
15. An agent's self-review is not external verification.
16. Every candidate, including repaired candidates, must pass the full required deterministic verification pipeline.
17. Every meaningful change must have regression tests.
18. Run targeted tests, then the complete test suite, then relevant real-tool/model checks where available.
19. Update documentation only after implementation and tests verify the behaviour.
20. Commit coherent checkpoints with descriptive messages; do not tag a release without human approval.

#### Execution protocol

At the start of each phase:
- state the phase and exact acceptance criteria;
- inspect the relevant code and tests;
- list the smallest implementation steps;
- identify risks and files likely to change.

For each implementation step:
- add or update a test;
- implement the smallest change;
- run the targeted test;
- run related tests;
- inspect the diff;
- record actual results.

At the end of each phase:
- run the complete test suite;
- run static lint;
- run relevant external-tool checks;
- report exact commands, counts, skipped tests, and failures;
- list files changed;
- update the phase status and evidence;
- stop if the gate is not met.

Do not silently proceed past a failed gate. Explain the blocker and propose the smallest next action.

#### First assignment

Start with **Phase 0 only**.

In particular:
- inspect the current CI workflow and fix its environment setup;
- inspect the latest GitHub Actions failure logs;
- reproduce and fix the actual failing tests;
- reconcile contradictory Best-of-N and acceptance documentation;
- verify the current baseline artifacts;
- establish a clean, passing single-agent checkpoint.

Do not implement the specialist agents until Phase 0 is complete and the user has reviewed the checkpoint report.

#### Required final report after each phase

Provide:
1. Phase and acceptance criteria.
2. Findings from source inspection.
3. Files changed.
4. Tests added/changed.
5. Exact commands executed.
6. Actual test and CI results.
7. Real model/tool results, if executed.
8. Remaining failures and blockers.
9. Artifact/commit references.
10. Whether the phase gate is met.
11. The next recommended phase, without starting it automatically.

The goal is a reliable and explainable RTL engineering harness, not a large codebase with many nominal agents. Demonstrate measurable correctness improvements and make every important decision traceable to evidence.
