# I2C benchmark case c08-three-byte

Master writes THREE bytes, `0x01`, `0x02`, `0x03`, to slave address 0x50.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Three data bytes; tests count-parameterised FSM generation.
