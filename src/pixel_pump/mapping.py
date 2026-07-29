"""Control mapping engine -- protocol v2 layer 1, the Pixel Pump 1 half.

``docs/usb-communication.md`` is the contract. The table is keyed by
``(control, gesture, slot)`` and holds ``(action, param)``. The CONNECTED
column applies while the vendor host is active; the device falls back to
STANDALONE the moment the host heartbeat times out, so unplugging a host can
never leave the buttons dead.

Two objects live here:

- ``MappingTable`` is what ``USBManager(mapping=...)`` talks to. It implements
  the contract spelled out in that class' docstring, and owns persistence.
- ``MappingEngine`` is the local half. It turns control events into
  state-machine intents, owns the pump refcount and paints the remote-mode
  LEDs.
"""

import utime

try:
    from micropython import const
except ImportError:

    def const(value):
        return value


from .enums import Brightness, Colors
from .usb.protocol import ControlId, EventKind, MappingSlot


class Gesture:
    TAP = const(1)
    LONG_HOLD = const(2)
    HELD = const(3)
    DELTA_CW = const(4)
    DELTA_CCW = const(5)
    PRESS = const(6)


class Action:
    NONE = const(0x00)
    FORWARD = const(0x01)
    SEND_KEY = const(0x02)
    PUMP_TRIGGER = const(0x10)
    PUMP_TOGGLE = const(0x11)
    VENT_PULSE = const(0x12)
    MODE_LIFT = const(0x20)
    MODE_DROP = const(0x21)
    MODE_REVERSE = const(0x22)
    POWER_LOW = const(0x23)
    POWER_HIGH = const(0x24)
    BRIGHTNESS_MENU = const(0x25)
    POWER_SETTINGS_LOW = const(0x26)
    POWER_SETTINGS_HIGH = const(0x27)


# SEND_KEY carries no modifier byte by design, but PP1's stdin protocol
# configures one for the aux pedal. param 0x00 on FPEDAL_AUX therefore means
# "use the legacy configured key + modifier". GET_MAPPING reports the resolved
# keycode while storage keeps the sentinel, so re-configuring the key over
# stdin keeps working and a host still sees what the pedal actually sends.
LEGACY_KEY = const(0x00)

# Every PP1 control accepts every button/pedal gesture. The encoder gestures
# (DELTA_CW / DELTA_CCW) have no hardware on this model.
GESTURES = (Gesture.PRESS, Gesture.TAP, Gesture.HELD, Gesture.LONG_HOLD)

BUTTON_CONTROLS = (
    ControlId.LIFT,
    ControlId.DROP,
    ControlId.LOW,
    ControlId.HIGH,
    ControlId.REVERSE,
    ControlId.TRIGGER_BTN,
)

CONTROLS = BUTTON_CONTROLS + (ControlId.FPEDAL, ControlId.FPEDAL_AUX)

SLOTS = (MappingSlot.STANDALONE, MappingSlot.CONNECTED)

ACTIONS = (
    Action.NONE,
    Action.FORWARD,
    Action.SEND_KEY,
    Action.PUMP_TRIGGER,
    Action.PUMP_TOGGLE,
    Action.VENT_PULSE,
    Action.MODE_LIFT,
    Action.MODE_DROP,
    Action.MODE_REVERSE,
    Action.POWER_LOW,
    Action.POWER_HIGH,
    Action.BRIGHTNESS_MENU,
    Action.POWER_SETTINGS_LOW,
    Action.POWER_SETTINGS_HIGH,
)

# Classic behaviour in both slots: a freshly updated pump acts exactly like
# legacy firmware until a host writes the CONNECTED column. LIFT/DROP/REVERSE
# fired on press-down and LOW/HIGH on release, hence PRESS vs TAP.
DEFAULTS = {
    (ControlId.LIFT, Gesture.PRESS): (Action.MODE_LIFT, 0),
    (ControlId.LIFT, Gesture.LONG_HOLD): (Action.BRIGHTNESS_MENU, 0),
    (ControlId.DROP, Gesture.PRESS): (Action.MODE_DROP, 0),
    (ControlId.LOW, Gesture.TAP): (Action.POWER_LOW, 0),
    (ControlId.LOW, Gesture.LONG_HOLD): (Action.POWER_SETTINGS_LOW, 0),
    (ControlId.HIGH, Gesture.TAP): (Action.POWER_HIGH, 0),
    (ControlId.HIGH, Gesture.LONG_HOLD): (Action.POWER_SETTINGS_HIGH, 0),
    (ControlId.REVERSE, Gesture.PRESS): (Action.MODE_REVERSE, 0),
    (ControlId.TRIGGER_BTN, Gesture.HELD): (Action.PUMP_TRIGGER, 0),
    (ControlId.FPEDAL, Gesture.HELD): (Action.PUMP_TRIGGER, 0),
    (ControlId.FPEDAL_AUX, Gesture.TAP): (Action.SEND_KEY, LEGACY_KEY),
    (ControlId.FPEDAL_AUX, Gesture.LONG_HOLD): (Action.SEND_KEY, LEGACY_KEY),
}

