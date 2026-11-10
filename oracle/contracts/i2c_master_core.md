# I²C Master Core — Frozen Interface Contract (v2)

**Version:** 2.0
**Oracle version:** o2.0
**Status:** FROZEN — agents may not edit; changes require Oracle Promotion Protocol + HUMAN signature.

---

## Module Interface

```verilog
module i2c_master_core #(
    parameter int CLK_HZ = 100_000_000,
    parameter int SCL_HZ = 100_000,
    parameter int STRETCH_TIMEOUT_US = 1000      // 0 disables the timeout
) (
    input  logic       clk,
    input  logic       rst_n,                   // active low, asynchronous assert
    // Request channel: one request = [START|repeated START] addr+R/W, N bytes, then STOP or hold
    input  logic       req_valid,
    output logic       req_ready,               // high only when idle or holding the bus
    input  logic [6:0] req_addr,                // 7-bit address (NOT shifted)
    input  logic       req_read,                // 0 = write, 1 = read
    input  logic [7:0] req_len,                 // 1..255 data bytes. 0 is illegal (undefined)
    input  logic       req_stop,                // 1 = STOP at end; 0 = hold bus, next request starts with repeated START
    // Write data stream: master -> slave
    input  logic       tx_valid,
    output logic       tx_ready,
    input  logic [7:0] tx_data,
    // Read data stream: slave -> master
    output logic       rx_valid,
    input  logic       rx_ready,
    output logic [7:0] rx_data,
    // Status (flags are sticky until the next accepted request)
    output logic       busy,
    output logic       done,                    // 1-cycle pulse when the request finished (also after an error)
    output logic       err_nack_addr,
    output logic       err_nack_data,
    output logic       err_stretch_timeout,
    // Bus, split open-drain. *_drive_low = 1 pulls the line low; 0 releases it.
    input  logic       sda_in,
    input  logic       scl_in,
    output logic       sda_drive_low,
    output logic       scl_drive_low
);
```

---

## Semantics (binding on all agents)

### Address and Data Format
- Address on the wire = `{req_addr, req_read}` MSB first.
- Data bytes transmitted MSB first.

### Write Transaction
- Consumes `req_len` bytes from `tx_*` (each accepted on `tx_valid && tx_ready`).
- The master **waits** (holds SCL low) if `tx_valid` is low when the next byte is needed.
- After the last byte, if `req_stop = 1` → issue STOP; if `req_stop = 0` → hold bus (SCL low, SDA released/low), next accepted request begins with **repeated START**.

### Read Transaction
- Produces `req_len` bytes on `rx_*`.
- The master **ACKs every byte except the last**, which it **NACKs**.
- Waits if `rx_ready` is low when the next byte is ready.

### NACK Handling
- NACK on the address byte → STOP (regardless of `req_stop`), `err_nack_addr` = 1, `done` = 1.
- NACK on a write data byte → STOP, `err_nack_data` = 1, `done` = 1; remaining `tx` bytes are **not** consumed.

### Bus Hold / Repeated START
- `req_stop = 0` leaves SCL low and SDA released/low (spec "bus held" state).
- The next accepted request begins with a **repeated START** (no intervening STOP).

### Reset Behaviour
- Both `*_drive_low` go to 0 (released) within 1 clock of `rst_n` low.
- All status flags (`busy`, `done`, `err_*`) cleared.
- Request and data channels ready for new transaction.

### Timing
- SCL frequency derived from `CLK_HZ` / `SCL_HZ` (divider rounds **up** via `ceil`).
- Setup/hold times per `oracle/timing/i2c_timing.yaml` (monitor G6 enforces).
- Clock stretching (L4): master senses `scl_in` and waits while slave holds it low; timeout after `STRETCH_TIMEOUT_US` microseconds sets `err_stretch_timeout`.

### Prohibited in Candidate Artifacts (G0 integrity rules)
- Any `$…` system task/function (`$display`, `$finish`, `$fopen`, `$system`, `$readmem*`, etc.)
- `force` / `release`
- Hierarchical references (`testbench*`, `$root`, etc.)
- `` `include ``, `` `define `` that redefine oracle macros
- `initial` blocks (unless the contract explicitly allows)
- `#` delays, `wait` statements
- `assign` to `sda_drive_low` / `scl_drive_low` outside the FSM (the FSM owns the drivers)

---

## Gate Mapping (for the Conductor and agents)

| Gate | Checks | Oracle Source |
|------|--------|---------------|
| G0 | Integrity (forbidden constructs), port list match, parameter defaults | `tools/structural_checks.py` (extended) |
| G1 | Icarus compile | `tools/compile_tool.py` |
| G2 | Verilator lint (no warnings treated as errors unless policy says) | `tools/hardware_checks.py` |
| G3 | Yosys synthesis (no latches, no multiple drivers, no width mismatch) | `tools/hardware_checks.py` |
| G4 | Functional simulation (legacy_v1 testbench + nonce) | `tools/simulation_tool.py` (nonce parser) |
| G5 | Protocol checks (START/STOP/ACK/NACK/address/data/byte_count) | `tools/judge.py` |
| G6 | Timing monitor (setup/hold/bus-free vs `i2c_timing.yaml`) | NEW: `oracle/monitor/timing_monitor.py` |
| G7 | Coverage map (requirement → checks → pass/fail) | NEW: `oracle/coverage.yaml` |
| G8 | Reset-injection / back-to-back / watchdog scenarios | NEW: hold-out suite |
| G9 | Synthesizability (full Yosys flow to netlist) | `tools/hardware_checks.py` |

---

## Change Control
Any modification to this contract, `i2c_timing.yaml`, the testbench, the BFM, the judge, or the monitor requires:
1. A proposal in `proposals/oracle/` with rationale and evidence.
2. The Oracle Promotion Protocol (see §5.6 of the v2 roadmap).
3. HUMAN approval signature.
4. New `oracle/MANIFEST.sha256` committed.