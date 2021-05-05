import array
from machine import Pin
import rp2


@rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_LEFT,
             autopull=True, pull_thresh=24)  # PIO configuration
# define WS2812 PIO
def ws2812():
    T1 = 2
    T2 = 5
    T3 = 3
    wrap_target()
    label("bitloop")
    out(x, 1)               .side(0)[T3 - 1]
    jmp(not_x, "do_zero")   .side(1)[T1 - 1]
    jmp("bitloop")          .side(1)[T2 - 1]
    label("do_zero")
    nop()                   .side(0)[T2 - 1]
    wrap()


class UIRenderer:
    def __init__(self, on_rendering_finished=None):
        self.on_rendering_finished = on_rendering_finished
        self.led_count = 12
        self.buttonCount = self.led_count / 2
        self.is_dirty = True
        self.brightness_modifier = 1.0

        # Basically our frame buffer
        self.pixel_array = array.array("I", [0 for _ in range(self.led_count)])
        self.brightness_array = array.array(
            "f", [0 for _ in range(self.led_count)])

        # Create the StateMachine with the ws2812 program.
        # Its running at 8MHz so it has 10 clock cycles and outputs data with 800kHz just as the WS2812 spec says.
        self.state_machine = rp2.StateMachine(
            0, ws2812, freq=8_000_000, sideset_base=Pin(26))

        # Activate the state machine
        self.state_machine.active(1)

    def flush_frame_buffer(self):
        if not self.is_dirty:
            return

        dimmer_array = array.array("I", [0 for _ in range(self.led_count)])
        for index, pixelValue in enumerate(self.pixel_array):
            brightness = self.brightness_array[index]
            # 8-bit red dimmed to brightness
            r = int(((pixelValue >> 8) & 0xFF) *
                    brightness * self.brightness_modifier)
            # 8-bit green dimmed to brightness
            g = int(((pixelValue >> 16) & 0xFF) *
                    brightness * self.brightness_modifier)
            # 8-bit blue dimmed to brightness
            b = int((pixelValue & 0xFF) * brightness *
                    self.brightness_modifier)
            # 24-bit color dimmed to brightness
            dimmer_array[index] = (g << 16) + (r << 8) + b

        # update the state machine with new colors
        self.state_machine.put(dimmer_array, 8)
        self.is_dirty = False

        if self.on_rendering_finished:
            self.on_rendering_finished()

    def set_led_color(self, index, color, brightness=1.0):
        self.is_dirty = True
        self.pixel_array[index] = (color[1] << 16) + (color[0] << 8) + color[2]
        self.brightness_array[index] = brightness
