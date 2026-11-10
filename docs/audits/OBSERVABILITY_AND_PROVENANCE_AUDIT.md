# Observability and Provenance Audit

**Date:** 2026-11-11  
**Commit:** dca7c78 (main)  
**Auditor:** Autonomous Engineering Agent

---

## 1. Provenance Chain

### 1.1 Current Provenance Chain

```
Specification (spec.md)
    │
    ▼
Parsed Spec (tools/i2c_spec.py)
    │
    ▼
Retrieved Examples (tools/rtl_memory.py)
    │
    ▼
Model Prompt (agent/rtl_generator.py)
    │
    ▼
Raw Model Output (ollama_client)
    │
    ▼
Cleaned RTL (rtl_cleaner)
    │
    ▼
Normalized Fragment (normalize_model_rtl)
    │
    ▼
Deterministic Wrapper (assembler)
    │
    ▼
top_glue.v (CandidatePatch)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    VERIFICATION PIPELINE                     │
├─────────────────────────────────────────────────────────────┤
│ Structural → Compile → Hardware → Simulation → Judge        │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Repair Loop (max 2)
    │
    ▼
Final Candidate → PASS/FAIL
    │
    ▼
Artifacts: run.json, attempt_*.json, attempt_*.v, trace.json
```

---

## 2. Artifact Provenance

### 2.1 Artifacts Per Run

| Artifact | Location | Content | Hash |
|----------|----------|---------|------|
| `run.json` | `results/runs/<run_id>/` | Run header (git, config, model, seed) | - |
| `attempt_<n>.json` | `results/runs/<run_id>/` | Prompt, raw output, cleaned code, stages, metadata | SHA-256 in `code_sha256` |
| `attempt_<n>.v` | `results/runs/<run_id>/` | Final Verilog source | SHA-256 in `code_sha256` |
| `attempt_<n>_prompt.txt` | `results/runs/<run_id>/` | Full prompt sent to model | - |
| `attempt_<n>_compile_stderr.txt` | `results/runs/<run_id>/` | Compile stderr | - |
| `attempt_<n>_sim_stdout.txt` | `results/runs/<run_id>/` | Simulation stdout | - |
| `trace.json` | `results/traces/<run_id>.json` | Event timeline + decision records | - |

### 2.2 Index Files

| File | Purpose | Format |
|------|---------|--------|
| `results/runs.jsonl` | One-line index per run | JSONL |
| `results/bench/runs.csv` | Benchmark CSV | CSV |
| `results/bench/runs.jsonl` | Benchmark index | JSONL |
| `results/bench/summary.json` | Aggregated stats | JSON |
| `results/bench/report.md` | Human-readable report | Markdown |

---

## 3. Traceability Matrix

| Requirement | Spec | Parsed Spec | Prompt | Model Output | Cleaned | Verified |
|-------------|------|-------------|--------|--------------|---------|----------|
| I2C-ADDR | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| I2C-DATA | ✅ | ✅ | ✅ | ⚠️ | ✅ | ❌ (multi-byte) |
| I2C-START | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| I2C-STOP | ✅ | ✅ | ✅ | ⚠️ | ✅ | ❌ (multi-byte) |
| I2C-ACK | ✅ | ✅ | ✅ | ⚠️ | ✅ | ❌ (multi-byte) |
| I2C-OPEN-DRAIN | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Legend:** ✅ = traced, ⚠️ = partial, ❌ = broken

---

## 4. Artifact Identity Strategy

### 4.1 Current Strategy

| Artifact | Identity Mechanism |
|----------|-------------------|
| Candidate RTL | SHA-256 of full Verilog (`code_sha256`) |
| Attempt | `run_id` + `attempt_n` |
| Run | `run_id` = timestamp + uuid8 |
| Trace | `run_id` |
| Retrieval example | SHA-256 of code (first 16 chars) |
| Verified example | SHA-256 (validated on load) |
| Failed example | SHA-256 + failure metadata |

### 4.2 Strengths

1. **Content-addressable** - SHA-256 ensures content integrity
2. **Deterministic run IDs** - Timestamp + UUID prevents collisions
3. **Content verification** - Verified examples validated on load
6. **Tamper detection** - Replay compares stored vs recomputed verdicts

### 4.3 Weaknesses

| Weakness | Impact | Fix |
|----------|--------|-----|
| No content-addressable run IDs | Cannot detect run ID collision | Use content hash in run_id |
| No cross-run deduplication | Duplicate candidates not detected | Global content index |
| No artifact signing | Cannot verify authorship | Optional GPG signing |
| No artifact versioning | Schema evolution not tracked | Schema version in metadata |
| No global artifact index | Slow lookup across runs | Centralized index DB |

---

## 5. Decision Provenance

### 5.1 Current State

| Decision Point | Recorded? | Where |
|----------------|-----------|-------|
| Model selection | ✅ | `run.json.model` |
| Temperature/seed | ✅ | `run.json` |
| Retrieval enabled | ✅ | `run.json.retrieval_enabled` |
| Patcher enabled | ✅ | `run.json.config.patcher` |
| Prompt sent | ✅ | `attempt_*.json.prompt` |
| Raw model output | ✅ | `attempt_*.json.raw_output` |
| Retrieved examples | ✅ | `attempt_*.json.retrieval` |
| Gate verdicts | ✅ | `attempt_*.json.stages` |
| Repair decision | ✅ | `attempt_*.json.outcome=repaired` |
| Repair prompt | ✅ | `attempt_*.json.prompt` (repair) |
| Final verdict | ✅ | `run.json.final` |

