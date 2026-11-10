# I2C benchmark case c06-two-byte

Master writes TWO bytes, `0x11`, `0x22`, to slave address 0x50.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Two data bytes, matching protocols/i2c/spec_2byte.md.
