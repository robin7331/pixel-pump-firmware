from pixel_pump.enums.power_mode import PowerMode

class State:
    # While True the mapping engine is bypassed and the state handles buttons
    # itself through on_button_event. Only the settings menus do this.
    suspends_mapping = False

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

    def to_power_low(self):
        self.device.set_power_mode(PowerMode.LOW)

    def to_power_high(self):
        self.device.set_power_mode(PowerMode.HIGH)

    def to_low_power_settings(self):
        from .low_power_settings_state import LowPowerSettingsState
        self.device.set_state(LowPowerSettingsState(self.device))

    def to_high_power_settings(self):
        from .high_power_settings_state import HighPowerSettingsState
        self.device.set_state(HighPowerSettingsState(self.device))

    def vent_pulse(self, duration_ms):
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(duration_ms)

    def trigger_on(self):
        pass

    def trigger_off(self):
        pass

    def on_button_event(self, button, event):
        # Only reached on the suspended path, i.e. from states that set
        # suspends_mapping and override this. Every other state is driven by
        # the mapping engine, which speaks the intents above.
        pass

    def on_motor_timeout(self, motor):
        pass

    def tick(self, tick_ms):
        pass