### 5.2 Missing Decision Provenance

| Missing | Impact | Fix |
|---------|--------|-----|
| Why specific repair chosen | Cannot audit repair logic | Add `repair_rationale` to attempt |
| Why candidate rejected | Cannot audit selection | Add `rejection_reason` to attempt |
| Retrieval ranking rationale | Cannot audit retrieval | Add `retrieval_scores` to metadata |
| Model temperature per attempt | Partial | Record per-attempt temperature |

---

## 6. Observability Gaps

### 6.1 Dashboard (`ui/dashboard.py`)

| Feature | Status | Gap |
|---------|--------|-----|
| Timeline rendering | ✅ | - |
| Verification summary | ✅ | - |
| Attempt details | ❌ | Stub returns error |
| Prompt display | ❌ | Not implemented |
| Retrieved example | ❌ | Not implemented |
| Raw output display | ❌ | Not implemented |
| Verdict breakdown | ❌ | Not implemented |

### 6.2 MCP Server (`mcp_server/server.py`)

| Tool | Implemented | Tested |
|------|-------------|--------|
| `compile_rtl` | ✅ | ❌ |
| `lint_rtl` | ✅ | ❌ |
| `simulate_and_judge` | ✅ | ❌ |
| `run_pipeline` | ✅ | ❌ |
| `get_run` | ✅ | ❌ |

### 6.3 Replay (`tools/replay.py`)

| Feature | Status |
|---------|--------|
| Structural gate replay | ✅ |
| Compile replay | ✅ |
| Hardware checks | ✅ |
| Simulation replay | ✅ |
| Judge replay | ✅ |
| Nonce support | ✅ |
| Tamper detection | ✅ |
| Attempt selection | ✅ |

---

## 7. Provenance Gaps and Fixes

### 7.1 Critical Gaps

| Gap | Severity | Fix |
|-----|----------|-----|
| No content-addressable run IDs | HIGH | Include content hash in `run_id` |
| No global artifact index | HIGH | Add SQLite/JSON index |
| No artifact signing | MEDIUM | Optional GPG signing |
| No artifact versioning | MEDIUM | Add schema version to metadata |
| `render_attempt_details` stub | MEDIUM | Implement full lookup |
| MCP `get_run` untested | MEDIUM | Add integration test |

### 7.2 Recommended Artifact Identity Strategy

```python
# Proposed run_id format:
run_id = f"{timestamp}-{content_hash[:8]}-{uuid8}"
# Example: 20261111T120000-a1b2c3d4-e5f6g7h8

# Global artifact index (SQLite or JSONL):
{
  "artifact_id": "sha256:...",
  "run_id": "...",
  "attempt": 0,
  "type": "candidate|attempt_record|trace|prompt",
  "sha256": "...",
  "size_bytes": 12345,
  "created_at": 1234567890.123,
  "git_commit": "abc123..."
}
```

---

## 8. Tamper Detection

### 8.1 Current Mechanisms

| Mechanism | Scope | Effectiveness |
|-----------|-------|---------------|
| SHA-256 in attempt record | Per-attempt | HIGH |
| Verified example validation on load | Per-example | HIGH |
| Replay verdict comparison | Per-replay | HIGH |
| Nonce-authenticated RESULT | Per-simulation | HIGH |
| Attempt record SHA-256 | Per-attempt | HIGH |

### 8.2 Tampering Scenarios Not Detected

| Scenario | Detected? | Fix |
|----------|-----------|-----|
| Modified `run.json` | ❌ | Content hash in run_id |
| Swapped `attempt_*.json` | ❌ | Content hash in filename |
| Modified `trace.json` | ❌ | Content hash in run_id |
| Deleted `attempt_*.v` | ❌ | Index cross-reference |

---

## 9. Recommended Improvements

| Priority | Improvement | Effort |
|----------|-------------|--------|
| Content-addressable run IDs | HIGH | 4 hrs |
| Global artifact index (SQLite) | HIGH | 8 hrs |
| Implement `render_attempt_details` | HIGH | 4 hrs |
| MCP `get_run` integration test | MEDIUM | 2 hrs |
| Artifact signing (optional) | MEDIUM | 4 hrs |
| Schema version in metadata | LOW | 2 hrs |
| Cross-run deduplication index | LOW | 4 hrs |

---

## 10. Summary

| Aspect | Score | Notes |
|--------|-------|-------|
| Artifact persistence | 95% | Excellent per-run isolation |
| Content integrity | 90% | SHA-256 everywhere |
| Decision provenance | 80% | Missing repair rationale |
| Tamper detection | 85% | Good, but run-level gaps |
| Replay fidelity | 95% | Excellent |
| Dashboard observability | 60% | Attempt details missing |
| MCP observability | 40% | Tools exist, untested |
| Global traceability | 50% | No global index |

---

*End of Observability and Provenance Audit*