# RP2 USB HID library for gamepad device
# see lib/tinyusb/src/class/hid/hid_device.h for gamepad descriptor

import usb_hid

class GamePad:        
    def __init__(self):
        self._report = bytearray(9)
    
    def set_linear(self, x, y, z : int) -> None:
        self._report[0] = x
        self._report[1] = y
        self._report[2] = z

    def set_rotation(self, rx, ry, rz : int) -> None:
        self._report[3] = rz
        self._report[4] = rx
        self._report[5] = ry

    def set_hat(self, hat : int) -> None:
        self._report[6] = hat

    def set_buttons(self, buttons : int) -> None:
        self._report[7] = buttons & 0xff
        self._report[8] = (buttons >> 8) & 0xff

    def send(self) -> None:
        usb_hid.report(usb_hid.GAMEPAD, self._report)
