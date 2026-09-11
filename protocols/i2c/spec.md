# I2C protocol spec (v1, master-only, single-slave, no clock stretching, no multi-master)

## Top-level contract

Every generated glue module MUST be named `top_glue` and expose EXACTLY
this port list, regardless of what's inside:

```verilog
module top_glue (
    input  wire clk,
    input  wire rst_n,
    inout  wire sda,
    inout  wire scl,
    input  wire tb_start,
    output wire tb_done
);
```

This is fixed so one hand-written testbench (`testbench_i2c.v`) works
against every generation attempt without ever being edited.

## Physical rules

- Two wires: SDA (data), SCL (clock), both open-drain with pull-ups
  (model as `inout wire` with tri-state drivers in simulation; resistors
  are not modeled in RTL sim -- assume ideal high when released).
- SDA must only change while SCL is LOW. Changing SDA while SCL is HIGH is
  reserved for START/STOP conditions only.

## Transaction primitives

1. START condition: SDA falls while SCL is HIGH.
2. STOP condition: SDA rises while SCL is HIGH.
3. Address phase: master sends 7-bit address + 1 R/W bit (MSB first), 8 clock pulses.
4. ACK/NACK: 9th clock pulse. Receiver pulls SDA low = ACK. SDA stays high = NACK.
5. Data phase: 8 bits, MSB first, followed by another ACK/NACK clock pulse.
6. Repeated START: not required for v1.

## v1 required transaction (this is what the testbench checks)

Master (top_glue's I2C logic) writes ONE byte to slave address 0x50:

```
START -> [0x50 << 1 | 0] -> ACK -> [data byte] -> ACK -> STOP
```

`tb_start` pulses high for one cycle to kick off this transaction.
`tb_done` must go high once the STOP condition has been issued.

## Explicitly out of scope for v1

- Clock stretching
- Multi-master / arbitration
- Repeated START
- 10-bit addressing
