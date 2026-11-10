# I2C benchmark case c12-top-address

Master writes ONE byte, `0x7F`, to slave address 0x7F.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Highest legal 7-bit address; write byte is 0xFE.
