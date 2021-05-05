
from machine import Pin, PWM
from ui_renderer import UIRenderer
from machine import Timer
from button import Button
from valve import Valve
from pixel_pump import *
from boot_sequence import run_boot_sequence
from motor import Motor


foot_aux = Pin(19, Pin.IN, Pin.PULL_DOWN)

motor = Motor(motorPin=20)

# The UI Renderer class holds the frame buffer and the PIO state machine
renderer = UIRenderer()


def liftBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_lift()


def dropBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_drop()


def low_buttonTouchUp(btn):
    global pixel_pump
    pixel_pump.set_power_mode(PowerMode.LOW)


def high_buttonTouchUp(btn):
    global pixel_pump
    pixel_pump.set_power_mode(PowerMode.HIGH)


def reverse_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.to_reverse()


def trigger_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.trigger_on()


def trigger_buttonTouchUp(btn):
    global pixel_pump
    pixel_pump.state.trigger_off()


def renderBtn(btn):
    global renderer
    renderer.setLEDColor(
        btn.leftLedIndex, (btn.leftColor[0], btn.leftColor[1], btn.leftColor[2]), btn.leftColor[3])
    renderer.setLEDColor(
        btn.rightLedIndex, (btn.rightColor[0], btn.rightColor[1], btn.rightColor[2]), btn.rightColor[3])


lift_button = Button(title='Lift',
                     leftLedIndex=0,
                     rightLedIndex=1,
                     switchPin=16,
                     onTouchDown=liftBtnTouchUp,
                     onShouldRender=renderBtn)

drop_button = Button(title='Drop',
                     leftLedIndex=2,
                     rightLedIndex=3,
                     switchPin=13,
                     onTouchDown=dropBtnTouchUp,
                     onShouldRender=renderBtn)

low_button = Button(title='Low',
                    leftLedIndex=4,
                    rightLedIndex=5,
                    switchPin=14,
                    onTouchUp=low_buttonTouchUp,
                    onShouldRender=renderBtn)

high_button = Button(title='High',
                     leftLedIndex=6,
                     rightLedIndex=7,
                     switchPin=28,
                     onTouchUp=high_buttonTouchUp,
                     onShouldRender=renderBtn)

reverse_button = Button(title='Reverse',
                        leftLedIndex=8,
                        rightLedIndex=9,
                        switchPin=27,
                        onTouchDown=reverse_buttonTouchDown,
                        onShouldRender=renderBtn)

trigger_button = Button(title='Trigger',
                        leftLedIndex=10,
                        rightLedIndex=11,
                        switchPin=17,
                        secondarySwitchPin=18,
                        onTouchUp=trigger_buttonTouchUp,
                        onTouchDown=trigger_buttonTouchDown,
                        onShouldRender=renderBtn)

no_valve = Valve(15)
nc_valve = Valve(22)
three_way_valve = Valve(21)

pixel_pump = PixelPump(motor=motor,
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
uiTimer = Timer()
uiTimer.init(freq=30, mode=Timer.PERIODIC,
             callback=lambda t: renderer.flushFrameBuffer())

# Lets run a fancy rainbow boot sequence followed by a few relay clicks because we can
# run_boot_sequence(renderer, [noValve, ncValve, threeWayValve])

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
