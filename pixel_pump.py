from button import ButtonEvent

class PowerMode:
    HIGH = 1
    LOW = 0


class Direction:
    SUCK = 0
    BLOW = 1


class Colors:
    NONE = (0, 0, 0)
    BLUE = (90, 183, 232)
    RED = (242, 31, 31)
    GREEN = (63, 242, 31)


class Brightness:
    DIMMER = 0.08
    DEFAULT = 0.12
    BRIGHTER = 0.5


class PixelPump:
    def __init__(self, motor, ui_renderer, lift_button, drop_button, low_button, high_button, reverse_button, trigger_button, nc_valve, no_valve, three_way_valve):
        self.motor = motor
        self.ui_renderer = ui_renderer
        self.lift_button = lift_button
        self.drop_button = drop_button
        self.low_button = low_button
        self.high_button = high_button
        self.reverse_button = reverse_button
        self.trigger_button = trigger_button
        self.nc_valve = nc_valve
        self.no_valve = no_valve
        self.three_way_valve = three_way_valve
        self.power_mode = None
        self.direction = None
        self.last_state_class = None
        self.state = None

        self.set_state(LiftState(self))
        self.set_power_mode(PowerMode.LOW)
        self.set_direction(Direction.SUCK)

    def in_state(self, state):
        return isinstance(self.state, state)

    def set_state(self, state):
        last_state = self.state
        if last_state:
            self.last_state_class = last_state.__class__
            last_state.on_exit(state)
        self.state = state
        self.state.on_enter(last_state)

    def set_last_state(self):
        if self.last_state_class:
            self.set_state(self.last_state_class(self))

    def set_power_mode(self, power_mode):
        self.power_mode = power_mode
        if self.power_mode is PowerMode.HIGH:
            self.high_button.set_color(Colors.BLUE, Brightness.DEFAULT)
            self.low_button.clear_color()
        else:
            self.low_button.set_color(Colors.BLUE, Brightness.DEFAULT)
            self.high_button.clear_color()

    def set_direction(self, direction):
        self.direction = direction
        if self.direction is Direction.SUCK:
            self.reverse_button.clear_color()
        else:
            self.reverse_button.set_color((242, 31, 31), Brightness.DEFAULT)

    def target_motor_pwm(self):
        if self.power_mode is PowerMode.HIGH:
            return 255
        else:
            return 200

    def tick(self):
        self.motor.set_pwm(self.target_motor_pwm())
        self.state.tick()


class State:
    def __init__(self, device):
        self.device = device

    def on_enter(self, previous_state):
        pass

    def on_exit(self, next_state):
        pass

    def to_lift(self):
        pass

    def to_drop(self):
        pass

    def to_brightness_settings(self):
        pass

    def to_reverse(self):
        pass

    def trigger_on(self):
        pass

    def trigger_off(self):
        pass

    def on_button_event(self, button, event):
        if button is self.device.low_button and event is ButtonEvent.TOUCH_UP:
            self.device.set_power_mode(PowerMode.LOW)
        if button is self.device.high_button and event is ButtonEvent.TOUCH_UP:
            self.device.set_power_mode(PowerMode.HIGH)
        pass

    def tick(self):
        pass


class LiftState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.lift_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.device.motor.stop()
        self.device.nc_valve.deactivate()

    def on_exit(self, next_state):
        self.device.lift_button.clear_color()
        self.device.nc_valve.deactivate()

    def to_drop(self):
        self.device.set_state(DropState(self.device))

    def to_reverse(self):
        self.device.set_state(ReverseState(self.device))

    def to_brightness_settings(self):
        self.device.set_state(BrightnessSettings(self.device))

    def trigger_on(self):
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.motor.start()
        self.device.nc_valve.deactivate()

    def trigger_off(self):
        self.device.trigger_button.clear_color()
        self.device.motor.stop()
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(500)


class DropState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.drop_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)
        self.paused = True

    def on_exit(self, next_state):
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.clear_color()
        self.device.drop_button.clear_color()

    def to_lift(self):
        self.device.set_state(LiftState(self.device))

    def to_drop(self):
        if self.paused:
            self.paused = False
            self.device.motor.start()
            self.device.trigger_button.stop_pulsating()

    def to_reverse(self):
        self.device.set_state(ReverseState(self.device))

    def to_brightness_settings(self):
        self.device.set_state(BrightnessSettings(self.device))

    def trigger_on(self):
        if self.paused:
            self.paused = False
            self.device.motor.start()
            self.device.trigger_button.stop_pulsating()
        else:
            self.device.motor.stop()
            self.device.nc_valve.activate()
            self.device.nc_valve.deactivate(500)

        self.device.trigger_button.clear_color()

    def trigger_off(self):
        self.device.motor.start()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.nc_valve.deactivate()


class ReverseState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.reverse_button.set_color(Colors.RED, Brightness.DEFAULT)
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

    def on_exit(self, next_state):
        self.device.reverse_button.clear_color()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.clear_color()
        self.device.no_valve.deactivate()
        self.device.nc_valve.deactivate()
        self.device.three_way_valve.deactivate()

    def to_lift(self):
        self.device.set_state(LiftState(self.device))

    def to_drop(self):
        self.device.set_state(DropState(self.device))

    def to_reverse(self):
        self.device.reverse_button.clear_color()
        self.device.set_last_state()

    def to_brightness_settings(self):
        self.device.set_state(BrightnessSettings(self.device))

    def trigger_on(self):
        self.device.motor.start()
        self.device.trigger_button.stop_pulsating()
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)

        self.device.three_way_valve.activate()
        self.device.nc_valve.activate(100)
        self.device.no_valve.activate(200)

    def trigger_off(self):
        self.device.motor.stop()
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

        self.device.no_valve.deactivate(0)
        self.device.nc_valve.deactivate(100)
        self.device.three_way_valve.deactivate(200)


class BrightnessSettings(State):
    def __init__(self, device):
        super().__init__(device)
        self.old_brightness_modifier = None
        self.current_brightness_modifier = None

    def on_enter(self, previous_state):
        self.old_brightness_modifier = self.device.ui_renderer.brightness_modifier
        self.current_brightness_modifier = self.device.ui_renderer.brightness_modifier
        self.device.trigger_button.set_color(Colors.GREEN, Brightness.DEFAULT)
        self.device.reverse_button.set_color(Colors.RED, Brightness.DEFAULT)
        self.device.low_button.set_color(Colors.BLUE, Brightness.DEFAULT)
        self.device.high_button.set_color(Colors.BLUE, Brightness.DEFAULT)

    def on_exit(self, next_state):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)

    def on_button_event(self, btn, event):
        if btn is self.device.low_button and event is ButtonEvent.TOUCH_UP:
            self.current_brightness_modifier = self.current_brightness_modifier - 0.1
            if self.current_brightness_modifier < 0:
                self.current_brightness_modifier = 0
            self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier

        if btn is self.device.high_button and event is ButtonEvent.TOUCH_UP:
            self.current_brightness_modifier = self.current_brightness_modifier + 0.1
            if self.current_brightness_modifier > 1.0:
                self.current_brightness_modifier = 1.0
            self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier

    def to_reverse(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)
        self.device.ui_renderer.brightness_modifier = self.old_brightness_modifier
        self.device.set_last_state()

    def trigger_off(self):
        self.device.trigger_button.clear_color()
        self.device.reverse_button.clear_color()
        self.device.set_power_mode(self.device.power_mode)
        self.device.ui_renderer.brightness_modifier = self.current_brightness_modifier
        self.device.set_last_state()

