# I2C benchmark case c04-all-ones-payload

Master writes ONE byte, `0xFF`, to slave address 0x77.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: All-ones data byte and a high address.
