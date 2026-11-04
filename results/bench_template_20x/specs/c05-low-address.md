# I2C benchmark case c05-low-address

Master writes ONE byte, `0x81`, to slave address 0x03.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Low address; address byte has many zero bits.
