# Baseline Architecture Audit

**Date:** 2026-11-11  
**Commit:** dca7c78 (main)  
**Auditor:** Autonomous Engineering Agent

---

## 1. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PROTOFORGE ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐     │
│  │   agent_core     │    │   domains/i2c    │    │   agent/rtl_     │     │
│  │   agent.py       │───▶│   adapter.py     │───▶│   generator.py   │     │
│  │   config.py      │    │                  │    │                  │     │
│  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘     │
│           │                       │                       │               │
│           ▼                       ▼                       ▼               │
│  ┌──────────────────────────────────────────────────────────────────┐    │
  │                    core/orchestrator.py                          │    │
  │  execute_task() → generate → structural → compile → hw → sim → judge → repair │
  └──────────────────────────────────────────────────────────────────┘    │
           │                       │                       │               │
           ▼                       ▼                       ▼               │
  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐     │
  │ tools/           │    │ tools/           │    │ tools/           │     │
  │ structural_      │    │ compile_tool     │    │ simulation_      │     │
  │ checks.py        │    │ .py              │    │ tool.py          │     │
  └────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘     │
           │                       │                       │               │
           ▼                       ▼                       ▼               │
  ┌──────────────────────────────────────────────────────────────────┐    │
  │                    tools/judge.py                                │    │
  └──────────────────────────────────────────────────────────────────┘    │
           │                                                           │
           ▼                                                           │
  ┌──────────────────────────────────────────────────────────────────┐    │
  │                    agent_core/agent.py (run_once)                │    │
  │  - run_once() → Orchestrator → run_dir → run.json + attempt_*.json │    │
  └──────────────────────────────────────────────────────────────────┘    │
           │                                                           │
           ▼                                                           │
  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐     │
  │ tools/replay.py  │    │ ui/dashboard.py  │    │ mcp_server/      │     │
  │ (replay gates)   │    │ (timeline view)  │    │ server.py (MCP)  │     │
  └──────────────────┘    └──────────────────┘    └──────────────────┘     │
           │                       │                       │               │
           ▼                       ▼                       ▼               │
  ┌──────────────────────────────────────────────────────────────────┐    │
  │                    bench/run.py (benchmark runner)               │    │
  │  - bench/bon.py (Best-of-N runner)                               │    │
  │  - tools/rtl_memory.py (retrieval memory)                        │    │
  └──────────────────────────────────────────────────────────────────┘    │
           │                                                           │
           ▼                                                           │
  ┌──────────────────────────────────────────────────────────────────┐    │
  │                    tools/ (verification oracle)                   │    │
  │  - tools/simulation_tool.py (nonce-based parsing)                │    │
  │  - tools/judge.py (protocol checks)                              │    │
  │  - tools/structural_checks.py (G0 integrity)                     │    │
  │  - tools/compile_tool.py (iverilog)                              │    │
  │  - tools/hardware_checks.py (Verilator + Yosys)                  │    │
  │  - protocols/i2c/testbench_i2c.v (BFM + nonce)                   │    │
  └──────────────────────────────────────────────────────────────────┘    │
                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Live Execution Flow

```
run_once() [agent_core/agent.py]
    │
    ├─▶ Config.from_env() → RunPaths.create()
    │       │
    ├─▶ I2CDomainAdapter.build_context()
    │       │
    ├─▶ Task creation
    │       │
    ├─▶ RunTrace creation + event_sink
    │       │
    ├─▶ Orchestrator.execute_task()
    │       │
    │   ├─▶ domain.generate_candidate() → generate_multi_device_glue_rtl()
    │   │       ├─▶ build_device_instance_blocks()
    │   │       ├─▶ retrieve_rtl_examples() → rtl_memory
    │   │       ├─▶ _invoke_model() → ollama_client.generate_verilog()
    │   │       ├─▶ clean_rtl() / normalize_model_rtl()
    │   │       └─▶ _assemble_multi_fragment()
    │       │
    │   ├─▶ Structural gate (structural_checks)
    │   ├─▶ Compile (iverilog)
    │   ├─▶ Hardware checks (Verilator + Yosys)
    │   ├─▶ Simulation (vvp + nonce)
    │   ├─▶ Judge (protocol checks)
    │   └─▶ Repair loop (max_repairs times)
    │
    └─▶ Write run.json, attempt_*.json, trace.json
```

---

## 3. Component Dependency Map

