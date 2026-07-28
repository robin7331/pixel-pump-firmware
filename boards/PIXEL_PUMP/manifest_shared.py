include("$(PORT_DIR)/boards/manifest.py")

# Include the usb stack from micropython-lib
# https://github.com/micropython/micropython-lib/tree/master/micropython/usb
require("usb-device")
require("usb-device-hid")
require("usb-device-keyboard")
