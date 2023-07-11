
from .settings_manager import SettingsManager
from .states.lift_state import LiftState
from .states.drop_state import DropState
from .states.reverse_state import ReverseState
from .enums.brightness import Brightness
from .enums.power_mode import PowerMode
from .enums.colors import Colors


class PixelPumpStateMachine:
    def __init__(self, motor, ui_renderer, lift_button, drop_button, low_button, high_button, reverse_button, trigger_button, nc_valve, no_valve, three_way_valve):
        self.motor = motor
        self.ui_renderer = ui_renderer
        self.lift_button = lift_button
        self.drop_button = drop_button
        self.low_button = low_button
        self.high_button = high_button
        self.reverse_button = reverse_button
        self.trigger_button = trigger_button
        self.nc_valve = nc_valve
        self.no_valve = no_valve
        self.three_way_valve = three_way_valve
        self.power_mode = None
        self.last_state_class = None
        self.state = None
        self.high_duty = 0
        self.low_duty = 0

        self.settings_manager = SettingsManager()
        self.ui_renderer.brightness_modifier = self.settings_manager.get_brightness()
        power_mode = self.settings_manager.get_power_mode()

        self.set_power_mode(power_mode)
        self.high_duty = int(self.settings_manager.get_high_power_setting() * 2.55)
        self.low_duty = int(self.settings_manager.get_low_power_setting() * 2.55)

        self.motor.on_timeout = lambda motor: self.state.on_motor_timeout(
            motor)

        mode = self.settings_manager.get_mode()
        if mode is 0:
            self.set_state(LiftState(self))
        elif mode is 1:
            self.set_state(DropState(self))
        elif mode is 2:
            self.set_state(ReverseState(self))
        else:
            self.set_state(LiftState(self))

    def in_state(self, state):
        return isinstance(self.state, state)

    def set_state(self, state):
        last_state = self.state
        if last_state:
            self.last_state_class = last_state.__class__
            last_state.on_exit(state)
        self.state = state
        self.state.on_enter(last_state)

    def set_last_state(self):
        if self.last_state_class:
            self.set_state(self.last_state_class(self))

    def set_power_mode(self, power_mode):
        self.power_mode = power_mode
        self.settings_manager.set_power_mode(power_mode)
        if self.power_mode is PowerMode.HIGH:
            self.high_button.set_color(Colors.BLUE, Brightness.DEFAULT)
            self.low_button.clear_color()
        else:
            self.low_button.set_color(Colors.BLUE, Brightness.DEFAULT)
            self.high_button.clear_color()

    def target_motor_pwm(self):
        if self.power_mode is PowerMode.LOW:
            return self.low_duty
        elif self.power_mode is PowerMode.HIGH:
            return self.high_duty
        elif self.power_mode is PowerMode.MAX:
            return 255

    def tick(self, tick_ms):
        self.motor.set_pwm(self.target_motor_pwm())
        self.state.tick(tick_ms)

