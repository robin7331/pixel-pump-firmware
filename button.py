from machine import Pin
import utime
import math


class ButtonEvent:
    TOUCH_DOWN = 0
    TOUCH_UP = 1
    TOUCH = 2
    LONG_PRESS = 3


class Button:
    def __init__(self, title, leftLedIndex, rightLedIndex, switchPin, secondarySwitchPin=None, onButtonEvent=None, onTouchDown=None, onTouchUp=None, onTouch=None, onLongPress=None, onShouldRender=None, lerpSpeed=0.25):
        self.title = title
        self.pin = Pin(switchPin, Pin.IN, Pin.PULL_DOWN)
        self.secondaryPin = None
        if secondarySwitchPin:
            self.secondaryPin = Pin(secondarySwitchPin, Pin.IN, Pin.PULL_DOWN)
        self.leftLedIndex = leftLedIndex
        self.rightLedIndex = rightLedIndex
        self.onButtonEvent = onButtonEvent
        self.onTouchDown = onTouchDown
        self.onTouchUp = onTouchUp
        self.onShouldRender = onShouldRender
        self.onLongPress = onLongPress
        self.onTouch = onTouch
        self.pressed = False
        self.lastAnimatedAt = 0
        self.lerpSpeed = lerpSpeed
        self.touchStart = 0
        self.pulsing = False
        self.pulseFromColor = None
        self.pulseFromBrightness = None
        self.pulseToColor = None
        self.pulseToBrightness = None
        self.pulseDirection = 0
        self.leftColor = (0, 0, 0, 0.0)
        self.rightColor = (0, 0, 0, 0.0)
        self.leftTargetColor = self.leftColor
        self.rightTargetColor = self.rightColor

    def tick(self):

        state = False
        if self.secondaryPin:
            state = self.pin.value() or self.secondaryPin.value()
        else:
            state = self.pin.value()

        if state and self.touchStart > 0 and (utime.ticks_ms()-self.touchStart) > 750:
            self.touchStart = 0
            if self.onLongPress:
                self.onLongPress(self)
            if self.onButtonEvent:
                self.onButtonEvent(ButtonEvent.LONG_PRESS)

        if state != self.pressed:
            self.pressed = state
            if self.pressed:
                self.touchStart = utime.ticks_ms()
                if self.onTouchDown:
                    self.onTouchDown(self)
                if self.onButtonEvent:
                    self.onButtonEvent(ButtonEvent.TOUCH_DOWN)
            elif not self.pressed:
                self.touchStart = 0
                if self.onTouchUp:
                    self.onTouchUp(self)
                if self.onButtonEvent:
                    self.onButtonEvent(ButtonEvent.TOUCH_UP)

        if state and self.onTouch:
            self.onTouch(self)
        if self.onButtonEvent:
            self.onButtonEvent(ButtonEvent.TOUCH)

        if self.pulsing:
            # Pulse to?
            if self.pulseDirection is 1:
                self.setColor(self.pulseToColor, self.pulseToBrightness)
                if self.isColorSet(sourceColor=self.pulseToColor, sourceBrightness=self.pulseToBrightness):
                    self.pulseDirection = 2
            elif self.pulseDirection is 2:
                self.setColor(self.pulseFromColor, self.pulseFromBrightness)
                if self.isColorSet(sourceColor=self.pulseFromColor, sourceBrightness=self.pulseFromBrightness):
                    self.pulseDirection = 1

        waitTime = (1000//30) - (utime.ticks_ms() - self.lastAnimatedAt)
        if waitTime <= 0:
            self.lastAnimatedAt = utime.ticks_ms()
            self.__animate()

    def __animate(self):
        self.leftColor = self.__lerpColor(self.leftColor, self.leftTargetColor)
        self.rightColor = self.__lerpColor(
            self.rightColor, self.rightTargetColor)
        if self.onShouldRender:
            self.onShouldRender(self)

    def __lerpColor(self, current, target):
        return (current[0] + int((target[0] - current[0]) * self.lerpSpeed), current[1] + int((target[1] - current[1]) * self.lerpSpeed), current[2] + int((target[2] - current[2]) * self.lerpSpeed), current[3] + (target[3] - current[3]) * self.lerpSpeed)

    def setColor(self, color, brightness, animated=True):
        if not animated:
            self.leftTargetColor = self.leftColor = (
                color[0], color[1], color[2], brightness)
            self.rightTargetColor = self.rightColor = (
                color[0], color[1], color[2], brightness)
            return

        self.leftTargetColor = (color[0], color[1], color[2], brightness)
        self.rightTargetColor = (color[0], color[1], color[2], brightness)

    def clearColor(self, animated=True):
        if not animated:
            self.leftTargetColor = self.leftColor = (0, 0, 0, 0.0)
            self.rightTargetColor = self.rightColor = (0, 0, 0, 0.0)
            return

        self.leftTargetColor = (0, 0, 0, 0.0)
        self.rightTargetColor = (0, 0, 0, 0.0)

    def pulsate(self, fromColor, fromBrightness, toColor, toBrightness):
        self.pulsing = True
        self.pulseDirection = 1
        self.pulseFromColor = fromColor
        self.pulseFromBrightness = fromBrightness
        self.pulseToColor = toColor
        self.pulseToBrightness = toBrightness

    def stopPulsating(self):
        self.pulsing = False

    def isColorSet(self, sourceColor, sourceBrightness, colorMargin=10, brightnessMargin=0.05):
        for i in range(2):
            if abs(sourceColor[i]-self.leftColor[i]) > colorMargin:
                return False

        if math.fabs(sourceBrightness-self.leftColor[3]) > brightnessMargin:
            return False

        return True
