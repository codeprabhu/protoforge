# Phase 1 Implementation Specification

**Version:** 1.0
**Date:** 2026-11-11
**Status:** Implementation Ready

---

## 1. Overview

This specification defines the implementation of Phase 1: Multi-Agent Architecture for ProtoForge v2.

The goal is to replace the single-agent orchestration loop with a specialist multi-agent system while preserving the existing verified single-agent baseline as a regression path.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PHASE 1 MULTI-AGENT ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐   │
│  │   HUMAN      │    │    CONDUCTOR     │    │        ORACLE          │   │
│  │  (approvals, │    │  (state machine, │    │  (frozen, read-only)   │   │
│  │   budgets,   │    │   budgets,       │    │   - testbench          │   │
│  │   oracle     │    │   scheduling,    │    │   - BFM                │   │
│  │   changes)   │    │   evidence,      │    │   - judge              │   │
│  └──────┬───────┘    │   blackboard)    │    │   - timing monitor     │   │
│         │            └────────┬─────────┘    │   - coverage map       │   │
│         │                     │              └───────────┬─────────────┘   │
│         ▼                     ▼                        ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    BLACKBOARD (append-only, hash-chained)        │   │
│  │  events.jsonl │ evidence/ │ records/ │ candidates/ │ lessons/   │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                        │
│         ┌─────────────────────┼─────────────────────┐                 │
│         ▼                     ▼                     ▼                 │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐           │
│  │   AGENTS    │      │    TOOLS    │      │   MEMORY    │           │
│  │             │      │             │      │             │           │
│  │ spec_analyst│      │ compile_tool│      │ lessons/    │           │
│  │ architect   │      │ lint_tool   │      │ failed/     │           │
│  │ implementer │      │ sim_tool    │      │ verified/   │           │
│  │failure_an.  │      │ judge       │      │ signatures  │           │
│  │ reviewer    │      │ struct_check│      │ attempts/   │           │
│  │ test_author │      │ replay_tool │      │ ledger/     │           │
│  │ archivist   │      │             │      │             │           │
│  └─────────────┘      └─────────────┘      └─────────────┘           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Agent Contracts

### 3.1 Common Agent Interface

```python
class Agent(ABC):
    """Base class for all agents."""

    @abstractmethod
    def role(self) -> str:
        """Agent role identifier."""

    @abstractmethod
    def execute(self, context_pack: ContextPack) -> Record:
        """Execute the agent's task, return structured Reasoning Record."""

    @abstractmethod
    def validate_output(self, record: Record) -> bool:
        """Validate record conforms to schema and self-checks."""
```

### 3.2 Agent Specifications

#### 3.2.1 Specification Analyst
- **Role:** `spec_analyst`
- **Purpose:** Parse WI + spec excerpts → numbered, testable requirements
- **Inputs:** WI text, contract, timing YAML, coverage map
- **Outputs:** `requirements.json` (Requirement[]), Record
- **Model:** Reasoning model (qwen3:4b)
- **Budget:** 1 call per WI
- **Validation:** Every requirement maps to ≥1 oracle check

#### 3.2.2 Architect
- **Role:** `architect`
- **Purpose:** Design delta for WI: blocks, states, timing, invariants, risks
- **Inputs:** Requirements, contract, lessons, ledger summary
- **Outputs:** `plan.md` + Record (with `handoff.to=implementer`)
- **Model:** Reasoning model (qwen3:4b)
- **Validation:** IR validator pre-check; one retry on schema violation

#### 3.2.3 Implementer
- **Role:** `implementer`
- **Purpose:** Produce candidate artifact (IR JSON or RTL fragment)
- **Inputs:** Plan, contract, diagnosis (if repair), ledger, lessons
- **Outputs:** Candidate artifact + Record
- **Model:** Code model (qwen2.5-coder:7b)
- **Samples:** N per strategy (configurable, default 1)
- **Validation:** Schema conformance; one retry on violation

