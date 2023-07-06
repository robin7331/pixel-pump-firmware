from enums.colors import Colors
from enums.brightness import Brightness
import states


class DropState(states.State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.settings_manager.set_mode(1)
        self.device.drop_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.set_paused()

    def on_exit(self, next_state):
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.clear_color()
        self.device.drop_button.clear_color()
        self.device.motor.stop()

    def set_paused(self):
        self.paused = True
        self.device.motor.stop()
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

    def set_running(self):
        self.paused = False
        self.device.motor.start()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)

    def vent(self):
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(500)

    def to_lift(self):
        self.device.set_state(states.LiftState(self.device))

    def to_drop(self, autorun=True):
        if autorun:
            if self.paused:
                self.set_running()
            else:
                self.set_paused()
                self.vent()

    def to_reverse(self):
        self.device.set_state(states.ReverseState(self.device))

    def to_brightness_settings(self):
        self.device.set_state(states.BrightnessSettings(self.device))

    def trigger_on(self):
        if self.paused:
            self.set_running()
        else:
            self.device.motor.stop()
            self.vent()
            self.device.trigger_button.pulsate(
                Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

    def trigger_off(self):
        self.set_running()
        self.device.nc_valve.deactivate()

    # Should not be reached since the motor timeout is at 30s and this state's timeout is at 15
    # Added just in case we change the state or motor timeout to something else.
    def on_motor_timeout(self, motor):
        self.set_paused()
        self.vent()