# The I2C oracle (v1)

A candidate only "passes" when every gate below agrees. This document is the
contract: it lists what is actually observed, what is inferred, and what the
v1 oracle deliberately does **not** check. The golden reference and the
testbench are never generated or edited by the model.

## Gate order

1. **Structural** (`tools/structural_checks.py`) — parses the RTL, requires the
   `top_glue` port list, the two device instances, and legal open-drain
   `sda`/`scl` drivers (no `1'b1` push-pull drive, no procedural bus drive, no
   delays).
2. **Compile** (`tools/compile_tool.py`) — `iverilog -g2001` with the two
   devices and the trusted testbench.
3. **Hardware** (`tools/hardware_checks.py`) — `verilator --lint-only` then a
   Yosys `read_verilog`/`hierarchy`/`proc`/`opt`/`check`/`stat` pass. Verilator
   warning count is recorded in the stage metadata but does not fail the run
   yet (see "Hardware warnings").
4. **Simulation** (`tools/simulation_tool.py`) — runs `vvp` and parses one
   `RESULT:` line.
5. **Judge** (`tools/judge.py`) — maps the parsed fields to named checks.

## What the testbench observes

`protocols/i2c/testbench_i2c.v` instantiates the candidate as `top_glue`,
drives `tb_start`, and acts as a bus-functional-model slave that ACKs address
`0x50` (write byte `0xA0`) and one data byte. It watches the raw `sda`/`scl`
wires like a logic analyzer and prints:

```
RESULT:saw_start=.. saw_stop=.. saw_ack_addr=.. saw_ack_data=..
       addr=.. data=.. tb_done=.. starts=.. stops=.. sda_glitches=.. bytes=..
```

`+I2C_ADDR=<hex>` and `+I2C_DATA=<hex>` override the expected address/data
without editing the testbench. `+dump_vcd` opts into waveform capture.

| Field | Meaning |
| --- | --- |
| `saw_start` | SDA fell while SCL was high at least once. |
| `saw_stop` | SDA rose while SCL was high at least once. |
| `saw_ack_addr` | The BFM armed its ACK driver after the address byte. |
| `saw_ack_data` | The BFM armed its ACK driver after the first data byte. |
| `addr` / `data` | Last byte shifted in for the address/data phase. |
| `tb_done` | `tb_done` was high at least once before the timeout. |
| `starts` / `stops` | Total START / STOP conditions seen. |
| `sda_glitches` | SDA changed while SCL was high outside a legal START/STOP, e.g. a repeated START mid-transaction or a data change during the high phase. |
| `bytes` | Completed bytes since the last START (address + data). |

## What the judge checks

| Check | Pass condition |
| --- | --- |
| `start` | Exactly one START was observed (`saw_start == 1`). |
| `stop` | A STOP was observed (`saw_stop == 1`). |
| `address_ack` | The address phase was ACKed. |
| `data_ack` | The data phase was ACKed. |
| `correct_address` | Captured address byte equals the expected byte. |
| `correct_data` | Captured data byte equals the expected byte. |
| `tb_done` | Completion was asserted. |
| `single_start` | Exactly one START, so no repeated START. |
| `single_stop` | Exactly one STOP. |
| `sda_stable` | `sda_glitches == 0`. |
| `byte_count` | `bytes == expected_data_bytes + 1` (one address byte + data). |

Checks whose fields are absent from an older `RESULT:` line are skipped, so
old logs remain parseable; they are never silently treated as passing when the
field is present and wrong.

Failure text (`explain_failures`) names the actual expected address/data, so
repair prompts stay correct when plusargs select another transaction.

## Mutation coverage

`tests/test_oracle_mutation.py` compiles single-fault mutants of the golden
master and asserts that each is rejected on the check describing its fault.
This is what makes a pass meaningful: the oracle must fail designs that are
almost right.

| Mutant | Fault | Expected check |
| --- | --- | --- |
| `no_start` | No START condition | `start` |
| `no_stop` | SDA never released for STOP | `stop` |
| `no_tb_done` | Completion never asserted | `tb_done` |
| `wrong_address` | Address `0xA2` instead of `0xA0` | `address_ack` |
| `wrong_data` | Data `0x00` instead of `0x3C` | `correct_data` |
| `lsb_first` | Byte sent LSB first | `address_ack` |
| `third_byte` | Extra data byte before STOP | `byte_count` |
| `sda_glitch` | SDA toggled while SCL high | `sda_stable` |
| `skip_ack` | Address ACK clock omitted | `data_ack` (downstream) |
| `repeated_start` | START re-issued mid-transaction | `single_start` |

### Known diagnosis gap: `skip_ack`

Omitting the address ACK clock does **not** clear `saw_ack_addr`: the BFM arms
its ACK at the byte boundary. The missing ninth clock instead shifts every
later bit cell, so the data ACK, captured data and byte count fail. The design
is still rejected, but the first reported check is `data_ack`/`byte_count`,
not `address_ack`. This is a diagnosis-quality limitation, not a false pass.
A future testbench revision should arm `saw_ack_addr` only when the ACK clock
cell is actually observed.

## What the v1 oracle does not check

- **Timing:** no setup/hold, SCL frequency, bus-free time, or clock-stretching
  assertions. The testbench only checks event ordering and waveform legality.
- **Read transactions, repeated START for reads, 10-bit addressing, general
  call, arbitration, and multi-master behaviour.** v1 is a single write of one
  address byte plus one data byte.
- **NACK handling.** The master may ignore a NACK; the reference does.
- **Reset during a transaction.** Only reset-before-start is exercised.
- **Electrical analog behaviour.** Open-drain legality is checked structurally
  and by tri-state simulation, not by drive-strength or rise-time analysis.
- **Synthesis equivalence.** Yosys only elaborates and checks the netlist; it
  does not prove the gate-level netlist matches simulation.
- **Formal properties.** There are no SVA/assertion checks yet.
- **Data-dependent behaviour beyond the selected address/data pair.** The
  plusarg tests cover several pairs, but not every value.

## Hardware warnings

`run_hardware_checks` records `warning_count` from Verilator. On the golden
reference it is `0`. Yosys emits informational warnings (tri-state support,
undriven `device_controller.read_data`) that do not fail the gate. Once a
warning policy is agreed, these kinds should fail:

- latches inferred in combinational logic;
- width mismatch / truncation on the bus or state;
- combinational loops;
- multiple drivers or undriven outputs used by `top_glue`.

They do not fail today because the policy has not been validated against the
seed examples yet.

## Reproduce

```bash
.venv/bin/python -m pytest -q tests/test_oracle_mutation.py -vv
.venv/bin/python -m pytest -q tests/test_judge_extended.py
```

Both need Icarus Verilog; the mutation suite skips cleanly without it.