#### 3.2.4 Failure Analyst
- **Role:** `failure_analyst`
- **Purpose:** Explain failure, propose minimal fix
- **Inputs:** Failure bundle, ledger, plan, lessons
- **Outputs:** Diagnosis Record (with `handoff.to=implementer`, `must_address`, `must_not_change`)
- **Model:** Reasoning model (qwen3:4b)
- **Budget:** 1 call per failed attempt
- **Evidence Requests:** ≤1 round per attempt

#### 3.2.5 Reviewer
- **Role:** `reviewer`
- **Purpose:** Post-pass audit + oracle criticism
- **Inputs:** Passing candidate, requirements, coverage map
- **Outputs:** Review Record (never blocks promotion)
- **Model:** Different family (gemma3:4b)
- **Output:** `risk` items → `oracle_gap` events

#### 3.2.6 Test Author
- **Role:** `test_author`
- **Purpose:** Propose directed tests/mutants for coverage gaps
- **Inputs:** Coverage gaps, contract, BFM docs
- **Outputs:** Files in `proposals/oracle/`
- **Model:** Code model (qwen2.5-coder:7b)

#### 3.2.7 Archivist
- **Role:** `archivist`
- **Purpose:** Distill iterations into lessons
- **Inputs:** Iteration records/evidence
- **Outputs:** Lesson candidates + summaries
- **Model:** Small model (qwen2.5-coder:3b)

---

## 4. Core Data Structures

### 4.1 Context Pack (Input to every agent)

```python
class ContextPack(BaseModel):
    """Assembled by Conductor; immutable per call."""
    charter: str                          # Role instructions + output schema
    task: TaskInfo                        # WI id, objective, acceptance checks
    contract: ContractExcerpt             # Relevant ports/semantics
    timing_yaml: TimingConfig             # Relevant timing params
    coverage_map: CoverageMap             # Requirement → checks mapping
    upstream_records: List[Record]        # Structured records from upstream roles
    evidence: List[EvidenceSummary]       # Referenced evidence IDs
    ledger_summary: LedgerSummary         # Last 6 attempts as compact table
    lessons: List[Lesson]                 # ≤3, signature-matched
    current_artifact: Optional[Artifact]  # Current candidate (or diff)
    output_reserve: int = 1000            # Reserved tokens for answer
```

**Token Budget (num_ctx=8192):**

| Section | Budget | Dropped First |
|---------|--------|---------------|
| Charter | 700 | Never |
| Task | 500 | Never |
| Contract | 900 | Trimmed to relevant ports |
| Upstream records | 1500 | Oldest first |
| Evidence | 1500 | Lowest relevance |
| Ledger | 700 | Oldest first |
| Lessons | 600 | Candidate status first |
| Current artifact | 1000 | Full → diff |
| Output reserve | ≥1000 | Never |

---

### 4.2 Reasoning Record (Agent Output)

```python
class Record(BaseModel):
    schema: Literal["protoforge.record/1"]
    record_id: str                    # R-<wi>-<seq>
    campaign_id: str
    wi: str
    iteration: int
    attempt_id: str                   # A-<wi>-<n>
    role: AgentRole
    model: ModelInfo                  # name, digest, temp, seed, num_ctx
    inputs: RecordInputs              # context_pack_sha256, responds_to[], evidence[], lessons[]
    observations: List[Observation]   # MUST cite evidence IDs
    hypotheses: List[Hypothesis]      # status: open/confirmed/refuted/inconclusive
    decision: Decision                # action, rationale, alternatives_rejected
    assumptions: List[str]
    open_questions: List[str]
    handoff: Handoff                  # to, ask, must_address[], must_not_change[]
    self_check: SelfCheck             # every_obs_cites_evidence, no_unref_facts
```

**Validation Rules (enforced by Conductor):**
1. Every `observation` cites ≥1 evidence ID
2. `responds_to` lists ≥1 upstream record (unless first in iteration)
3. `handoff.to` is valid role; `must_address` IDs exist in this record
4. `hypotheses[].status` = `open` on creation
5. Valid JSON per schema; on violation → one retry with validator errors

