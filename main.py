
from machine import Pin, PWM
from ui_renderer import UIRenderer
from machine import Timer
from button import Button
from valve import Valve
from pixel_pump import PixelPump, PowerMode
from boot_sequence import run_boot_sequence
from motor import Motor
import utime

foot_aux = Pin(19, Pin.IN, Pin.PULL_DOWN)

motor = Motor(motorPin=20)

# The UI Renderer class holds the frame buffer and the PIO state machine
renderer = UIRenderer()


def liftBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_lift()


def liftBtnLongPress(btn):
    global pixel_pump
    pixel_pump.state.to_brightness_settings()


def dropBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_drop()


def reverse_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.to_reverse()


def trigger_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.trigger_on()


def trigger_buttonTouchUp(btn):
    global pixel_pump
    pixel_pump.state.trigger_off()


def on_button_event(btn, event):
    global pixel_pump
    pixel_pump.state.on_button_event(btn, event)


def renderBtn(btn):
    global renderer
    renderer.set_led_color(
        btn.left_led_index, (btn.left_color[0], btn.left_color[1], btn.left_color[2]), btn.left_color[3])
    renderer.set_led_color(
        btn.right_led_index, (btn.right_color[0], btn.right_color[1], btn.right_color[2]), btn.right_color[3])


lift_button = Button(title='Lift',
                     left_led_index=0,
                     right_led_index=1,
                     switch_pin=16,
                     on_button_event=on_button_event,
                     on_touch_down=liftBtnTouchUp,
                     on_long_press=liftBtnLongPress,
                     on_should_render=renderBtn)

drop_button = Button(title='Drop',
                     left_led_index=2,
                     right_led_index=3,
                     switch_pin=13,
                     on_button_event=on_button_event,
                     on_touch_down=dropBtnTouchUp,
                     on_should_render=renderBtn)

low_button = Button(title='Low',
                    left_led_index=4,
                    right_led_index=5,
                    switch_pin=14,
                    on_button_event=on_button_event,
                    on_should_render=renderBtn)

high_button = Button(title='High',
                     left_led_index=6,
                     right_led_index=7,
                     switch_pin=28,
                     on_button_event=on_button_event,
                     on_should_render=renderBtn)

reverse_button = Button(title='Reverse',
                        left_led_index=8,
                        right_led_index=9,
                        switch_pin=27,
                        on_button_event=on_button_event,
                        on_touch_down=reverse_buttonTouchDown,
                        on_should_render=renderBtn)

trigger_button = Button(title='Trigger',
                        left_led_index=10,
                        right_led_index=11,
                        switch_pin=17,
                        secondary_switch_pin=18,
                        on_button_event=on_button_event,
                        on_touch_up=trigger_buttonTouchUp,
                        on_touch_down=trigger_buttonTouchDown,
                        on_should_render=renderBtn)

no_valve = Valve(15)
nc_valve = Valve(22)
three_way_valve = Valve(21)

pixel_pump = PixelPump(motor=motor,
                       ui_renderer=renderer,
                       lift_button=lift_button,
                       drop_button=drop_button,
                       low_button=low_button,
                       high_button=high_button,
                       reverse_button=reverse_button,
                       trigger_button=trigger_button,
                       nc_valve=nc_valve,
                       no_valve=no_valve,
                       three_way_valve=three_way_valve)

# Lets render the buttons at 30 fps
# uiTimer = Timer()
# uiTimer.init(freq=30, mode=Timer.PERIODIC,
#              callback=lambda t: renderer.flush_frame_buffer())

# Lets run a fancy rainbow boot sequence followed by a few relay clicks because we can
# run_boot_sequence(renderer, [no_valve, nc_valve, three_way_valve])

rendered_at = 0

while True:
    lift_button.tick()
    drop_button.tick()
    low_button.tick()
    high_button.tick()
    reverse_button.tick()
    trigger_button.tick()

    no_valve.tick()
    nc_valve.tick()
    three_way_valve.tick()

    motor.tick()

    pixel_pump.tick()

    # Render the UI at 30 FPS.
    if utime.ticks_ms() - rendered_at > 33:
        renderer.flush_frame_buffer()
        rendered_at = utime.ticks_ms()
