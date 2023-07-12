from pixel_pump.controls.button_event import ButtonEvent
from pixel_pump.enums.power_mode import PowerMode

class State:
    def __init__(self, device):
        self.device = device

    def on_enter(self, previous_state):
        pass

    def on_exit(self, next_state):
        pass

    def to_lift(self):
        pass

    def to_drop(self, autorun=True):
        pass

    def to_brightness_settings(self):
        pass

    def to_reverse(self):
        pass

    def trigger_on(self):
        pass

    def trigger_off(self):
        pass

    def on_button_event(self, button, event):
        if button is self.device.low_button:
            if event is ButtonEvent.TOUCH_UP:
                self.device.set_power_mode(PowerMode.LOW)
            if event is ButtonEvent.LONG_PRESS:
                from .low_power_settings_state import LowPowerSettingsState
                self.device.set_state(LowPowerSettingsState(self.device))
        if button is self.device.high_button:
            if event is ButtonEvent.TOUCH_UP:
                self.device.set_power_mode(PowerMode.HIGH)
            if event is ButtonEvent.LONG_PRESS:
                from .high_power_settings_state import HighPowerSettingsState
                self.device.set_state(HighPowerSettingsState(self.device))
        pass

    def on_motor_timeout(self, motor):
        pass

    def tick(self, tick_ms):
        pass