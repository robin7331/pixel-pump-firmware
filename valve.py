from machine import Pin
import utime


class Valve:
    def __init__(self, outputPin):
        self.type = type
        self.outputPin = Pin(outputPin, Pin.OUT)
        self.deactivateAt = None
        self.activateAt = None

    def activate(self, delayInMs=0.0):
        if delayInMs > 0:
            self.activateAt = utime.ticks_ms() + delayInMs
        else:
          self.deactivateAt = None
          self.activateAt = None
          self.outputPin.value(1)

    def deactivate(self, delayInMs=0.0):
        if delayInMs > 0:
            self.deactivateAt = utime.ticks_ms() + delayInMs
        else:
            self.deactivateAt = None
            self.activateAt = None
            self.outputPin.value(0)

    def tick(self):
        if self.deactivateAt and utime.ticks_ms() >= self.deactivateAt:
            self.deactivate()

        if self.activateAt and utime.ticks_ms() >= self.activateAt:
            self.activate()
