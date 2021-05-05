from machine import Pin
import utime


class Valve:
    def __init__(self, output_pin):
        self.type = type
        self.output_pin = Pin(output_pin, Pin.OUT)
        self.deactivate_at = None
        self.activate_at = None

    def activate(self, delay_in_ms=0.0):
        if delay_in_ms > 0:
            self.activate_at = utime.ticks_ms() + delay_in_ms
        else:
          self.deactivate_at = None
          self.activate_at = None
          self.output_pin.value(1)

    def deactivate(self, delay_in_ms=0.0):
        if delay_in_ms > 0:
            self.deactivate_at = utime.ticks_ms() + delay_in_ms
        else:
            self.deactivate_at = None
            self.activate_at = None
            self.output_pin.value(0)

    def tick(self):
        if self.deactivate_at and utime.ticks_ms() >= self.deactivate_at:
            self.deactivate()

        if self.activate_at and utime.ticks_ms() >= self.activate_at:
            self.activate()
