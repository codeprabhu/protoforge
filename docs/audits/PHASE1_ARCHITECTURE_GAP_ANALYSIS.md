# Phase 1 Architecture Gap Analysis

**Date:** 2026-11-11  
**Commit:** dca7c78 (main)  
**Auditor:** Autonomous Engineering Agent

---

## 1. Current State vs Phase 1 Requirements

| Phase 1 Component | Current State | Gap | Effort |
|-------------------|---------------|-----|--------|
| Specification Analyst | ❌ Not implemented | New agent role | 40 hrs |
| Architect | ❌ Not implemented | New agent role | 40 hrs |
| Implementer | ⚠️ Partial (rtl_generator) | Refactor + schema | 20 hrs |
| Failure Analyst | ❌ Not implemented | New agent role | 30 hrs |
| Reviewer | ❌ Not implemented | New agent role | 30 hrs |
| Test Author | ❌ Not implemented | New agent role | 20 hrs |
| Archivist | ⚠️ Partial (rtl_memory) | Extend + schema | 15 hrs |
| Conductor | ⚠️ Partial (Orchestrator) | Refactor + state machine | 40 hrs |
| Blackboard | ⚠️ Partial (EventStore) | Extend + schema | 20 hrs |
| Evidence System | ❌ Not implemented | New types + builders | 20 hrs |
| Reasoning Records | ❌ Not implemented | New schema + validation | 20 hrs |
| Context Packs | ❌ Not implemented | Builder + token budget | 20 hrs |
| Lessons Memory | ⚠️ Partial (rtl_memory) | Extend + signatures | 15 hrs |
| Oracle Promotion | ❌ Not implemented | Protocol + human gate | 15 hrs |
| Campaign Manager | ❌ Not implemented | State machine + persistence | 20 hrs |

---

## 2. Reusable Infrastructure (High Confidence)

| Module | Current Role | Phase 1 Role | Changes Needed |
|--------|--------------|--------------|----------------|
| `core/orchestrator.py` | Orchestrator | Conductor (refactor) | State machine, budget mgmt |
| `core/attempt_record.py` | AttemptRecord | AttemptRecord (extend) | Add evidence IDs, decision refs |
| `core/event_store.py` | EventStore | Blackboard (extend) | Hash-chaining, evidence/records dirs |
| `core/trace_schema.py` | TraceSchema | EventSchema (extend) | Add evidence/record refs |
| `core/trace_serializer.py` | Serializer | Serializer (reuse) | Minor |
| `core/schemas.py` | Schemas | Schema base | Extend for records/evidence |
| `agent/rtl_generator.py` | Generator | Implementer (refactor) | Structured output schema |
| `agent/ollama_client.py` | Ollama client | Model client (shared) | Add role config |
| `tools/rtl_memory.py` | Retrieval | Lessons Memory (extend) | Signatures, status transitions |
| `tools/replay.py` | Replay tool | Replay tool (reuse) | Agent-level replay |
| `tools/structural_checks.py` | G0 gate | G0 gate (extend) | Add integrity rules per WI |
| `tools/judge.py` | Judge | Judge (reuse) | Evidence-based verdicts |
| `tools/simulation_tool.py` | Sim tool | Sim tool (reuse) | Nonce + evidence |
| `tools/compile_tool.py` | Compile tool | Compile tool (reuse) | Evidence output |
| `tools/hardware_checks.py` | HW checks | HW checks (reuse) | Evidence output |
| `tools/judge.py` | Judge | Judge (reuse) | Evidence-based |
| `tools/replay.py` | Replay tool | Replay tool (reuse) | Agent-level replay |
| `bench/run.py` | Bench runner | Bench runner (reuse) | Campaign integration |
| `bench/bon.py` | Bon runner | Sampling strategy | Evidence per candidate |

---

## 3. Missing Infrastructure (Must Build)

