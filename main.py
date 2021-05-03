from machine import Pin, PWM
from ui_renderer import UIRenderer
from machine import Timer
from button import Button
from valve import Valve
from boot_sequence import run_boot_sequence

powerMode = 1
operationMode = 1
dropModePause = True
reverse = 0
defaultBrightness = 0.12

noValve = Valve(15)
ncValve = Valve(22)
threeWayValve = Valve(21)

foot = Pin(18, Pin.IN, Pin.PULL_DOWN)
foot_aux = Pin(19, Pin.IN, Pin.PULL_DOWN)

motor = PWM(Pin(20))
motor.freq(10000)
motorDuty = 0

# The UI Renderer class holds the frame buffer and the PIO state machine
renderer = UIRenderer()


def liftBtnTouchUp(btn):
    global operationMode, defaultBrightness
    liftButton.setColor((90, 183, 232), defaultBrightness)
    dropButton.clearColor()
    operationMode = 1
    triggerButton.stopPulsating()
    triggerButton.clearColor()


def dropBtnTouchUp(btn):
    global operationMode, defaultBrightness, dropModePause
    liftButton.clearColor()
    if operationMode != 2:
        operationMode = 2
        dropModePause = True
        dropButton.setColor((90, 183, 232), defaultBrightness)
        triggerButton.pulsate((0,0,0), defaultBrightness, (63, 242, 31), defaultBrightness)


def lowButtonTouchUp(btn):
    global powerMode, defaultBrightness
    powerMode = 1
    lowButton.setColor((90, 183, 232), defaultBrightness)
    highButton.clearColor()


def highButtonTouchUp(btn):
    global powerMode, defaultBrightness
    powerMode = 2
    highButton.setColor((90, 183, 232), defaultBrightness)
    lowButton.clearColor()


def reverseButtonTouchDown(btn):
    global reverse
    if reverse is 1:
        reverseButton.stopPulsating()
        reverseButton.clearColor()
        reverse = 0
        threeWayValve.deactivate()
        ncValve.deactivate()
        noValve.deactivate()


def reverseButtonLongPress(btn):
    global reverse, defaultBrightness
    if reverse is 0:
        reverseButton.setColor((242, 31, 31), defaultBrightness)
        reverse = 1
        threeWayValve.activate()
        ncValve.activate()
        noValve.activate()


def triggerButtonTouchDown(btn):
    global motorDuty, powerMode, defaultBrightness
    triggerButton.setColor((63, 242, 31), defaultBrightness)
    motorDuty = 200 if powerMode == 1 else 255
    if reverse == 0:
        ncValve.deactivate()


def triggerButtonTouchUp(btn):
    global motorDuty
    motorDuty = 0
    if reverse == 0:
        ncValve.activate()
        ncValve.deactivate(500)
    else:
        noValve.deactivate()
        noValve.activate(500)
    triggerButton.clearColor()


def renderBtn(btn):
    global renderer
    renderer.setLEDColor(
        btn.leftLedIndex, (btn.leftColor[0], btn.leftColor[1], btn.leftColor[2]), btn.leftColor[3])
    renderer.setLEDColor(
        btn.rightLedIndex, (btn.rightColor[0], btn.rightColor[1], btn.rightColor[2]), btn.rightColor[3])


liftButton = Button(title='Lift', leftLedIndex=0, rightLedIndex=1,
                    switchPin=16, onTouchDown=liftBtnTouchUp, onShouldRender=renderBtn)
dropButton = Button(title='Drop', leftLedIndex=2, rightLedIndex=3,
                    switchPin=13, onTouchDown=dropBtnTouchUp, onShouldRender=renderBtn)
lowButton = Button(title='Low', leftLedIndex=4, rightLedIndex=5,
                   switchPin=14, onTouchUp=lowButtonTouchUp, onShouldRender=renderBtn)
highButton = Button(title='High', leftLedIndex=6, rightLedIndex=7,
                    switchPin=28, onTouchUp=highButtonTouchUp, onShouldRender=renderBtn)
reverseButton = Button(title='Reverse', leftLedIndex=8, rightLedIndex=9,
                       switchPin=27, onLongPress=reverseButtonLongPress, onTouchDown=reverseButtonTouchDown, onShouldRender=renderBtn)
triggerButton = Button(title='Trigger', leftLedIndex=10, rightLedIndex=11, switchPin=17, secondarySwitchPin=18,
                       onTouchUp=triggerButtonTouchUp, onTouchDown=triggerButtonTouchDown, onShouldRender=renderBtn)

# footPedal = Button(title='Footpedal', leftLedIndex=10, rightLedIndex=11, switchPin=18,
#                    onTouchUp=triggerButtonTouchUp, onTouchDown=triggerButtonTouchDown, onShouldRender=renderBtn)

liftButton.setColor((90, 183, 232), defaultBrightness)
lowButton.setColor((90, 183, 232), defaultBrightness)



# Lets render the buttons at 30 fps
uiTimer = Timer()
uiTimer.init(freq=30, mode=Timer.PERIODIC,
             callback=lambda t: renderer.flushFrameBuffer())

# Lets run a fancy rainbow boot sequence followed by a few relay clicks because we can
run_boot_sequence(renderer, [noValve, ncValve, threeWayValve])

while True:

    motor.duty_u16(motorDuty * motorDuty)

    liftButton.tick()
    dropButton.tick()
    lowButton.tick()
    highButton.tick()
    reverseButton.tick()
    triggerButton.tick()
    # footPedal.tick()

    noValve.tick()
    ncValve.tick()
    threeWayValve.tick()