---

### 4.3 Evidence Item

```python
class Evidence(BaseModel):
    evidence_id: str                    # E-<wi>-<seq>
    kind: EvidenceKind                  # gate_result | sim_event_timeline | compiler_diag | diff | measurement | waveform_slice | coverage_map
    produced_by: Literal["conductor"]
    tool: ToolInfo                      # name, version
    attempt_id: str
    artifact_path: str                  # Relative to run dir
    sha256: str
    summary: str                        # ≤600 chars, deterministic
    created_at: datetime
```

**Evidence Builders Required:**
- `gate_result`: from ValidationResult
- `sim_event_timeline`: EVENT: lines + timestamps
- `compiler_diag`: first 40 normalized lines
- `diff`: unified diff + stat
- `measurement`: timing vs limits
- `waveform_slice`: signals × time window
- `coverage_map`: requirement → checks → pass/fail

---

### 4.4 Attempt Ledger Entry

```python
class LedgerEntry(BaseModel):
    attempt_id: str                     # A-<wi>-<n>
    parent: Optional[str]               # A-<wi>-<n-1>
    strategy: StrategyName              # S0_repair, S1_new_architect, etc.
    implementer: ModelConfig
    fingerprint: str                    # SHA256 of normalized artifact
    diff_stat: DiffStat                 # added, removed, states_touched[]
    gates: GateVerdicts                 # G0..G9 pass/fail
    failed_checks: List[str]
    signature: str                      # Failure signature
    progress: ProgressScore             # (gate_depth, -failed_count)
    decision_record: str                # R-<wi>-<seq>
```

**Anti-Oscillation Rules:**
1. **Duplicate:** If `fingerprint` exists in WI → log `duplicate_candidate`, consume attempt, prompt "you reproduced attempt A-n"
2. **Ping-pong:** If `failed_checks[n] == failed_checks[n-2] != failed_checks[n-1]` → stagnation → escalate strategy
3. **Preserve List:** Conductor computes `must_not_change` from best attempt's passed gates + coverage map; failure analyst may add, never shrink

---

### 4.5 Lesson Schema

```python
class Lesson(BaseModel):
    lesson_id: str                      # L-<seq>
    signature: str                      # sha256(oracle_ver | wi | stage | sorted(checks) | diag)[:12]
    wi_scope: List[str]                 # e.g., ["WI-03", "WI-04"]
    symptom: str
    cause: str
    fix_pattern: str
    evidence: List[str]                 # Evidence IDs
    status: Literal["candidate", "trusted", "retired"]
    oracle_version: str
    uses: LessonUsage                   # retrieved, led_to_pass, led_to_regression
    created_by: str                     # archivist
```

**Status Transitions:**
| From → To | Condition |
|-----------|-----------|
| candidate → trusted | `led_to_pass ≥ 2` AND `led_to_regression = 0` |
| trusted/candidate → retired | `led_to_regression ≥ 1` OR `oracle_version` changed |

**Retrieval Priority:** Exact signature match → same stage Jaccard ≥0.5 → max 3 per pack; candidate labeled "unverified hint"

---

## 5. Conductor State Machine

### 5.1 States

