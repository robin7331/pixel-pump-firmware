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
    def __init__(self):
        self.ledCount = 12  # number of LEDs
        self.buttonCount = self.ledCount / 2
        self.isDirty = True

        # Basically our frame buffer
        self.pixelArray = array.array("I", [0 for _ in range(self.ledCount)])
        self.brightnessArray = array.array(
            "f", [0 for _ in range(self.ledCount)])

        # Create the StateMachine with the ws2812 program.
        # Its running at 8MHz so it has 10 clock cycles and outputs data with 800kHz just as the WS2812 spec says.
        self.stateMachine = rp2.StateMachine(
            0, ws2812, freq=8_000_000, sideset_base=Pin(26))

        # Activate the state machine
        self.stateMachine.active(1)

    def flushFrameBuffer(self):
        if not self.isDirty:
            return

        dimmerArray = array.array("I", [0 for _ in range(self.ledCount)])
        for index, pixelValue in enumerate(self.pixelArray):
            brightness = self.brightnessArray[index]
            # 8-bit red dimmed to brightness
            r = int(((pixelValue >> 8) & 0xFF) * brightness)
            # 8-bit green dimmed to brightness
            g = int(((pixelValue >> 16) & 0xFF) * brightness)
            # 8-bit blue dimmed to brightness
            b = int((pixelValue & 0xFF) * brightness)
            # 24-bit color dimmed to brightness
            dimmerArray[index] = (g << 16) + (r << 8) + b

        # update the state machine with new colors
        self.stateMachine.put(dimmerArray, 8)
        self.isDirty = False

    def setLEDColor(self, index, color, brightness=1.0):
        self.isDirty = True
        self.pixelArray[index] = (color[1] << 16) + (color[0] << 8) + color[2]
        self.brightnessArray[index] = brightness

