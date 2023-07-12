# RP2 USB HID library for mouse device
# see lib/tinyusb/src/class/hid/hid_device.h for mouse (relative) descriptor
# see ports/rp2/tusb_port.c for mouse (moveto/absolute) descriptor

import usb_hid

class Mouse:        
    BUTTON_NONE     = 0x00
    BUTTON_LEFT     = 0x01
    BUTTON_RIGHT    = 0x02
    BUTTON_MIDDLE   = 0x04
    BUTTON_PREVIOUS = 0x08
    BUTTON_NEXT     = 0x10
    BUTTON_ALL = BUTTON_LEFT | BUTTON_MIDDLE | BUTTON_RIGHT | BUTTON_PREVIOUS | BUTTON_NEXT

    def __init__(self):
        self._report = bytearray(5)
            
    def _send_no_move(self) -> None:
        for i in range(1, 5):
            self._report[i] = 0
        usb_hid.report(usb_hid.MOUSE, self._report)
        
    def press(self, buttons : int) -> None:
        self._report[0] |= buttons & 0xff
        self._send_no_move()

    def release(self, buttons : int) -> None:
        self._report[0] &= ~(buttons & 0xff)
        self._send_no_move()

    def click(self, buttons : int) -> None:
        self.press(buttons)
        self.release(buttons)

    # relative mouse move
    def move(self, x=0, y=0, v=0, h=0) -> None:
        clamp = lambda change: min(127, max(-127, change))

        while (x != 0 or y != 0 or v != 0 or h !=0):
            dx = clamp(x)
            dy = clamp(y)
            dv = clamp(v)
            dh = clamp(h)
            self._report[1] = dx 
            self._report[2] = dy        
            self._report[3] = dv 
            self._report[4] = dh
            usb_hid.report(usb_hid.MOUSE, self._report)
            x -= dx
            y -= dy
            v -= dv
            h -= dh

    # absolute mouse move ([0-32767],[0-32767])
    def moveto(self, x : int, y : int) -> None:
            self._report[1] = x & 0xff 
            self._report[2] = (x >> 8) & 0xff        
            self._report[3] = y & 0xff 
            self._report[4] = (y >> 8) & 0xff           
            usb_hid.report(usb_hid.MOUSE_ABS, self._report)
