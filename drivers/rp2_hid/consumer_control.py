# RP2 USB HID library for consumer control device
# see lib/tinyusb/src/class/hid/hid_device.h for consumer_control descriptor

import usb_hid

class Consumer_Control:
    NONE                 = 0x00
    PLAY_PAUSE           = 0xcd
    SCAN_NEXT            = 0xb5
    SCAN_PREVIOUS        = 0xb6
    STOP                 = 0xb7    
    EJECT                = 0xb8    
    MUTE                 = 0xe2
    VOLUME_INCREMENT     = 0xe9
    VOLUME_DECREMENT     = 0xea
    BRIGHTNESS_INCREMENT = 0x6f
    BRIGHTNESS_DECREMENT = 0x70
    
    def __init__(self):
        self._report = bytearray(2)
    
    def send(self, control : int) -> None:    
        self._report[0] = control & 0xff
        self._report[1] = (control >> 8) & 0xff
        usb_hid.report(usb_hid.CONSUMER_CONTROL, self._report)
        self._report[0] = self._report[1] = 0
        usb_hid.report(usb_hid.CONSUMER_CONTROL, self._report)