| State | Actor | Input | Output | Next |
|-------|---------|-------|--------|------|
| **S0 SELECT** | Conductor | Backlog, budgets, STOP file, oracle hash | WI selected, iteration opened, `wi_selected` event | S1 if no `requirements.json`; else S2 |
| **S1 SPECIFY** | Spec Analyst | Pack (task, contract, timing, coverage) | `requirements.json` + Record | S2; any req w/o mapped check → `coverage_gap` event |
| **S2 PLAN** | Architect | Requirements, contract, lessons, ledger | `plan.md` + Record | S3; IR validator pre-check → 1 retry then S9 |
| **S3 DRAFT** | Implementer (N samples) | Plan, contract, diagnosis, ledger, lessons | N candidates + Records | S4 per candidate (stop at first pass) |
| **S4 GATES** | Conductor + Tools | Candidate, Oracle | Evidence + `gates/A-....json` + Ledger | All G0-G7(+G9) pass → S7; else S5 if budget |
| **S5 DIAGNOSE** | Failure Analyst | Failure bundle, ledger, plan, lessons | Diagnosis Record (may have `evidence_requests`) | If `evidence_requests` → Conductor runs, re-runs S5 once; else S6 |
| **S6 REPAIR** | Implementer | Diagnosis, `must_not_change`, ledger, lessons | New candidate + Record | S4 (same attempt counter +1) |
| **S7 PASS-AUDIT** | Reviewer | Passing candidate, requirements, coverage | Review Record (never blocks) | S8; `risk` items → `oracle_gap` events |
| **S8 PROMOTE** | Conductor + Archivist | Passing artifact + verdicts | (1) Hold-out scenarios; (2) Verified write + git tag + lessons | S0; hold-out fail → S5 with names-only bundle |
| **S9 ESCALATE** | Conductor | Ledger, last diagnosis | `blocked/<WI>.md`, status=blocked | S0 (next WI) |

### 5.2 Budget & Stop Rules

| Scope | Default |
|-------|---------|
| Per WI | ≤12 implementer calls, ≤90 min, ≤3 strategies |
| Per attempt | ≤1 failure-analyst, ≤1 evidence request |
| Per campaign | Wall-clock cap (configurable); stop if ≥3 consecutive WIs `blocked` |
| Kill switch | `workspace/STOP` file |

---

## 6. Gates Specification

| Gate | Name | Tool | Pass Condition | Exists |
|------|------|------|----------------|--------|
| G0 | Integrity | structural_checks.py | No forbidden constructs | ✅ (P0-S03) |
| G1 | Compile | iverilog | returncode=0 | ✅ |
| G2 | Verilator Lint | verilator | No errors (warnings OK) | ✅ |
| G3 | Yosys Synthesis | yosys | No latches, no multi-drivers, no width mismatch | ✅ |
| G4 | Simulation | vvp + BFM | `success=true` + parsed RESULT | ✅ |
| G5 | Protocol | judge.py | `pass=true` | ✅ |
| G6 | Timing | timing_monitor | All setup/hold met per `i2c_timing.yaml` | ❌ (NEW) |
| G7 | Coverage | coverage_map | All requirements mapped to passing checks | ❌ (NEW) |
| G8 | Hold-out | holdout_suite | Unseen scenarios pass | ❌ (NEW) |
| G9 | Synthesizability | yosys (full) | Netlist generated | ✅ (partial) |

---

## 7. Error Handling Table

| Event | Detection | Handler |
|-------|-----------|---------|
| `agent_schema_violation` | Record fails validation | 1 retry with validator msg; 2nd → `agent_protocol_violation`, consume call |
| `agent_timeout` | No completion in wall-clock cap | Abort Ollama, log, count call, retry with lower `num_predict`, then escalate |
| `context_overflow` | Pack > budget after drops | Conductor bug: stop campaign, write `FATAL_context_overflow.md` |
| `generation_error` | Ollama unreachable / empty | Exp backoff 3×; then pause `OLLAMA_DOWN` (not model failure) |
| `tool_missing` | iverilog/vvp/verilator/yosys absent | Pause campaign (not model failure) |
| `duplicate_candidate` | Fingerprint seen in WI | Log, consume attempt, prompt "you reproduced attempt A-n" |
| `integrity_violation` | G0 forbidden pattern match | Candidate fails G0, log event; ≥2/campaign → pause for HUMAN |
| `gate_nondeterminism` | Re-run gives different verdict | Quarantine WI, hard stop for HUMAN |
| `crash` | Process died | Resume from `state.json` |
| `ORACLE_TAMPERED` | Hash mismatch | Hard stop |

---

## 8. Oracle Integrity

### 8.1 Read-Only Enforcement

```python
# Before every gate run:
assert oracle_manifest_hash() == EXPECTED_HASH, "ORACLE_TAMPERED"
```

