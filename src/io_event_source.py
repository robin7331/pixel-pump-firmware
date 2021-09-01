from machine import Pin
import utime


class IOEvent:
    ACTIVATE = 0
    DEACTIVATE = 1
    HOLD = 2
    LONG_HOLD = 3
    ULTRA_LONG_HOLD = 4
    TAPPED = 5


class IOEventSource:
    def __init__(self, title, pin_number, pin_mode, pin_pull, long_hold_threshold=750, tapped_threshold=300, on_event=None, on_tapped=None, on_active=None, on_deactive=None, on_hold=None, on_long_hold=None):
        self.title = title
        self.pin = Pin(pin_number, pin_mode, pin_pull)
        self.long_hold_threshold = long_hold_threshold
        self.tapped_threshold = tapped_threshold
        self.on_tapped = on_tapped
        self.on_event = on_event
        self.on_active = on_active
        self.on_deactive = on_deactive
        self.on_hold = on_hold
        self.on_long_hold = on_long_hold
        self.pressed = False
        self.active_start = 0

    def tick(self):
        state = False
        state = self.pin.value()

        if state and self.active_start > 0 and (utime.ticks_ms()-self.active_start) > self.long_hold_threshold:
            self.active_start = 0
            if self.on_long_hold:
                self.on_long_hold(self)
            if self.on_event:
                self.on_event(self, IOEvent.LONG_HOLD)

        if not state and self.active_start > 0 and (utime.ticks_ms()-self.active_start) > 50 and (utime.ticks_ms()-self.active_start) < self.tapped_threshold:
            self.active_start = 0
            if self.on_tapped:
                self.on_tapped(self)
            if self.on_event:
                self.on_event(self, IOEvent.TAPPED)

        if state != self.pressed:
            self.pressed = state
            if self.pressed:
                self.active_start = utime.ticks_ms()
                if self.on_active:
                    self.on_active(self)
                if self.on_event:
                    self.on_event(self, IOEvent.ACTIVATE)
            elif not self.pressed:
                self.active_start = 0
                if self.on_deactive:
                    self.on_deactive(self)
                if self.on_event:
                    self.on_event(self, IOEvent.DEACTIVATE)

        if state:
            if self.on_hold:
                self.on_hold(self)
            if self.on_event:
                self.on_event(self, IOEvent.HOLD)
