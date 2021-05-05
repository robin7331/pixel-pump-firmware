from machine import Pin
import utime
import math


class ButtonEvent:
    TOUCH_DOWN = 0
    TOUCH_UP = 1
    TOUCH = 2
    LONG_PRESS = 3


class Button:
    def __init__(self, title, left_led_index, right_led_index, switch_pin, secondary_switch_pin=None, on_button_event=None, on_touch_down=None, on_touch_up=None, on_touch=None, on_long_press=None, on_should_render=None, lerp_speed=0.25):
        self.title = title
        self.pin = Pin(switch_pin, Pin.IN, Pin.PULL_DOWN)
        self.secondary_pin = None
        if secondary_switch_pin:
            self.secondary_pin = Pin(secondary_switch_pin, Pin.IN, Pin.PULL_DOWN)
        self.left_led_index = left_led_index
        self.right_led_index = right_led_index
        self.on_button_event = on_button_event
        self.on_touch_down = on_touch_down
        self.on_touch_up = on_touch_up
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

    def tick(self):

        state = False
        if self.secondary_pin:
            state = self.pin.value() or self.secondary_pin.value()
        else:
            state = self.pin.value()

        if state and self.touch_start > 0 and (utime.ticks_ms()-self.touch_start) > 750:
            self.touch_start = 0
            if self.on_long_press:
                self.on_long_press(self)
            if self.on_button_event:
                self.on_button_event(self, ButtonEvent.LONG_PRESS)

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
            # Pulse to?
            if self.pulseDirection is 1:
                self.set_color(self.pulse_to_color, self.pulse_to_Brightness)
                if self.is_color_set(source_color=self.pulse_to_color, source_brightness=self.pulse_to_Brightness):
                    self.pulseDirection = 2
            elif self.pulseDirection is 2:
                self.set_color(self.pulse_from_color, self.pulse_from_brightness)
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

    def set_color(self, color, brightness, animated=True):
        if not animated:
            self.left_target_color = self.left_color = (
                color[0], color[1], color[2], brightness)
            self.right_target_color = self.right_color = (
                color[0], color[1], color[2], brightness)
            return

        self.left_target_color = (color[0], color[1], color[2], brightness)
        self.right_target_color = (color[0], color[1], color[2], brightness)

    def clear_color(self, animated=True):
        if not animated:
            self.left_target_color = self.left_color = (0, 0, 0, 0.0)
            self.right_target_color = self.right_color = (0, 0, 0, 0.0)
            return

        self.left_target_color = (0, 0, 0, 0.0)
        self.right_target_color = (0, 0, 0, 0.0)

    def pulsate(self, fromColor, fromBrightness, toColor, toBrightness):
        self.pulsing = True
        self.pulseDirection = 1
        self.pulse_from_color = fromColor
        self.pulse_from_brightness = fromBrightness
        self.pulse_to_color = toColor
        self.pulse_to_Brightness = toBrightness

    def stop_pulsating(self):
        self.pulsing = False

    def is_color_set(self, source_color, source_brightness, colorMargin=10, brightnessMargin=0.05):
        for i in range(2):
            if abs(source_color[i]-self.left_color[i]) > colorMargin:
                return False

        if math.fabs(source_brightness-self.left_color[3]) > brightnessMargin:
            return False

        return True
