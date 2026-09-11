# Device file contract

A "device" is a single `.v` file containing exactly one module, declared
ANSI-style:

    module <name> (
        input  wire        clk,
        input  wire        rst_n,
        ... other ports ...
    );

Rules:
- Every device MUST expose `clk` and active-low `rst_n`.
- No `generate` blocks, no SystemVerilog interfaces/structs (plain Verilog-2001 only).
- No parameters that change port widths (fixed widths only, for v1).
- Exactly one module per file.
