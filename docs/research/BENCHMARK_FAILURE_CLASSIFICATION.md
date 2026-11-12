# Benchmark Failure Classification

**Status:** Implemented in `bench/run.py` and `bench/bon.py`  
**Tests:** `tests/test_bench_classifications.py`  
**Applies to:** every `bench.run` and `bench.bon` batch

This document defines how ProtoForge classifies a run, how it decides whether a
batch is valid, and why an invalid experiment must never report a pass rate.

---

## 1. Why explicit classification exists

An earlier Best-of-4 batch recorded 48 runs with:
`generation_error: "The Ollama Python package is missing"`. Those runs were
initially presented as model failures and produced a meaningless 41.7% claim.
They were **infrastructure failures**: the model was never invoked. The same
mistake must be impossible to repeat silently.

Every row now carries an `explicit_classification` field. The aggregate
`summary.json` contains an `explicit_outcomes` map with all classes, including
zeros, so a failure can never be collapsed into an "other" bucket.

---

## 2. Classification vocabulary

| Classification | Meaning | Counts toward infrastructure? | Counts toward candidate verification? |
|----------------|---------|:-----------------------------:|:-------------------------------------:|
| `generation_error` | The model was not invoked or produced no candidate (Ollama missing, timeout, connection refused). | Yes | No |
| `runner_error` | The harness failed around the model: import error, server crash, missing external tool (`tool_missing`), uncaught runner exception. | Yes | No |
| `structural_failure` | A candidate was produced but the structural gate rejected it. | No | Yes |
| `compile_failure` | A candidate was produced but Icarus compilation failed. | No | Yes |
| `lint_failure` | The candidate compiled but Verilator lint or Yosys synthesis failed (the orchestrator's `hardware_failed` stage). | No | Yes |
| `simulation_failure` | The candidate compiled but `vvp` simulation failed. | No | Yes |
| `oracle_failure` | Simulation passed but the protocol judge rejected the behaviour. | No | Yes |
| `pass` | All gates passed, either on the first attempt or after bounded repair. | No | No |

`INFRASTRUCTURE_CLASSIFICATIONS = {generation_error, runner_error}`.

`CANDIDATE_FAILURE_CLASSIFICATIONS = {structural_failure, compile_failure,
lint_failure, simulation_failure, oracle_failure}`.

`pass` is a successful candidate run. It is never counted as a verification
failure. A regression test covers this because the first implementation counted
every non-infrastructure row as a failure, producing `24` failures for the
24-run baseline that contained 16 passes.

---

## 3. Internal status → classification mapping

| Orchestrator `final` | Explicit classification |
|----------------------|-------------------------|
| `generation_error` | `generation_error` |
| `runner_error` | `runner_error` |
| `tool_missing` | `runner_error` |
| `structural_failed` | `structural_failure` |
| `compile_failed`, `compile_error` | `compile_failure` |
| `hardware_failed`, `lint_error` | `lint_failure` |
| `sim_checks_failed` | `simulation_failure` |
| `judge_error`, `protocol_error` | `oracle_failure` |
| `pass` | `pass` |
| unknown / empty | `oracle_failure` (conservative default) |

The `tool_missing` mapping is deliberate: a missing compiler or simulator says
nothing about the candidate and must invalidate the batch rather than count as
an RTL failure.

---

## 4. Batch validity rules

`assess_batch_validity()` marks a batch **invalid** when:

1. **No runs completed** — there is no evidence at all.
2. **No model invocations recorded** — every run is an infrastructure failure.
   This is the rule that catches the archived 48-run Ollama batch.
3. **Infrastructure error rate exceeds the threshold** (default 50%). A batch
   where half or more of the attempted runs never reached the model cannot
   support a pass-rate claim.
4. **The intended model cannot be established as invoked** — a run with zero
   attempts is not evidence of model capability.

A batch at exactly the 50% threshold is valid by the current rule; the rule is
strictly `rate > threshold`.

`summary.json` records, per configuration:

- `batch_valid` and `batch_invalid_reason`;
- `model_invoked`;
- `infrastructure_failures` and `candidate_verification_failures`;
- `runs_without_candidate` and `candidate_runs`;
- `explicit_outcomes` (all classifications, including zeros);
- `successful_candidates`.

An invalid batch must not be quoted as a pass rate. The report table renders the
batch-valid column with ✅/❌ so the number is visibly contextualised.

### `pass` denominators

Both `pass@1` and `pass after repair` currently use the total number of rows in
the configuration. Infrastructure failures therefore lower the denominator's
quality; that is exactly why batch validity is reported alongside the rate.
Read an invalid batch's rate as "not measured", never as a capability result.

---

## 5. Harness-owned defects are never candidate failures

A deterministic scaffold defect that drops or duplicates harness-owned code
produces a gate failure for which the candidate is not responsible. Four
occurred while establishing the baseline and the multi-byte validation; all
were suppressed before the defect was charged to the model:

| Defect | Effect | Fix |
|--------|--------|-----|
| `HALF` treated as declared when only referenced in a `wire` statement | 24/24 first attempts failed to compile (`Unable to bind wire/reg/memory HALF`) | Require a real `localparam|parameter NAME =` |
| Model-supplied device instances and `assign sda/scl` drivers left in the fragment | 2 multi-byte runs failed the structural gate on duplicate drivers/instances | Strip harness-owned code before wrapping |
| Payload constants sliced away with the discarded module prefix | 3 multi-byte attempts failed to compile (`Unable to bind wire/reg/memory DATA_BYTE1`) although the model had declared them | Scaffold supplies/specializes `DATA_BYTE1..N` and `DATA_BYTE_0..N-1` from the spec; preserve other referenced constants |
| Retrieval tie always selected the 1-byte seed reference | Every multi-byte prompt was shown a single-byte design; candidates sent one data byte | Prefer the reference matching the payload length; state the byte/ACK sequence explicitly |
| Both 0-based and 1-based payload names injected at once | A correct 1-based `DATA_BYTE_1 = 8'h11` was rewritten to the *second* payload byte; 5 of 8 multi-byte runs transmitted the wrong bytes or failed to compile (`DATA_BYTE_2` undefined) | Choose one scheme per fragment: `DATA_BYTE_0` present means 0-based, otherwise 1-based |

Rule: **any deterministic defect must be removed before a run is counted, not
explained afterwards.** A pre-fix artifact may be retained as evidence of the
defect, but it must never be reported as a capability result, and the fix must
come with a test that replays the failing input. See
`docs/research/VALID_BASELINE_REPORT.md` §4.

## 6. Preflight gate

Before a model configuration starts, `preflight_check()` verifies:

1. the `ollama` Python package is importable;
2. the Ollama server responds to `client.list()`;
3. the requested model appears under the response field `model`
   (Ollama 0.6.3 returns `{"models": [{"model": ...}]}`, not `name`);
4. `iverilog`, `vvp`, `verilator` and `yosys` are on `PATH`.

`bench.run` and `bench.bon` return exit code `2` and print `PREFLIGHT FAILED`
before writing any benchmark row when the check fails. Template-only batches
skip the model gate but still require the RTL tools.

Example early failure (before the model-key fix):

```
PREFLIGHT FAILED: Model 'qwen2.5-coder:7b' not found in Ollama
(available: ['', '', ''])
```

That message was a harness bug: the code read `name` instead of `model`. It is
now covered by the working baseline run and should be treated as a
`runner_error`-class defect if it reappears.

---

## 7. Artifact and metadata contract

For every run, the tracked summary must make the following auditable:

| Field | Location |
|-------|----------|
| Git commit | `run.json:git_commit` |
| Model and digest | `run.json:model`, `run.json:model_digest` |
| Temperature, seed, repairs | `run.json` and `runs.csv` |
| Retrieval corpus hash | `summary.json:retrieval_corpus_hash` |
| Retrieval frozen/learning | `summary.json:retrieval_memory_frozen` |
| Explicit classification | `runs.csv:explicit_classification` |
| Batch validity | `runs.csv` columns and `summary.json:configs.<name>` |
| Raw prompt/RTL/logs | `runs/<run_id>/attempt_N_*` (gitignored) |

`runs.csv` is the source of truth. `runs.jsonl` and `report.md` are regenerated
from the CSV rows, so a crash or resume cannot leave them describing a different
set of runs.

---

## 8. Best-of-N classification

`bench.bon.run_one_bon()` applies the same `classify_final_result()` mapping to
each candidate:

- a candidate that raises a runner exception becomes `runner_error`;
- the first `pass` wins and later candidates are not generated;
- if all candidates fail, the last candidate's row is reported with
  `bon_candidate_index` and `bon_total_candidates`;
- candidate seeds are `seed + candidate_index * 1000`, so every candidate is an
  independent generation;
- every candidate receives the same spec, device list, temperature, repair
  budget, retrieval setting, patcher setting and output root.

`tests/test_bench_bon_mock.py` verifies these properties with a deterministic
mock runner before any real Best-of-N compute is spent. Best-of-N results remain
**unmeasured** until that suite and a real baseline are green.

---

## 9. Reporting rules

- Report infrastructure failures and candidate verification failures separately.
- Never quote a pass rate from a batch with `batch_valid = false`.
- Never count a repeated deterministic candidate as a distinct success; use
  `distinct_first_designs`.
- Never substitute a known-good design for a failed model candidate.
- Preserve historical artifacts. If an old artifact used a different
  vocabulary, label it; do not rewrite it to look current.
