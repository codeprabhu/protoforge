# I2C benchmark case c03-zero-payload

Master writes ONE byte, `0x00`, to slave address 0x10.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: All-zero data byte; catches models that treat 0 as 'no data'.
