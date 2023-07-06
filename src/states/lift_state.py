from enums.colors import Colors
from enums.brightness import Brightness
import states

class LiftState(states.State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.settings_manager.set_mode(0)
        self.device.lift_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.device.motor.stop()
        self.device.nc_valve.deactivate()
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

    def on_exit(self, next_state):
        self.device.lift_button.clear_color()
        self.device.nc_valve.deactivate()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.clear_color()

    def to_drop(self, autorun=True):
        self.device.set_state(states.DropState(self.device))

    def to_reverse(self):
        self.device.set_state(states.ReverseState(self.device))

    def to_brightness_settings(self):
        self.device.set_state(states.BrightnessSettingsState(self.device))

    def trigger_on(self):
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.motor.start()
        self.device.nc_valve.deactivate()

    def trigger_off(self):
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)
        self.device.motor.stop()
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(500)

    def on_motor_timeout(self, motor):
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)
        self.device.motor.stop()
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(500)