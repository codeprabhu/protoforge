# Driver boundary

ProtoForge generates synthesizable I2C master RTL and validates the local
device-module interfaces described by `devices/CONTRACT.md`. The repository
does not contain a board-specific C, Linux, or FPGA platform driver, so no
physical-device API is claimed or silently generated.

Device-local control ports are wired as deterministic RTL signals. They are
not treated as SDA/SCL activity; only the open-drain bus transaction observed
by `protocols/i2c/testbench_i2c.v` can verify an I2C operation.