```
┌─────────────────────┐
│   agent_core/agent  │◀─── run_once() entry point
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  core/orchestrator  │◀─── Orchestrator.execute_task()
└─────────┬───────────┘
          │
    ┌─────┼─────┬────────────┬────────────┬────────────┐
    ▼     ▼     ▼            ▼            ▼            ▼
┌──────┐┌────┐┌────────┐┌──────────┐┌─────────┐┌────────┐
│Struct│Compile│Hardware │ Simulation │  Judge  │ Repair │
└──┬───┘└──┬──┘└────┬───┘└────┬────┘└────┬────┘└────┬───┘
   │    │    │     │          │         │         │
   ▼    ▼    ▼     ▼          ▼         ▼         ▼
tools/  tools/ tools/   tools/    tools/   agent/    tools/
struct compile hardware simulation  judge    rtl_    rtl_
checks _tool  _checks  _tool      .py     generator memory
.py    .py   .py     .py        .py       .py     .py
```

---

## 4. Component Analysis

### 4.1 Core Orchestrator (`core/orchestrator.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Coordinate the 5-gate pipeline with bounded repair |
| **Actual Implementation** | ✅ Complete - 5-gate pipeline with bounded repair (max 2 by default) |
| **Connected to Live Path** | ✅ Yes - called by `agent_core.agent.run_once()` |
| **Dependencies** | `core.attempt_record`, `core.schemas`, `core.trace_schema`, `domains.base` |
| **Tests** | `tests/test_domain_neutral_core.py`, `tests/test_orchestrator*.py` |
| **Persistence** | Writes `run.json`, `attempt_*.json`, `attempt_*.v`, `attempt_*_prompt.txt` |
| **Error Handling** | Distinct outcomes: GENERATION_ERROR, STRUCTURAL_FAILED, COMPILE_FAILED, HARDWARE_FAILED, SIM_CHECKS_FAILED, TOOL_MISSING, GENERATION_ERROR |
| **Limitations** | Single-threaded, no parallel candidate evaluation |

### 4.2 Domain Adapter (`domains/i2c/adapter.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | I²C protocol domain adapter - parse spec, generate/repair, validate, judge |
| **Actual Implementation** | ✅ Complete - parses I2C spec, generates candidates, runs 5-gate pipeline |
| **Connected to Live Path** | ✅ Yes - registered in `domains/registry.py` |
| **Dependencies** | `agent.rtl_generator`, `tools.*`, `core.schemas` |
| **Tests** | `tests/test_domain_neutral_core.py`, `tests/test_domain_neutral_core.py` |
| **Limitations** | Single I²C protocol, no multi-protocol support |

### 4.3 RTL Generator (`agent/rtl_generator.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Generate RTL fragment, normalize, wrap in deterministic scaffold |
| **Actual Implementation** | ✅ Complete - prompt construction, model invocation, cleaning, normalization, wrapping |
| **Connected to Live Path** | ✅ Yes - called by `I2CDomainAdapter.generate_candidate()` |
| **Dependencies** | `agent.ollama_client`, `agent.rtl_cleaner`, `tools.rtl_memory`, `tools.verilog_parser` |
| **Tests** | `tests/test_fragment_scaffold.py`, `tests/test_ir_generator.py` |
| **Limitations** | Prompt is one-byte oriented; multi-byte payloads not well handled |

### 4.4 Ollama Client (`agent/ollama_client.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Thin wrapper around Ollama API with metadata capture |
| **Actual Implementation** | ✅ Complete - generates Verilog and JSON with metadata capture |
| **Connected to Live Path** | ✅ Yes - called by `agent.rtl_generator._invoke_model()` |
| **Dependencies** | `ollama` Python package, `urllib` |
| **Tests** | `tests/test_unit_checks.py::test_ollama_client_uses_stable_options_without_real_ollama` |
| **Limitations** | No retry logic for transient failures; no streaming support |

### 4.5 Benchmark Runner (`bench/run.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Run benchmark cases across configurations, produce CSV/JSONL/MD reports |
| **Actual Implementation** | ✅ Complete - 4 configs × 12 cases × N samples |
| **Connected to Live Path** | ✅ Yes - `bench.run` CLI entry point |
| **Dependencies** | `agent_core.agent.run_once`, `bench.run` internals |
| **Tests** | `tests/test_bench.py`, `tests/test_bench_artifacts.py` |
| **Features** | Resume support (`--resume`), IR path (`--ir`), frozen retrieval (`--learn`) |
| **Limitations** | Sequential execution only; no parallel candidate evaluation |

### 4.6 Best-of-N Runner (`bench/bon.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Run N candidates per case, first pass wins |
| **Actual Implementation** | ✅ Implemented - runs N candidates sequentially, stops at first pass |
| **Connected to Live Path** | ✅ Yes - `bench.bon` CLI entry point |
| **Dependencies** | `bench.run` internals, `agent_core.agent.run_once` |
| **Tests** | None yet (new implementation) |
| **Limitations** | Sequential only; no parallel candidate evaluation; no candidate diversity metrics |

