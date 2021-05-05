
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
    def __init__(self, motor, lift_button, drop_button, low_button, high_button, reverse_button, trigger_button, nc_valve, no_valve, three_way_valve):
        self.motor = motor
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
            self.high_button.setColor(Colors.BLUE, Brightness.DEFAULT)
            self.low_button.clearColor()
        else:
            self.low_button.setColor(Colors.BLUE, Brightness.DEFAULT)
            self.high_button.clearColor()

    def set_direction(self, direction):
        self.direction = direction
        if self.direction is Direction.SUCK:
            self.reverse_button.clearColor()
        else:
            self.reverse_button.setColor((242, 31, 31), Brightness.DEFAULT)

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

    def to_reverse(self):
        pass

    def trigger_on(self):
        pass

    def trigger_off(self):
        pass

    def on_button_event(self, event, button):
        pass

    def tick(self):
        pass


class LiftState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.lift_button.setColor(Colors.BLUE, Brightness.DEFAULT)
        self.device.motor.stop()
        self.device.nc_valve.deactivate()

    def on_exit(self, next_state):
        self.device.lift_button.clearColor()
        self.device.nc_valve.deactivate()

    def to_drop(self):
        self.device.set_state(DropState(self.device))

    def to_reverse(self):
        self.device.set_state(ReverseState(self.device))

    def trigger_on(self):
        self.device.trigger_button.setColor(Colors.GREEN, Brightness.DEFAULT)
        self.device.motor.start()
        self.device.nc_valve.deactivate()

    def trigger_off(self):
        self.device.trigger_button.clearColor()
        self.device.motor.stop()
        self.device.nc_valve.activate()
        self.device.nc_valve.deactivate(500)


class DropState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.drop_button.setColor(Colors.BLUE, Brightness.DEFAULT)
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)
        self.paused = True

    def on_exit(self, next_state):
        self.device.trigger_button.stopPulsating()
        self.device.trigger_button.clearColor()
        self.device.drop_button.clearColor()

    def to_lift(self):
        self.device.set_state(LiftState(self.device))

    def to_drop(self):
        if self.paused:
            self.paused = False
            self.device.motor.start()
            self.device.trigger_button.stopPulsating()

    def to_reverse(self):
        self.device.set_state(ReverseState(self.device))

    def trigger_on(self):
        if self.paused:
            self.paused = False
            self.device.motor.start()
            self.device.trigger_button.stopPulsating()
        else:
            self.device.motor.stop()
            self.device.nc_valve.activate()
            self.device.nc_valve.deactivate(500)

        self.device.trigger_button.clearColor()

    def trigger_off(self):
        self.device.motor.start()
        self.device.trigger_button.setColor(Colors.GREEN, Brightness.DEFAULT)
        self.device.nc_valve.deactivate()


class ReverseState(State):
    def __init__(self, device):
        super().__init__(device)

    def on_enter(self, previous_state):
        self.device.reverse_button.setColor(Colors.RED, Brightness.DEFAULT)
        self.device.trigger_button.pulsate(
            Colors.NONE, Brightness.DEFAULT, Colors.GREEN, Brightness.DEFAULT)

    def on_exit(self, next_state):
        self.device.reverse_button.clearColor()
        self.device.trigger_button.stopPulsating()
        self.device.trigger_button.clearColor()
        self.device.no_valve.deactivate()
        self.device.nc_valve.deactivate()
        self.device.three_way_valve.deactivate()

    def to_lift(self):
        self.device.set_state(LiftState(self.device))

    def to_drop(self):
        self.device.set_state(DropState(self.device))

    def to_reverse(self):
        self.device.reverse_button.clearColor()
        self.device.set_last_state()

    def trigger_on(self):
        self.device.motor.start()
        self.device.trigger_button.stopPulsating()
        self.device.trigger_button.setColor(Colors.GREEN, Brightness.DEFAULT)

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
