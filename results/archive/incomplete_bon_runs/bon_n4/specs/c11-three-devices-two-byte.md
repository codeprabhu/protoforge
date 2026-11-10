# I2C benchmark case c11-three-devices-two-byte

Master writes TWO bytes, `0xA5`, `0x5A`, to slave address 0x21.

Device instances: device_sensor, device_controller, device_timer.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Three devices plus a two-byte payload.