| Component | Description | Dependencies | Effort |
|-----------|-------------|--------------|--------|
| `core/evidence.py` | Evidence types + builders | schemas | 20 hrs |
| `core/records.py` | ReasoningRecord, DecisionRecord | schemas, evidence | 20 hrs |
| `core/blackboard.py` | Blackboard with hash-chaining | event_store, evidence, records | 20 hrs |
| `core/state_machine.py` | Conductor state machine | state_machine | 40 hrs |
| `core/context_pack.py` | ContextPack builder | blackboard, schemas | 20 hrs |
| `core/budget.py` | Budget enforcement | config | 10 hrs |
| `agents/spec_analyst.py` | Spec Analyst agent | llm, records | 20 hrs |
| `agents/architect.py` | Architect agent | llm, records | 20 hrs |
| `agents/implementer.py` | Implementer agent | llm, records | 20 hrs |
| `agents/failure_analyst.py` | Failure Analyst agent | llm, records | 20 hrs |
| `agents/reviewer.py` | Reviewer agent | llm, records | 20 hrs |
| `agents/test_author.py` | Test Author agent | llm, records | 15 hrs |
| `agents/archivist.py` | Archivist agent | lessons, records | 15 hrs |
| `core/context_pack.py` | ContextPack builder | blackboard, schemas | 20 hrs |
| `core/lessons.py` | LessonsMemory (extend rtl_memory) | rtl_memory, records | 15 hrs |
| `core/oracle_promotion.py` | Oracle Promotion Protocol | oracle, records | 15 hrs |
| `core/campaign.py` | Campaign manager | state_machine | 20 hrs |
| `agents/__init__.py` | Agent registry | agents | 2 hrs |

---

## 4. Schema Extensions Required

### 4.1 New Schemas Needed

```python
# core/schemas.py additions

class Evidence(BaseModel):
    evidence_id: str  # E-<run_id>-<seq>
    kind: Literal["gate_result", "sim_event_timeline", "compiler_diag", 
                   "diff", "measurement", "waveform_slice", "coverage_map"]
    produced_by: Literal["conductor"]
    tool: ToolInfo
    attempt_id: str
    artifact_path: str
    sha256: str
    summary: str  # ≤ 600 chars
    created_at: datetime

class Record(BaseModel):
    record_id: str  # R-<wi>-<seq>
    schema: Literal["protoforge.record/1"]
    campaign_id: str
    wi: str
    iteration: int
    attempt_id: str
    role: AgentRole
    model: ModelInfo
    inputs: RecordInputs
    observations: List[Observation]
    hypotheses: List[Hypothesis]
    decision: Decision
    assumptions: List[str]
    open_questions: List[str]
    handoff: Handoff
    self_check: SelfCheck

class ContextPack(BaseModel):
    sections: List[ContextSection]
    token_budget: TokenBudget
    context_pack_sha256: str

class Lesson(BaseModel):
    lesson_id: str  # L-<seq>
    signature: str  # sha256(oracle_version | wi | stage | checks | diag)[:12]
    wi_scope: List[str]
    symptom: str
    cause: str
    fix_pattern: str
    evidence: List[str]
    status: Literal["candidate", "trusted", "retired"]
    oracle_version: str
    uses: LessonUsage
    created_by: str
```

---

## 4.2 Oracle Promotion Protocol

```
1. Agent proposes oracle change (test_author)
   → Creates proposal in proposals/oracle/
   → Includes: rationale, evidence IDs, test plan

2. Conductor validates
   → Runs mutation tests on proposal
   → Checks against oracle manifest

3. Human reviews
   → Reviews proposal + test results
   → Signs off (GPG or manual approval)

4. Conductor promotes
   → Updates oracle/MANIFEST.sha256
   → Archives old oracle version
   → Invalidates dependent lessons

5. Archivist updates lessons
   → Re-validates affected lessons
   → Updates status (retired/candidate)
```

---

## 5. Conductor State Machine

```
S0 SELECT
    │
    ▼
S1 SPECIFY (spec_analyst)
    │
    ▼
S2 PLAN (architect)
    │
    ▼
S3 DRAFT (implementer × N samples)
    │
    ▼
S4 GATES (Conductor + tools)
    │
    ├── all pass ──▶ S7 PASS-AUDIT (reviewer)
    │                   │
    │                   ▼
    │                   S8 PROMOTE (Conductor + archivist)
    │                   │
    │                   ▼
    │                   S0 SELECT (next WI)
    │
    └── fail ──▶ S5 DIAGNOSE (failure_analyst)
                    │
                    ▼
                    S6 REPAIR (implementer)
                    │
                    ▼
                    S4 GATES (retry)
                    │
                    ├── budget exhausted ──▶ S9 ESCALATE
                    │
                    ▼
                    S5 DIAGNOSE (new failure)
```

