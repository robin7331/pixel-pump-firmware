from machine import Pin
import utime
import math

from .button_event import ButtonEvent

class Button:
    def __init__(self, title, left_led_index, right_led_index, switch_pin, long_press_threshold=750, tapped_threshold=300, on_button_event=None, on_touch_down=None, on_touch_up=None, on_tapped=None, on_touch=None, on_long_press=None, on_should_render=None, lerp_speed=0.25):
        self.title = title
        self.pin = Pin(switch_pin, Pin.IN, Pin.PULL_DOWN)
        self.left_led_index = left_led_index
        self.right_led_index = right_led_index
        self.long_press_threshold = long_press_threshold
        self.tapped_threshold = tapped_threshold
        self.on_button_event = on_button_event
        self.on_touch_down = on_touch_down
        self.on_touch_up = on_touch_up
        self.on_tapped = on_tapped
        self.on_should_render = on_should_render
        self.on_long_press = on_long_press
        self.on_touch = on_touch
        self.pressed = False
        self.last_animated_at = 0
        self.lerp_speed = lerp_speed
        self.touch_start = 0
        self.pulsing = False
        self.pulse_from_color = None
        self.pulse_from_brightness = None
        self.pulse_to_color = None
        self.pulse_to_Brightness = None
        self.pulseDirection = 0
        self.left_color = (0, 0, 0, 0.0)
        self.right_color = (0, 0, 0, 0.0)
        self.left_target_color = self.left_color
        self.right_target_color = self.right_color
        # While the host owns this button -- mapping.py's remote badge, the
        # spec's "replacing the state-machine color" -- paints from the state
        # machine are recorded here instead of shown. One button has one
        # owner, and the states do not have to know which.
        self.remote = False
        self.remote_paint = None

    def tick(self):

        state = self.pin.value()

        if state and self.touch_start > 0 and (utime.ticks_ms()-self.touch_start) > self.long_press_threshold:
            self.touch_start = 0
            if self.on_long_press:
                self.on_long_press(self)
            if self.on_button_event:
                self.on_button_event(self, ButtonEvent.LONG_PRESS)

        # Same thresholds and ordering as IOEventSource: a tap is a release
        # after 50 ms and before tapped_threshold, and it precedes TOUCH_UP.
        if not state and self.touch_start > 0 and (utime.ticks_ms()-self.touch_start) > 50 and (utime.ticks_ms()-self.touch_start) < self.tapped_threshold:
            self.touch_start = 0
            if self.on_tapped:
                self.on_tapped(self)
            if self.on_button_event:
                self.on_button_event(self, ButtonEvent.TAPPED)

        if state != self.pressed:
            self.pressed = state
            if self.pressed:
                self.touch_start = utime.ticks_ms()
                if self.on_touch_down:
                    self.on_touch_down(self)
                if self.on_button_event:
                    self.on_button_event(self, ButtonEvent.TOUCH_DOWN)
            elif not self.pressed:
                self.touch_start = 0
                if self.on_touch_up:
                    self.on_touch_up(self)
                if self.on_button_event:
                    self.on_button_event(self, ButtonEvent.TOUCH_UP)

        if state:
            if self.on_touch:
                self.on_touch(self)
            if self.on_button_event:
                self.on_button_event(self, ButtonEvent.TOUCH)

        if self.pulsing:
            # override, because the ping-pong drives whichever layer is live --
            # while the host owns the button that is the badge's own pulse, and
            # the recorded state-machine paint must not be overwritten by it.
            # Pulse to?
            if self.pulseDirection == 1:
                self.set_color(self.pulse_to_color, self.pulse_to_Brightness, override=True)
                if self.is_color_set(source_color=self.pulse_to_color, source_brightness=self.pulse_to_Brightness):
                    self.pulseDirection = 2
            elif self.pulseDirection == 2:
                self.set_color(self.pulse_from_color, self.pulse_from_brightness, override=True)
                if self.is_color_set(source_color=self.pulse_from_color, source_brightness=self.pulse_from_brightness):
                    self.pulseDirection = 1

        wait_time = (1000//30) - (utime.ticks_ms() - self.last_animated_at)
        if wait_time <= 0:
            self.last_animated_at = utime.ticks_ms()
            self.__animate()

    def __animate(self):
        self.left_color = self.__lerpColor(self.left_color, self.left_target_color)
        self.right_color = self.__lerpColor(
            self.right_color, self.right_target_color)
        if self.on_should_render:
            self.on_should_render(self)

    def __lerpColor(self, current, target):
        return (current[0] + int((target[0] - current[0]) * self.lerp_speed), current[1] + int((target[1] - current[1]) * self.lerp_speed), current[2] + int((target[2] - current[2]) * self.lerp_speed), current[3] + (target[3] - current[3]) * self.lerp_speed)

    # override=True paints the LEDs even while the host owns the button. Three
    # callers are entitled to it: the mapping engine rendering the badge (it
    # *is* the host's paint), the pulse ping-pong above, and the bootloader's
    # whole-panel takeover. Everything else is state-machine feedback and
    # yields.

    def set_color(self, color, brightness, animated=True, override=False):
        if self.remote and not override:
            self.remote_paint[0] = (color[0], color[1], color[2], brightness)
            return

        self.left_target_color = (color[0], color[1], color[2], brightness)
        self.right_target_color = (color[0], color[1], color[2], brightness)
        if not animated:
            self.left_color = self.left_target_color
            self.right_color = self.right_target_color

    def clear_color(self, animated=True, override=False):
        self.set_color((0, 0, 0), 0.0, animated, override)

    def pulsate(self, fromColor, fromBrightness, toColor, toBrightness, override=False):
        if self.remote and not override:
            self.remote_paint[1] = True
            self.remote_paint[2] = fromColor
            self.remote_paint[3] = fromBrightness
            self.remote_paint[4] = toColor
            self.remote_paint[5] = toBrightness
            return

        self.pulsing = True
        self.pulseDirection = 1
        self.pulse_from_color = fromColor
        self.pulse_from_brightness = fromBrightness
        self.pulse_to_color = toColor
        self.pulse_to_Brightness = toBrightness

    def stop_pulsating(self, override=False):
        if self.remote and not override:
            self.remote_paint[1] = False
            return

        self.pulsing = False

    def begin_remote(self):
        """Hand the LEDs to the host; the state machine paints into the record.

        Safe to call on a button that is already remote -- the record must not
        be reset, or a repaint (the host recolouring a button that stays
        host-owned) would strand whatever the state machine asked for since.
        """
        if self.remote:
            return

        self.remote_paint = [
            self.left_target_color,
            self.pulsing,
            self.pulse_from_color,
            self.pulse_from_brightness,
            self.pulse_to_color,
            self.pulse_to_Brightness,
        ]
        self.remote = True

    def end_remote(self):
        """Take the LEDs back, showing what the state machine last asked for.

        The record is live rather than a snapshot taken when the host took
        over, so a pump that changed mode while badged comes back to the mode
        it is actually in.
        """
        if not self.remote:
            return

        target, pulsing, from_c, from_b, to_c, to_b = self.remote_paint
        self.remote = False
        self.remote_paint = None
        self.stop_pulsating()
        self.set_color((target[0], target[1], target[2]), target[3])
        if pulsing:
            self.pulsate(from_c, from_b, to_c, to_b)

    def is_color_set(self, source_color, source_brightness, colorMargin=10, brightnessMargin=0.01):
        for i in range(2):
            if abs(source_color[i]-self.left_color[i]) > colorMargin:
                return False

        if math.fabs(source_brightness-self.left_color[3]) > brightnessMargin:
            return False

        return True