### 4.7 Retrieval Memory (`tools/rtl_memory.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Store/retrieve verified RTL examples for few-shot prompting |
| **Actual Implementation** | ✅ Complete - lexical retrieval, seed + learned examples |
| **Connected to Live Path** | ✅ Yes - called by `agent.rtl_generator.generate_multi_device_glue_rtl()` |
| **Dependencies** | `hashlib`, `json`, `re`, `collections.Counter` |
| **Tests** | `tests/test_unit_checks.py::test_memory_only_retrieves_verified_passing_examples` |
| **Limitations** | Lexical only (no embeddings); single seed reference; no failure-specific retrieval |

### 4.7 Verification Oracle (`tools/judge.py`, `tools/simulation_tool.py`, `protocols/i2c/testbench_i2c.v`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Protocol verification via BFM testbench + nonce-authenticated RESULT parsing |
| **Actual Implementation** | ✅ Complete - nonce-authenticated RESULT parsing, 11 protocol checks |
| **Connected to Live Path** | ✅ Yes - testbench instantiated with DUT, nonce-authenticated parsing |
| **Dependencies** | `re`, `subprocess`, `urllib` |
| **Tests** | `tests/test_oracle_mutation.py` (10 mutants), `tests/test_oracle_mutation.py` |
| **Limitations** | Single protocol (I²C v1), no timing checks beyond `sda_stable`, no multi-master |

### 4.8 Event Store & Dashboard (`core/event_store.py`, `ui/dashboard.py`)

| Aspect | Status |
|--------|--------|
| **Intended Responsibility** | Persist/query traces, render timeline |
| **Actual Implementation** | ✅ Partial - stores traces, renders timeline, but `render_attempt_details` not implemented |
| **Connected to Live Path** | ✅ Partially - traces saved by `agent_core.agent.run_once()` |
| **Dependencies** | `core.trace_schema`, `core.trace_serializer` |
| **Tests** | `tests/test_logging_replay.py` |
| **Limitations** | `render_attempt_details` not implemented; no web UI |

---

## 5. Implemented vs Disconnected Capabilities

| Capability | Implemented | Connected | Tested | Notes |
|------------|-------------|-----------|--------|-------|
| Core pipeline (5 gates) | ✅ | ✅ | ✅ | |
| Bounded repair (max 2) | ✅ | ✅ | ✅ | |
| Structural gate (G0) | ✅ | ✅ | ✅ | 19 checks |
| Icarus compilation | ✅ | ✅ | ✅ | |
| Verilator lint | ✅ | ✅ | ✅ | Warning count only |
| Yosys synthesis | ✅ | ✅ | ✅ | Basic checks only |
| Simulation (vvp) | ✅ | ✅ | ✅ | Nonce-authenticated |
| Protocol judge | ✅ | ✅ | ✅ | 11 checks |
| Bounded repair | ✅ | ✅ | ✅ | Max 2 by default |
| Retrieval memory | ✅ | ✅ | ✅ | Lexical only |
| Per-run artifacts | ✅ | ✅ | ✅ | run.json, attempt_*.json, .v |
| Replay tool | ✅ | ✅ | ✅ | Re-runs gates |
| Event store | ✅ | ✅ | ✅ | JSON files |
| Dashboard timeline | ✅ | Partial | ✅ | `render_attempt_details` TODO |
| MCP server | ✅ | Partial | ❌ | Tools registered, not tested |
| Benchmark runner | ✅ | ✅ | ✅ | 4 configs, 12 cases |
| Best-of-N runner | ✅ | ✅ | ❌ | No tests yet |
| IR path (FSM JSON) | ✅ | ✅ | ✅ | 0/24 pass@1 |
| Template baseline | ✅ | ✅ | ✅ | 240/240 pass |
| IR benchmark | ✅ | ✅ | ✅ | 0/24 pass@1 |
| Best-of-N runner | ✅ | ✅ | ❌ | New, no tests |

---

## 6. Technical Debt

| Area | Debt | Impact |
|------|------|--------|
| Multi-byte generation | Model fails on 2+ byte cases | 8/24 cases fail (all multi-byte) |
| Prompt scaffolding | One-byte oriented | Multi-byte prompts incomplete |
| Retrieval | Lexical only, no embeddings | Low recall for novel specs |
| Best-of-N | No tests, sequential only | Cannot validate claimed 41.7% pass@1 |
| Dashboard | `render_attempt_details` TODO | No attempt-level detail view |
| MCP server | Tools registered but untested | No integration tests |
| Repair prompts | String-based, fragile | Brittle, no structured feedback |
| Benchmark runner | Sequential only | Slow (hours for full runs) |
| Retrieval memory | Lexical only, no embeddings | Low recall for novel specs |
| MCP server | Tools registered, untested | No integration tests |
| Dashboard | `render_attempt_details` stub | No attempt detail view |
| Best-of-N | No tests | Cannot validate claims |
| IR path | 0/24 pass | Not competitive |

