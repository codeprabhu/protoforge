# I2C benchmark case c01-default-1byte

Master writes ONE byte, `0x3C`, to slave address 0x50.

Device instances: device_sensor, device_controller.
The fixed top_glue port list and open-drain rules are unchanged.

Notes: v1 default: address 0x50, one data byte, two devices.