### Budget Defaults

| Scope | Default |
|-------|---------|
| Per WI | ≤ 12 implementer calls, ≤ 90 min, ≤ 3 strategies |
| Per attempt | ≤ 1 failure_analyst, ≤ 1 evidence request |
| Per campaign | Wall-clock cap (configurable) |
| Kill switch | `workspace/STOP` file |

---

## 5. Reusable vs New Code Map

| Current Module | Phase 1 Role | Reuse % | Changes |
|----------------|--------------|---------|---------|
| `core/orchestrator.py` | Conductor | 40% | State machine, budgets, evidence |
| `core/attempt_record.py` | AttemptRecord | 80% | Add evidence IDs, decision refs |
| `core/event_store.py` | EventStore | 60% | Hash-chaining, evidence dirs |
| `core/trace_schema.py` | TraceSchema | 70% | Add evidence/record refs |
| `agent/rtl_generator.py` | Implementer | 30% | Structured output, evidence |
| `agent/ollama_client.py` | Model client | 90% | Role config |
| `tools/rtl_memory.py` | LessonsMemory | 50% | Signatures, status transitions |
| `tools/replay.py` | Replay tool | 80% | Agent-level replay |
| `tools/structural_checks.py` | G0 gate | 90% | Per-WI contracts |
| `tools/judge.py` | Judge | 90% | Evidence-based verdicts |
| `tools/simulation_tool.py` | Sim tool | 95% | Nonce + evidence |
| `tools/compile_tool.py` | Compile tool | 95% | Evidence output |
| `tools/hardware_checks.py` | HW checks | 95% | Evidence output |
| `bench/run.py` | Bench runner | 80% | Campaign integration |
| `bench/bon.py` | Sampling strategy | 60% | Evidence per candidate |

---

## 6. New Code Required (Summary)

| Category | Files | Lines (est) |
|----------|-------|-------------|
| Core schemas | 3 | ~800 |
| Evidence system | 2 | ~500 |
| Records | 2 | ~600 |
| Blackboard | 2 | ~600 |
| State machine | 2 | ~800 |
| Context packs | 2 | ~400 |
| Budget manager | 1 | ~200 |
| Agents | 7 | ~2000 |
| Context pack builder | 1 | ~300 |
| Lessons memory | 1 | ~300 |
| Oracle promotion | 1 | ~300 |
| Campaign manager | 1 | ~500 |
| **Total** | **~23** | **~7000** |

---

## 5. Integration Points

| Phase 1 Component | Integrates With |
|-------------------|-----------------|
| Conductor | Orchestrator, EventStore, Blackboard |
| Spec Analyst | Blackboard, Lessons, Oracle |
| Architect | Blackboard, Lessons, Spec Analyst output |
| Implementer | Blackboard, Architect plan, Lessons |
| Failure Analyst | Blackboard, Evidence, Lessons, Implementer output |
| Reviewer | Blackboard, Requirements, Evidence, Candidate |
| Test Author | Blackboard, Coverage gaps, Oracle |
| Archivist | Blackboard, Lessons, Evidence |
| Lessons Memory | rtl_memory, Evidence, Records |
| Oracle Promotion | Proposals, Human, Manifest |

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| State machine complexity | HIGH | HIGH | Incremental implementation, heavy testing |
| Token budget overflow | MEDIUM | HIGH | Strict budgeting, truncation |
| Agent schema violations | MEDIUM | HIGH | Strict validation, retry once |
| Oracle promotion bottleneck | LOW | HIGH | Human-in-loop, async |
| Blackboard contention | LOW | MEDIUM | Single-writer, append-only |
| Evidence explosion | LOW | MEDIUM | TTL, summarization |

---

*End of Phase 1 Architecture Gap Analysis*