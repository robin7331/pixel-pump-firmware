import utime
from usb.device.keyboard import KeyboardInterface

# HID usages for the eight modifier keys, as stored in settings.json by the
# legacy stdin protocol (see docs/usb-communication.md and deskthority's
# scancode table).
_MOD_LEFT_CTRL = 0xE0
_MOD_RIGHT_GUI = 0xE7

# send_keys() is given a short timeout rather than the library default of
# 100 ms: a pending report only blocks when the host has stopped draining the
# keyboard endpoint, and stalling the 30 FPS render loop for three frames is
# worse than dropping one key event. A closed interface never blocks at all --
# HIDInterface.busy() is False when not open -- which is what fixes the aux
# pedal freezing the firmware with no host attached (issue #29).
_SEND_TIMEOUT_MS = 10


class Keyboard:
    """Keyboard HID on top of micropython-lib's KeyboardInterface.

    PP1 stores keys as raw HID usages, so modifiers arrive as 0xE0-0xE7 while
    ``send_keys`` wants them as negative bitmasks. ``0x00`` means "no
    modifier".

    ``enabled=False`` (settings.json's ``keyboard_enabled``) builds no
    interface at all, so ``USBManager`` has nothing to register and the device
    enumerates without a keyboard collection. Every send then answers False --
    the same answer a closed interface gives -- so callers need no extra guard.
    The check is written out here rather than left to ``is_open()`` returning
    False on an unregistered interface: issue #29 was a send path that assumed
    an absent host was harmless.
    """

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.kb = KeyboardInterface() if enabled else None
        self._clear_at_ms = None

    def tick(self):
        if self._clear_at_ms is None:
            return

        if utime.ticks_diff(utime.ticks_ms(), self._clear_at_ms) >= 0:
            self._clear_at_ms = None
            self._send_keys([])

    def press(self, modifier, keycode):
        if not self.enabled:
            return False
        self._flush_pending_tap()
        return self._send_keys(self._key_list(modifier, keycode))

    def release(self):
        if not self.enabled:
            return False
        self._clear_at_ms = None
        return self._send_keys([])

    def tap(self, modifier, keycode, duration_ms=50):
        if not self.enabled:
            return False
        # Press now, release from tick() -- keeps the key down long enough for
        # the host to see it without blocking the main loop.
        self._flush_pending_tap()
        sent = self._send_keys(self._key_list(modifier, keycode))
        self._clear_at_ms = utime.ticks_add(utime.ticks_ms(), duration_ms)
        return sent

    def is_host_open(self):
        return self.enabled and self.kb.is_open()

    def _flush_pending_tap(self):
        if self._clear_at_ms is not None:
            self._clear_at_ms = None
            self._send_keys([])

    def _key_list(self, modifier, keycode):
        keys = []
        if modifier:
            keys.append(self._translate_modifier(modifier))
        if keycode:
            keys.append(keycode)
        return keys

    def _translate_modifier(self, modifier):
        if _MOD_LEFT_CTRL <= modifier <= _MOD_RIGHT_GUI:
            return -(1 << (modifier - _MOD_LEFT_CTRL))
        # Not a modifier usage. Legacy treated these as ordinary keycodes, so
        # keep doing that rather than dropping the key.
        return modifier

    def _send_keys(self, keys):
        if self.kb is None or not self.kb.is_open():
            return False
        return self.kb.send_keys(keys, timeout_ms=_SEND_TIMEOUT_MS)
