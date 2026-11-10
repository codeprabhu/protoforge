# I2C benchmark case c10-three-devices

Master writes ONE byte, `0x3C`, to slave address 0x50.

Device instances: device_sensor, device_controller, device_timer.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: Three device instances.
