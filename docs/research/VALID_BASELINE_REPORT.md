# Valid Baseline Report

**Experiment date:** 2026-11-12
**Branch:** `research/valid-baseline`
**Current baseline:** `results/bench_baseline_v4/` (commit `f8d204f`)
**Previous corrected baseline:** `results/bench_baseline_v3/` (commit `6f3fc15`)
**Pre-fix baseline (evidence only):** `results/bench_baseline_v2/`
**Targeted multi-byte validations:** `results/bench_multibyte_v4/`, `results/bench_multibyte_v6/`

---

## 1. Provenance of the current baseline

| Parameter | Value |
|-----------|-------|
| Git commit | `f8d204f11e552408c70c6b5998d0ffaeebc5d5ff` |
| Model | `qwen2.5-coder:7b` |
| Model digest | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| Temperature | `0.25` |
| Max repairs | `2` |
| Base seed | `42` (samples use seeds 42, 43) |
| Samples per case | `2` |
| Config | `on-on` (retrieval on, patcher on, RTL generator) |
| Cases | 12 (`c01`–`c12`) |
| Runs | 24 (12 cases × 2 samples) |
| Retrieval memory | Frozen (no learning during the batch) |
| Retrieval corpus hash | `ee388ce1da2e30c28b229fdba561743a063147d961d3267a0384be310d6eeb22` |
| Preflight | ollama import, server, model, `iverilog`, `vvp`, `verilator`, `yosys` all passed |

Command:

```bash
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_baseline_v4 --quiet
```

Preflight passed, so the model was invoked on every run and the batch is valid:

- runs: 24, `infrastructure_failures`: 0, `model_invoked`: true,
  `batch_valid`: true, `runs_without_candidate`: 0.
- All 24 per-run headers record `git_commit` `f8d204f…`; no commit was made
  while the batch was running.

The corpus, model, temperature, seeds, repair budget, and verification gates are
identical to `results/bench_baseline_v3/`. The only difference is the harness
fixes in §4.3–§4.5, so the two batches are directly comparable.

---

## 2. Current baseline results

| Metric | v4 (current) | v3 (previous) |
|--------|-------------:|--------------:|
| Runs | 24 | 24 |
| Infrastructure failures | 0 | 0 |
| Candidate verification failures | 1 | 8 |
| **pass@1 (first attempt)** | **21/24 (87.5%)**, Wilson 95% CI [69.0%, 95.7%] | 16/24 (66.7%) [46.7%, 82.0%] |
| **Pass after repair** | **23/24 (95.8%)**, Wilson 95% CI [79.8%, 99.3%] | 16/24 (66.7%) |
| Recovered by repair | **2** | 0 |
| Distinct first designs | 22/24 | 22/24 |
| Median latency | 105.552 s | 170.461 s |
| Median tokens per run | 3,484 | 3,487 |
| Total tokens | 113,113 | 174,550 |

Explicit classifications: `pass` 23, `simulation_failure` 1;
`generation_error`, `runner_error`, `lint_failure`, `oracle_failure`,
`compile_failure` and `structural_failure` are all 0.

Pass@1 and pass-after-repair are reported separately. Two runs (c06 sample 0,
c08 sample 1) pass after one repair; the remaining failure (c08 sample 0) is a
genuine model failure after three attempts. No run required more than three
attempts (21 runs: 1 attempt, 2 runs: 2 attempts, 1 run: 3 attempts).

The v3 → v4 change is not a new capability claim: it is the measured effect of
removing three harness defects that had corrupted or discarded correct model
output (§4.3–§4.5). The same model, corpus, and gates were used.

---

## 3. Per-case results (first attempt and after repair reported separately)

### Single-byte (c01–c05, c09, c10, c12) — 16 runs, all first-attempt passes

| Case | Description | Runs | pass@1 | After repair | Attempts |
|------|-------------|-----:|-------:|-------------:|----------|
| c01 | default 1-byte | 2 | 2/2 | 2/2 | 1 |
| c02 | alternate address | 2 | 2/2 | 2/2 | 1 |
| c03 | zero payload | 2 | 2/2 | 2/2 | 1 |
| c04 | all-ones payload | 2 | 2/2 | 2/2 | 1 |
| c05 | low address | 2 | 2/2 | 2/2 | 1 |
| c09 | single device | 2 | 2/2 | 2/2 | 1 |
| c10 | three devices | 2 | 2/2 | 2/2 | 1 |
| c12 | top address | 2 | 2/2 | 2/2 | 1 |

Single-byte is unchanged from v3: **16/16 pass@1**.

### Multi-byte (c06–c08, c11) — 8 runs

| Case | Bytes | Devices | Runs | pass@1 | After repair | Final classification |
|------|------:|--------:|-----:|-------:|-------------:|----------------------|
| c06 two-byte | 2 | 2 | 2 | 1/2 | 2/2 | one recovered after 1 repair |
| c07 two-byte alt | 2 | 2 | 2 | 2/2 | 2/2 | pass |
| c08 three-byte | 3 | 2 | 2 | 0/2 | 1/2 | `simulation_failure` (sample 0) |
| c11 three devices two-byte | 2 | 3 | 2 | 2/2 | 2/2 | pass |

