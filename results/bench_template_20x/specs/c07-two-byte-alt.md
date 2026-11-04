# I2C benchmark case c07-two-byte-alt

Master writes TWO bytes, `0xDE`, `0xAD`, to slave address 0x48.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Two-byte payload at a different address.
