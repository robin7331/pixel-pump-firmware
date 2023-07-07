from pixel_pump.controls.button_event import ButtonEvent
from pixel_pump.enums.power_mode import PowerMode
from pixel_pump.enums import Colors, Brightness
from .state import State


class HighPowerSettingsState(State):
    def __init__(self, device):
        super().__init__(device)
        self.old_duty = None
        self.current_duty = None
        self.old_power_mode = None

    def on_enter(self, previous_state):
        self.old_duty = self.device.high_duty
        self.old_power_mode = self.device.power_mode
        self.device.set_power_mode(PowerMode.HIGH)
        self.device.motor.start()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.reverse_button.set_color(Colors.RED, Brightness.DEFAULT)
        self.device.low_button.set_color(Colors.BLUE, Brightness.DIMMER)
        self.device.high_button.pulsate(
            Colors.BLUE, Brightness.DIMMER, Colors.BLUE, Brightness.BRIGHTER)

    def on_exit(self, next_state):
        self.device.motor.stop()
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.high_button.clear_color()
        self.device.high_button.stop_pulsating()
        self.device.set_power_mode(self.old_power_mode)

    def on_button_event(self, btn, event):
        if btn is self.device.low_button and event is ButtonEvent.TOUCH_DOWN:
            duty = self.device.high_duty - 10
            if duty < 0:
                duty = 0
            self.device.high_duty = duty
        if btn is self.device.high_button and event is ButtonEvent.TOUCH_DOWN:
            duty = self.device.high_duty + 10
            if duty > 255:
                duty = 255
            self.device.high_duty = duty

    def to_reverse(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.high_duty = self.old_duty
        self.device.set_last_state()

    def trigger_off(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.settings_manager.set_high_pwm_duty(self.device.high_duty)
        self.device.set_last_state()

    def on_motor_timeout(self, motor):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.high_duty = self.old_duty
        self.device.set_last_state()