Multi-byte moves from **0/8 in v3 to 5/8 pass@1 and 7/8 after repair**.

The single remaining failure, c08 sample 0, is model-owned at every attempt:

1. attempt 0: compiles fail because the candidate uses `half_cnt` without
   declaring it (the harness does not own design-internal counters);
2. attempt 1: sends two of three data bytes, changes SDA while SCL is high,
   and reports the wrong byte count;
3. attempt 2: never asserts `tb_done`, so no `RESULT:` line is produced.

### Historical v3 multi-byte table (before §4.3–§4.5)

| Case | Runs | pass@1 | After repair | Final classification |
|------|-----:|-------:|-------------:|----------------------|
| c06 two-byte | 2 | 0/2 | 0/2 | `compile_failure` (undefined `DATA_BYTE1`) |
| c07 two-byte alt | 2 | 0/2 | 0/2 | `simulation_failure`, `structural_failure` |
| c08 three-byte | 2 | 0/2 | 0/2 | `simulation_failure` |
| c11 three devices two-byte | 2 | 0/2 | 0/2 | `simulation_failure`, `structural_failure` |

Those failures were recorded before the payload-constant, retrieval, and
naming-scheme fixes. They remain as evidence and must not be quoted as model
capability.

---

## 4. Why the numbers changed: harness defects, all fixed

### 4.1 `HALF` was omitted from every first attempt (fixed, regression-tested)

`_normalize_fragment_scaffold` decided that a protocol constant was already
declared if its name appeared **anywhere** in a `wire`/`reg` statement. The
model almost always writes `wire half_done = (half_cnt == HALF - 1);`, so the
`localparam HALF = 4;` line was dropped and every first attempt failed with:

```
error: Unable to bind wire/reg/memory `HALF'
```

Evidence: in the pre-fix baseline (`results/bench_baseline_v2/`) 24/24 first
attempts carry that compiler error, 0/24 pass@1, and 16/24 only pass after
repair. In the corrected baselines 0/24 attempts mention `HALF` in a compiler
error and every first attempt that references `half_done` carries exactly one
`localparam HALF = 4;`.

Fix: an already-declared constant now requires a real
`localparam|parameter … NAME =` declaration. Test:
`tests/test_fragment_scaffold.py::test_half_referenced_inside_wire_declaration_is_not_treated_as_declared`.

### 4.2 Contract-violating repair responses duplicated scaffold-owned code (fixed, regression-tested)

Two recorded multi-byte runs ended `structural_failed` because the repair
response re-emitted `device_sensor`/`device_controller` instance blocks and its
own `assign sda` / `assign scl` drivers. The structural gate requires exactly
one open-drain driver and exactly one instance per device, so the assembled
RTL failed and the defect was charged to the candidate even though the model
had written a valid FSM fragment.

Fix: the normalization path drops model-supplied `assign sda`/`assign scl`
drivers and model-supplied device instance blocks before the deterministic
wrapper is added. Tests:
`tests/test_fragment_scaffold.py::test_model_instance_blocks_and_bus_assigns_are_dropped`
and
`::test_real_failing_repair_artifact_normalizes_cleanly`, which replays the two
recorded failing artifacts and asserts the gate now passes.

**Measured effect** (`results/bench_multibyte_v4/`, same corpus hash, commit
`3954966`): the two `structural_failure` outcomes disappeared; the remaining 8
multi-byte failures were 5 `simulation_failure` and 3 `compile_failure`. Those
compile failures were later traced to §4.3 and §4.5.

### 4.3 Multi-byte payload constants were dropped by fragment coercion (fixed, regression-tested)

Three recorded c06 attempts declared `DATA_BYTE1`/`DATA_BYTE2` with the exact
spec values, but `_coerce_full_module_to_fragment` slices a full module at the
last `localparam` before the always block. Those declarations were above the
device section and `HALF` was below it, so the payload constants were dropped
and the candidate failed with:

```
error: Unable to bind wire/reg/memory `DATA_BYTE1'
```

Fix: payload constants are harness-owned. The scaffold supplies the payload
constants from the parsed spec, rewrites a stale model value to the spec value,
and preserves other model constants that the retained fragment still
references. The repair path receives the whole payload list. Tests:
`tests/test_fragment_scaffold.py::test_multi_byte_payload_constants_are_scaffolded_from_the_spec`,
`::test_stale_payload_literal_is_rewritten_to_the_spec_value`,
`::test_real_multi_byte_artifact_keeps_its_payload_constants`, and
`tests/test_template_master.py::test_two_byte_reference_specializes_to_a_new_payload`.

### 4.4 Multi-byte prompts were shown a one-byte reference (fixed, regression-tested)

The 1-byte and 2-byte seed references tie on lexical retrieval score, and the
label order always selected the 1-byte design. Every multi-byte prompt was
therefore shown a single-byte example, and the first targeted run's candidate
used only `DATA_BYTE1`.

