from pixel_pump.enums.power_mode import PowerMode
from pixel_pump.enums import Colors, Brightness
from .state import State
from .brightness_settings_state import BrightnessSettingsState

class ReverseState(State):
    def __init__(self, device):
        super().__init__(device)
        self.old_power_mode = None

    def on_enter(self, previous_state):
        self.device.settings_manager.set_mode(2)
        self.device.reverse_button.set_color(Colors.RED, Brightness.DEFAULT)
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)
        self.old_power_mode = self.device.power_mode
        self.device.set_power_mode(PowerMode.MAX)
        self.device.low_button.clear_color()
        self.device.high_button.clear_color()

    def on_exit(self, next_state):
        self.device.reverse_button.clear_color()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.clear_color()
        self.device.no_valve.deactivate()
        self.device.nc_valve.deactivate()
        self.device.three_way_valve.deactivate()
        self.device.set_power_mode(self.old_power_mode)

    def to_lift(self):
        from .lift_state import LiftState
        self.device.set_state(LiftState(self.device))

    def to_drop(self, autorun=True):
        from .drop_state import DropState
        self.device.set_state(DropState(self.device))

    def to_reverse(self):
        self.device.reverse_button.clear_color()
        self.device.set_last_state()

    def to_brightness_settings(self):
        self.device.set_state(BrightnessSettingsState(self.device))

    def trigger_on(self):
        self.device.motor.start()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)

        self.device.three_way_valve.activate()
        self.device.nc_valve.activate(100)
        self.device.no_valve.activate(200)

    def trigger_off(self):
        self.device.motor.stop()
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

        self.device.no_valve.deactivate(0)
        self.device.nc_valve.deactivate(100)
        self.device.three_way_valve.deactivate(200)

    def on_button_event(self, button, event):
        pass