# I2C benchmark case c02-alt-address-1byte

Master writes ONE byte, `0x5A`, to slave address 0x2A.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Different 7-bit address and payload.