Fix: retrieval prefers the reference matching the payload length (2-byte for
two bytes; the 2-byte reference is the fallback for three bytes), the
multi-byte prompt states the required
`DATA_BYTE1 -> ACK -> DATA_BYTE2 -> ACK` sequence explicitly, and
`_behavior_context` no longer leaves the reference's `DATA_BYTE_0`/
`DATA_BYTE_1` undeclared. Tests:
`tests/test_retrieval_corpus.py::test_multi_byte_query_prefers_the_two_byte_reference`,
`::test_three_byte_query_falls_back_to_the_two_byte_reference`, and
`tests/test_protocol_spec.py::test_multi_byte_prompt_names_the_payload_constants`.

**Measured effect** (`results/bench_multibyte_v6/`, commit `be4517f`): 3/8 pass
after repair (2/8 pass@1, 1 recovered), no compile or structural failures, and
all 8 first attempts retrieved the 2-byte reference. The v6 artifacts are
retained as evidence; 4 of those 8 runs were later found to be corrupted by the
defect in §4.5.

### 4.5 Both payload-naming schemes were injected at once (fixed, regression-tested)

The scaffold supplied `DATA_BYTE1`/`DATA_BYTE2` (1-based) **and**
`DATA_BYTE_0`/`DATA_BYTE_1` (0-based) for the same payload. The same name
therefore meant different bytes under the two schemes. Recorded candidates
wrote a correct 1-based declaration:

```verilog
localparam DATA_BYTE_1 = 8'h11;  // first data byte
localparam DATA_BYTE_2 = 8'h22;  // second data byte
```

The scaffold then rewrote `DATA_BYTE_1` to the *second* payload byte (`0x22`),
so the candidate transmitted `0x22` twice. Candidates that declared nothing and
referenced `DATA_BYTE_2` failed to compile because only the 0-based aliases
were supplied.

Fix: choose one scheme per fragment. If the fragment references `DATA_BYTE_0`
it is 0-based; otherwise its `DATA_BYTE_<n>` names are 1-based. Only the chosen
scheme is declared and rewritten. This keeps support for the checked-in
0-based 2-byte reference without changing what a 1-based name means. Tests:
`tests/test_fragment_scaffold.py::test_one_based_payload_names_are_not_mapped_as_zero_based`,
`::test_zero_based_and_one_based_fragments_use_distinct_schemes`,
`::test_recorded_one_based_candidates_keep_the_spec_payload`, and
`tests/test_template_master.py::test_one_based_payload_reference_passes_every_gate`.

Replay evidence (recorded v6 raw responses, real compile/simulate/judge):

| Run | Before fix | After fix |
|-----|-----------|-----------|
| c06 sample 0 | fail | pass after 1 repair |
| c06 sample 1 | fail | **pass@1** |
| c07 sample 0/1 | pass | pass |
| c08 sample 0 | fail | fail (genuine FSM) |
| c08 sample 1 | pass | pass after 1 repair |
| c11 sample 0 | fail | **pass@1** |
| c11 sample 1 | fail | **pass@1** |

That is 5/8 pass@1 and 7/8 after repair on the same responses, versus the 3/8
recorded under the corrupting scaffold. The v4 baseline reproduces exactly
this outcome (c06: 1/2 pass@1, 2/2 after repair; c11: 2/2 pass@1).

---

## 5. Reproducibility check

```bash
cd /home/shrivathssp/Desktop/protoforge
.venv/bin/python -m bench.run \
  --configs on-on --samples 2 --case-limit 12 \
  --out-dir results/bench_baseline_v4 --quiet
```

Recorded on the current corpus: 24 runs, 0 infrastructure failures, batch
valid, **21/24 pass@1**, **23/24 after repair**, 2 recoveries, single-byte
16/16, multi-byte 5/8 pass@1 and 7/8 after repair. Sampling variation moves
individual cases, so compare the corpus hash and classification breakdown
before comparing rates.

## 6. What is and is not established

1. A **valid** baseline exists: model invoked in all 24 runs, no
   infrastructure failure, full provenance at one commit, corpus hash recorded.
2. Single-byte I²C generation is **reliable on the first attempt** (16/16,
   unchanged across v3 and v4).
3. Multi-byte generation is **substantially improved but not solved**:
   5/8 pass@1 and 7/8 after repair. The remaining c08 sample 0 failure is a
   genuine model FSM/byte-count failure, not a harness defect.
4. The v3 multi-byte numbers (0/8) and the pre-fix 0/24 pass@1 were
   harness-owned. Do not quote them as capability results; they are retained as
   defect evidence only.
5. Best-of-N is **still unmeasured**. The archived 48-run "Best-of-4" batch is
   invalid (the model was never invoked) and must not be used for comparisons.
6. The 3-byte case is the weakest: 1/2 after repair, with no 3-byte seed
   reference (retrieval falls back to the 2-byte design). Do not generalize the
   2-byte result to arbitrary payload lengths.