_NO_ACTION = (Action.NONE, 0)

# Holder id for PUMP_TOGGLE in the pump refcount. 0 is not a valid ControlId,
# so it can never collide with a control holding the trigger.
_TOGGLE_HOLDER = const(0)

VENT_PULSE_DEFAULT_MS = const(500)

REMOTE_COLOR = Colors.PURPLE
REMOTE_BRIGHTNESS = Brightness.DIMMER

FACTORY_RESET_HOLD_MS = const(3000)


class MappingTable:
    """The persisted ``(control, gesture, slot) -> (action, param)`` table.

    Only entries that differ from ``DEFAULTS`` are stored, so ``settings.json``
    stays small and the defaults can be changed by a firmware update without
    stale rows pinning the old behaviour.
    """

    def __init__(self, settings_manager):
        self.settings_manager = settings_manager
        # Bumped on every change so MappingEngine knows to re-evaluate the
        # remote-mode LEDs without polling the whole table.
        self.revision = 0
        self._overrides = {}
        self._load()

    # -- USBManager contract ------------------------------------------------

    def has_control(self, control_id):
        return control_id in CONTROLS

    def has_gesture(self, control_id, gesture):
        return gesture in GESTURES

    def is_valid_action(self, control_id, gesture, action, param):
        if action not in ACTIONS:
            return False
        # PUMP_TRIGGER is momentary -- it needs a release edge to stop the
        # vacuum, so HELD is the only gesture that can carry it.
        if action == Action.PUMP_TRIGGER and gesture != Gesture.HELD:
            return False
        return True

    def get(self, control_id, gesture, slot):
        """Host-facing read: the SEND_KEY sentinel is resolved to a keycode."""
        action, param = self.get_raw(control_id, gesture, slot)
        if action == Action.SEND_KEY and self._is_legacy_key(control_id, param):
            param = self.legacy_key(gesture)[1]
        return (action, param)

    def set(self, control_id, gesture, slot, action, param):
        """RAM only -- ``commit()`` is what reaches flash."""
        key = (control_id, slot, gesture)
        if (action, param) == DEFAULTS.get((control_id, gesture), _NO_ACTION):
            self._overrides.pop(key, None)
        else:
            self._overrides[key] = (action, param)
        self.revision += 1

    def entries(self):
        rows = []
        for control_id in CONTROLS:
            for slot in SLOTS:
                for gesture in GESTURES:
                    action, param = self.get(control_id, gesture, slot)
                    if action != Action.NONE:
                        rows.append((control_id, slot, gesture, action, param))
        return rows

    def reset(self):
        self._overrides = {}
        self.revision += 1
        return self.settings_manager.set_mappings([])

    def commit(self):
        rows = []
        for key in self._overrides:
            control_id, slot, gesture = key
            action, param = self._overrides[key]
            rows.append([control_id, slot, gesture, action, param])
        return self.settings_manager.set_mappings(rows)

    # -- local use ----------------------------------------------------------

    def get_raw(self, control_id, gesture, slot):
        """Read as stored -- the SEND_KEY sentinel stays a sentinel."""
        entry = self._overrides.get((control_id, slot, gesture), None)
        if entry is not None:
            return entry
        return DEFAULTS.get((control_id, gesture), _NO_ACTION)

    def legacy_key(self, gesture):
        """The aux pedal's stdin-configured ``(modifier, keycode)``."""
        settings = self.settings_manager
        if gesture == Gesture.LONG_HOLD:
            return (
                settings.get_secondary_pedal_long_key_modifier(),
                settings.get_secondary_pedal_long_key(),
            )
        return (
            settings.get_secondary_pedal_key_modifier(),
            settings.get_secondary_pedal_key(),
        )

    def send_key_args(self, control_id, gesture, param):
        if self._is_legacy_key(control_id, param):
            return self.legacy_key(gesture)
        # The wire carries no modifier, so a host-supplied keycode has none.
        return (0, param)

    def _is_legacy_key(self, control_id, param):
        return control_id == ControlId.FPEDAL_AUX and param == LEGACY_KEY

    def _load(self):
        rows = self.settings_manager.get_mappings()
        if not isinstance(rows, list):
            return

        for row in rows:
            try:
                control_id, slot, gesture, action, param = row
            except (TypeError, ValueError):
                continue  # corrupt row -- fall back to the default for it
            if not self.has_control(control_id):
                continue
            if slot not in SLOTS:
                continue
            if not self.has_gesture(control_id, gesture):
                continue
            if not self.is_valid_action(control_id, gesture, action, param):
                continue
            self.set(control_id, gesture, slot, action, param)


