import machine
from pixel_pump.controls.button_event import ButtonEvent
from pixel_pump.enums import Colors, Brightness
from .state import State

class BrightnessSettingsState(State):
    def __init__(self, device):
        super().__init__(device)
        self.old_brightness_modifier = None
        self.current_brightness_modifier = None

    def on_enter(self, previous_state):
        self.old_brightness_modifier = self.device.ui_renderer.brightness_modifier
        self.current_brightness_modifier = self.device.ui_renderer.brightness_modifier
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.reverse_button.set_color(Colors.RED, Brightness.DEFAULT)
        self.device.low_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.device.high_button.set_color(Colors.BLUE, Brightness.DEFAULT)

    def on_exit(self, next_state):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)

    def on_button_event(self, btn, event):
        if btn is self.device.low_button and event is ButtonEvent.TOUCH_UP:
            self.current_brightness_modifier = self.current_brightness_modifier - 0.05
            if self.current_brightness_modifier < 0.35:
                self.current_brightness_modifier = 0.35
            self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier

        if btn is self.device.high_button and event is ButtonEvent.TOUCH_UP:
            self.current_brightness_modifier = self.current_brightness_modifier + 0.05
            if self.current_brightness_modifier > 0.8:
                self.current_brightness_modifier = 0.8
            self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier

        if btn is self.device.drop_button and event is ButtonEvent.LONG_PRESS:
            from .bootloader_state import BootloaderState
            self.device.set_state(BootloaderState(self.device))

    def to_reverse(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)
        self.device.ui_renderer.brightness_modifier = self.old_brightness_modifier
        self.device.set_last_state()

    def trigger_off(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)
        self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier
        self.device.settings_manager.set_brightness(
            self.current_brightness_modifier)
        self.device.set_last_state()