### 8.2 Manifest

```
oracle/
  testbench_i2c.v
  timing/i2c_timing.yaml
  coverage/
  mutants/
  goldens/
  MANIFEST.sha256
```

### 8.3 Promotion Protocol

1. Proposal in `proposals/oracle/` with rationale + evidence
2. Oracle Promotion Protocol review
3. HUMAN signature required
4. New `MANIFEST.sha256` committed
5. Agents re-validated against new oracle

---

## 9. Blackboard Structure

```
workspace/campaigns/<campaign_id>/
  campaign.json           # Config snapshot: models.lock, oracle_version, budgets, git commit
  events.jsonl            # Every state transition, append-only, hash-chained
  wi/<WI-id>/
    requirements.json     # Latest accepted spec_analyst output
    plan.md               # Latest accepted architect plan
    ledger.jsonl          # Attempt ledger (one line per attempt)
    iter_<NNNN>/
      context/
        <role>_<call>.txt    # Exact prompt sent (Context Pack)
      records/
        R-....json             # Structured Reasoning Records
      raw/
        <role>_<call>.json     # Raw model output (incl. </think>) — NEVER fed to agents
      evidence/
        E-....json             # Tool-produced facts + artifact files
      candidates/
        A-....v                  # Candidate artifacts
        A-....json               # Gate-by-gate verdicts
      gates/
        A-....json               # Gate verdicts
  memory/
    lessons.jsonl
  verified/
    <WI-id>/<sha16>.{v,json}  # Promoted artifacts + verdict sidecar
  blocked/
    <WI-id>.md                   # Why parked
```

### Hash Chaining

```
event_n = hash(event_{n-1} || event_n_data)
```

---

## 10. Implementation Sequence

| Phase | Tasks | Dependencies | Deliverable |
|-------|-------|--------------|-------------|
| **P0** | Oracle hardening (G0 rules, nonce) | — | Oracle v2.0 |
| **P1** | Model bake-off, scheduling | P0 | `config/models.yaml`, roster |
| **P2** | Blackboard, Records, Evidence, Lessons | P1 | `core/blackboard.py`, `core/records.py`, `core/evidence.py`, `core/lessons.py` |
| **P2** | Reasoning Record schema + validation | P1 | `core/schemas.py` additions |
| **P3** | Conductor state machine | P2 | `core/conductor.py` |
| **P4** | Context Pack builder + token budget | P2 | `core/context_pack.py` |
| **P5** | Agent implementations (7 roles) | P3, P4 | `agents/*.py` |
| **P6** | Budget manager + kill switch | P3 | `core/budget.py` |
| **P7** | Campaign manager + backlog | P3 | `core/campaign.py` |
| **P8** | Oracle Promotion Protocol + manifest | P0 | `oracle_promotion.py`, `oracle/MANIFEST.sha256` |
| **P9** | Integration tests + smoke benchmark | P3-P8 | `tests/test_phase1_*.py` |
| **P10** | Documentation + examples | P9 | `docs/PHASE1_*.md` |

---

## 10. Acceptance Criteria for Phase 1

| Criterion | Verification |
|-----------|--------------|
| Conductor runs S0→S8 for a trivial WI (1-byte) | `pytest tests/test_phase1_conductor.py` |
| All 7 agents produce valid Records | `pytest tests/test_agents.py` |
| Blackboard hash-chain verifies | `pytest tests/test_blackboard.py` |
| Evidence citations enforced | `pytest tests/test_record_validation.py` |
| Lessons memory stores/retrieves | `pytest tests/test_lessons.py` |
| Oracle promotion protocol works | `pytest tests/test_oracle_promotion.py` |
| Campaign runs 3 WIs end-to-end | `pytest tests/test_campaign.py` |
| Hold-out scenarios catch regressions | `pytest tests/test_holdout.py` |
| Full campaign: 12 WIs, ≤12h | Manual verification |
| All v1 tests still pass | `pytest tests -q` |

---

*End of Phase 1 Implementation Specification*