class MappingEngine:
    """Turns control events into local behaviour, per the resolved slot.

    Also owns the pump refcount: the trigger button and the foot pedal are
    independent controls that both run the vacuum, so the state machine is
    driven off a set of holders rather than off either control directly.
    Without it, "hold pedal, tap button, release button" would stop the pump
    mid-pick.
    """

    def __init__(self, table, state_machine, keyboard, buttons, is_host_active):
        self.table = table
        self.state_machine = state_machine
        self.keyboard = keyboard
        self.buttons = buttons
        self.is_host_active = is_host_active

        self._pump_holders = set()
        self._held = {}
        self._remote_leds = {}
        self._last_slot = None
        self._last_revision = None

    # -- slot resolution ----------------------------------------------------

    def active_slot(self):
        if self.is_host_active():
            return MappingSlot.CONNECTED
        return MappingSlot.STANDALONE

    def resolve(self, control_id, gesture):
        return self.table.get_raw(control_id, gesture, self.active_slot())

    # -- event dispatch -----------------------------------------------------

    def dispatch(self, control_id, event_kind):
        if event_kind == EventKind.PRESS:
            self._fire(control_id, Gesture.PRESS)
            self._press_held(control_id)
        elif event_kind == EventKind.RELEASE:
            self.release_held(control_id)
        elif event_kind == EventKind.TAP:
            self._fire(control_id, Gesture.TAP)
        elif event_kind == EventKind.LONG_HOLD:
            self._fire(control_id, Gesture.LONG_HOLD)

    def hold_pump(self, control_id):
        """Run the vacuum from outside the mapping table, undoably.

        The suspended (settings-menu) path presses the trigger through here
        rather than calling pump_press() directly, so that release_held() can
        undo it whatever happens in between. A menu cancelled with Reverse, or
        timed out by the motor, hands the release back to the mapping path --
        which would otherwise never drop the holder, latching the pump and
        leaving the pedal dead.
        """
        if control_id in self._held:
            return
        self._held[control_id] = (Action.PUMP_TRIGGER, 0)
        self.pump_press(control_id)

    def release_held(self, control_id):
        """Undo whatever HELD started, if anything. Safe to call blind.

        The action is replayed from what was recorded on the press edge, not
        looked up again, so a host remapping the control -- or disconnecting --
        mid-hold can never leave the pump running.
        """
        entry = self._held.pop(control_id, None)
        if entry is None:
            return

        action, param = entry
        if action == Action.PUMP_TRIGGER:
            self.pump_release(control_id)
        elif action == Action.SEND_KEY:
            self.keyboard.release()

    def _press_held(self, control_id):
        action, param = self.resolve(control_id, Gesture.HELD)
        if action == Action.NONE or action == Action.FORWARD:
            return

        self._held[control_id] = (action, param)
        if action == Action.PUMP_TRIGGER:
            self.pump_press(control_id)
        elif action == Action.SEND_KEY:
            # release() clears every key, so two controls held on SEND_KEY at
            # once release together. Only the aux pedal realistically does this.
            modifier, keycode = self.table.send_key_args(
                control_id, Gesture.HELD, param
            )
            self.keyboard.press(modifier, keycode)
        else:
            self._perform(control_id, Gesture.HELD, action, param)

    def _fire(self, control_id, gesture):
        action, param = self.resolve(control_id, gesture)
        self._perform(control_id, gesture, action, param)

    def _perform(self, control_id, gesture, action, param):
        # NONE and FORWARD have no local behaviour by definition -- FORWARD's
        # EVENT frame already went out under the publish-all rule.
        if action == Action.NONE or action == Action.FORWARD:
            return

        state = self.state_machine.state

        if action == Action.MODE_LIFT:
            state.to_lift()
        elif action == Action.MODE_DROP:
            state.to_drop()
        elif action == Action.MODE_REVERSE:
            state.to_reverse()
        elif action == Action.POWER_LOW:
            state.to_power_low()
        elif action == Action.POWER_HIGH:
            state.to_power_high()
        elif action == Action.BRIGHTNESS_MENU:
            state.to_brightness_settings()
        elif action == Action.POWER_SETTINGS_LOW:
            state.to_low_power_settings()
        elif action == Action.POWER_SETTINGS_HIGH:
            state.to_high_power_settings()
        elif action == Action.SEND_KEY:
            modifier, keycode = self.table.send_key_args(control_id, gesture, param)
            self.keyboard.tap(modifier, keycode)
        elif action == Action.PUMP_TOGGLE:
            self._pump_toggle()
        elif action == Action.VENT_PULSE:
            state.vent_pulse(param * 10 if param else VENT_PULSE_DEFAULT_MS)
        # PUMP_TRIGGER only reaches here on a gesture with no release edge,
        # which is_valid_action() rejects. Ignore rather than latch the pump on.

    # -- pump refcount ------------------------------------------------------

    def pump_press(self, holder):
        if holder in self._pump_holders:
            return
        was_idle = not self._pump_holders
        self._pump_holders.add(holder)
        if was_idle:
            self.state_machine.state.trigger_on()

    def pump_release(self, holder):
        if holder not in self._pump_holders:
            return
        self._pump_holders.discard(holder)
        if not self._pump_holders:
            self.state_machine.state.trigger_off()

    def _pump_toggle(self):
        if _TOGGLE_HOLDER in self._pump_holders:
            self.pump_release(_TOGGLE_HOLDER)
        else:
            self.pump_press(_TOGGLE_HOLDER)

    # -- remote-mode LEDs ---------------------------------------------------

    def tick(self):
        slot = self.active_slot()
        revision = self.table.revision
        if slot == self._last_slot and revision == self._last_revision:
            return

        self._last_slot = slot
        self._last_revision = revision
        self._apply_remote_leds(slot)

    def _apply_remote_leds(self, slot):
        for control_id in self.buttons:
            button = self.buttons[control_id]
            remote = self._is_remote(control_id, slot)

            if remote and control_id not in self._remote_leds:
                self._remote_leds[control_id] = (
                    button.left_target_color,
                    button.pulsing,
                    button.pulse_from_color,
                    button.pulse_from_brightness,
                    button.pulse_to_color,
                    button.pulse_to_Brightness,
                )
                button.stop_pulsating()
                button.set_color(REMOTE_COLOR, REMOTE_BRIGHTNESS)
            elif not remote and control_id in self._remote_leds:
                target, pulsing, from_c, from_b, to_c, to_b = self._remote_leds.pop(
                    control_id
                )
                button.set_color((target[0], target[1], target[2]), target[3])
                if pulsing:
                    button.pulsate(from_c, from_b, to_c, to_b)

    def _is_remote(self, control_id, slot):
        # "Remote" means the host owns the button: something on it forwards and
        # nothing on it still acts locally. A button that keeps e.g. its
        # long-press menu is not remote, and showing the colour would lie.
        forwards = False
        for gesture in GESTURES:
            action = self.table.get_raw(control_id, gesture, slot)[0]
            if action == Action.FORWARD:
                forwards = True
            elif action != Action.NONE:
                return False
        return forwards


