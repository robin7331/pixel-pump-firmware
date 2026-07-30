import machine

from pixel_pump.enums import Colors, Brightness
from .state import State

class BootloaderState(State):
    def __init__(self, device):
        super().__init__(device)
        self.activated = False
        

    def on_enter(self, previous_state):
        # override, because this is the whole panel going white to say the
        # device is leaving, not per-button feedback: a host that owns buttons
        # is usually the one that sent ENTER_BOOTLOADER, and the flash is its
        # only confirmation. Nothing survives the reboot to restore.
        # stop_pulsating first, or a button left mid-pulse -- the trigger in
        # Lift, a PULSE badge -- animates straight back off the white.
        for button in (self.device.lift_button,
                       self.device.drop_button,
                       self.device.low_button,
                       self.device.high_button,
                       self.device.reverse_button,
                       self.device.trigger_button):
            button.stop_pulsating(override=True)
            button.set_color(Colors.WHITE, Brightness.DEFAULT, override=True)

    def tick(self, tick_ms):
        
        if not self.activated:
            self.activated = tick_ms

        # Give the buttons a bit time to animate to white
        if tick_ms - self.activated > 500:
            machine.bootloader()

