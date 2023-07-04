import usb_hid

# RP2 USB HID library for keyboard device
# see lib/tinyusb/src/class/hid/hid_device.h for keyboard descriptor

class Keyboard:    
    # https://deskthority.net/wiki/Scancode
    MOD_LEFT_CTRL   = 0xe0
    MOD_LEFT_SHIFT  = 0xe1
    MOD_LEFT_ALT    = 0xe2
    MOD_LEFT_GUI    = 0xe3   
    MOD_RIGHT_CTRL  = 0xe4
    MOD_RIGHT_SHIFT = 0xe5
    MOD_RIGHT_ALT   = 0xe6
    MOD_RIGHT_GUI   = 0xe7   
    
    CODE_A  = 0x04 # a-z 0x04-0x1d
    CODE_0  = 0x27
    CODE_1  = 0x1e # 1-9 0x1e-0x26
    CODE_F1 = 0x3a # f1-f12 0x3a-0x45
    
    def __init__(self):
        self._report = bytearray(8)

    def _send(self) -> None:
        usb_hid.report(usb_hid.KEYBOARD, self._report)

    def _modifier(self, keycode : int) -> int:
        if keycode >= self.MOD_LEFT_CTRL and keycode <= self.MOD_RIGHT_GUI:
            return 1 << (keycode - self.MOD_LEFT_CTRL)

    def _add_to_report(self, keycode : int) -> None:
        modifier_bit = self._modifier(keycode)
        if modifier_bit:
            self._report[0] |= modifier_bit
        else:
            done = False
            for i in range(6):
                if self._report[2+i] == keycode:
                    done = True
                    break
            if not done:
                for i in range(6):
                    if self._report[2+i] == 0:
                        self._report[2+i] = keycode
                        done = True
                        break
                if not done:
                    raise ValueError("more than 6 keys")
                    
    def _remove_from_report(self, keycode : int) -> None:
        modifier_bit = self._modifier(keycode)
        if modifier_bit:
            self._report[0] &= ~modifier_bit
        else:
            for i in range(6):
                if self._report[2+i] == keycode:
                    self._report[2+i] = 0
                    break

    def press(self, *keycodes : int) -> None:
        for k in keycodes:
            self._add_to_report(k)
        self._send()

    def release(self, *keycodes : int) -> None:
        for k in keycodes:
            self._remove_from_report(k)
        self._send()
    
    def release_all(self) -> None:
        for i in range(8):
            self._report[i] = 0
        self._send()
        