def check_factory_reset(table, renderer, pins, hold_ms=FACTORY_RESET_HOLD_MS):
    """LIFT + DROP held at power-on restores the default mapping table.

    The escape hatch exists because a host can map every button to FORWARD,
    which leaves a pump with no local way back. Runs before the boot sequence
    and costs nothing unless both pins are already down.
    """
    if not _all_held(pins):
        return False

    started_ms = utime.ticks_ms()
    while utime.ticks_diff(utime.ticks_ms(), started_ms) < hold_ms:
        if not _all_held(pins):
            return False
        utime.sleep_ms(10)

    table.reset()
    _flash_confirm(renderer)
    return True


def _all_held(pins):
    for pin in pins:
        if not pin.value():
            return False
    return True


def _flash_confirm(renderer, times=3, on_ms=120, off_ms=120):
    # The UI timer is not running yet at this point, so flush by hand.
    for _ in range(times):
        for index in range(renderer.led_count):
            renderer.set_led_color(index, Colors.WHITE, Brightness.DEFAULT)
        renderer.flush_frame_buffer()
        utime.sleep_ms(on_ms)
        for index in range(renderer.led_count):
            renderer.set_led_color(index, Colors.NONE, 0.0)
        renderer.flush_frame_buffer()
        utime.sleep_ms(off_ms)
