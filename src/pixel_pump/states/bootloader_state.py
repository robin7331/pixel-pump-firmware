import machine

from pixel_pump.enums import Colors, Brightness
from .state import State

class BootloaderState(State):
    def __init__(self, device):
        super().__init__(device)
        self.activated = False
        

    def on_enter(self, previous_state):
        self.device.lift_button.set_color(Colors.WHITE, Brightness.DEFAULT)
        self.device.drop_button.set_color(Colors.WHITE, Brightness.DEFAULT)
        self.device.low_button.set_color(Colors.WHITE, Brightness.DEFAULT)
        self.device.high_button.set_color(Colors.WHITE, Brightness.DEFAULT)
        self.device.reverse_button.set_color(Colors.WHITE, Brightness.DEFAULT)
        self.device.trigger_button.set_color(Colors.WHITE, Brightness.DEFAULT)

    def tick(self, tick_ms):
        
        if not self.activated:
            self.activated = tick_ms

        # Give the buttons a bit time to animate to white
        if tick_ms - self.activated > 500:
            machine.bootloader()