---

## 7. Reliability Concerns

| Concern | Severity | Evidence |
|---------|----------|----------|
| Pyflakes missing in CI | HIGH | Fixed in dca7c78 |
| No pyflakes in requirements-dev.txt | HIGH | Fixed in ce7269d |
| Ollama service reachability | MEDIUM | `check_ollama()` has 3s timeout |
| Ollama model digest caching | LOW | Cached per process |
| Ollama service reachability | MEDIUM | 3s timeout may be too short |
| No Ollama in CI | HIGH | Tests skip Ollama-dependent tests |
| No parallel execution | MEDIUM | Sequential only, slow benchmarks |
| No candidate diversity metrics | LOW | Best-of-N cannot measure diversity |
| No candidate deduplication | MEDIUM | Best-of-N may generate duplicates |
| No candidate timeout handling | MEDIUM | Ollama timeout not handled |
| Ollama service down handling | MEDIUM | Batch stops on Ollama down |
| Template benchmark not in CI | MEDIUM | Only 3-case smoke test |

---

## 8. Suggested Improvements

| Priority | Improvement | Effort |
|----------|-------------|--------|
| HIGH | Add pyflakes to requirements-dev.txt ✅ DONE | 15 min |
| HIGH | Add multi-byte prompt scaffolding | 2-4 hours |
| HIGH | Add Best-of-N tests | 2-4 hours |
| HIGH | Add retrieval embeddings (sentence-transformers) | 4-8 hours |
| MEDIUM | Implement dashboard `render_attempt_details` | 2-4 hours |
| MEDIUM | Add Best-of-N candidate deduplication | 2-4 hours |
| MEDIUM | Add candidate timeout handling | 2-4 hours |
| MEDIUM | Add parallel candidate evaluation (process pool) | 4-8 hours |
| MEDIUM | Add MCP integration tests | 2-4 hours |
| LOW | Implement dashboard `render_attempt_details` | 2-4 hours |
| LOW | Add candidate deduplication to Best-of-N | 2-4 hours |
| LOW | Add candidate timeout handling | 2-4 hours |

---

## 9. Component Reliability Scores

| Component | Reliability | Notes |
|-----------|-------------|-------|
| Core orchestrator | HIGH | Well-tested, clear error handling |
| Domain adapter | HIGH | Well-tested, clear contracts |
| RTL generator | MEDIUM | Multi-byte weakness |
| Ollama client | MEDIUM | No retry logic |
| Benchmark runner | HIGH | Well-tested, resume support |
| Best-of-N runner | LOW | No tests, new code |
| Retrieval memory | MEDIUM | Lexical only |
| Verification oracle | HIGH | Mutation-tested |
| Event store | HIGH | Well-tested |
| Dashboard | MEDIUM | Partial implementation |
| MCP server | LOW | Untested |
| Benchmark runner | HIGH | Resume support |
| Best-of-N runner | LOW | New, untested |

---

## 10. Summary

The ProtoForge codebase is **well-structured and largely reliable** for its intended single-agent I²C RTL generation pipeline. The core 5-gate pipeline (structural → compile → hardware → simulation → judge) with bounded repair is **well-implemented and tested** (189 tests pass).

**Key strengths:**
- Clean architecture with clear separation of concerns
- Deterministic template baseline (240/240 pass)
- Mutation-tested oracle (10 mutants caught)
- Per-run artifact persistence with replay capability
- Deterministic template upper bound
- Resume-capable benchmark runner

**Critical gaps:**
1. **Multi-byte generation failure** - 8/24 cases fail (all multi-byte)
2. **Best-of-N untested** - New implementation, no tests, claimed 41.7% not verified
3. **Retrieval is lexical only** - No embeddings, low recall for novel specs
4. **Best-of-N untested** - No tests, claimed 41.7% not verified
5. **Dashboard incomplete** - `render_attempt_details` stub
5. **MCP server untested** - Tools registered but no integration tests

**Readiness for Phase 1 (multi-agent):** The codebase is **architecturally ready** for Phase 1 specification work, but the **multi-byte generation weakness must be addressed first** as it affects both single-agent and multi-agent paths.

---

*End of Baseline Architecture Audit*