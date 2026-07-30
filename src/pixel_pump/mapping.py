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
  LEDs -- including the per-control appearance a host declares in FORWARD's
  param (spec §Control appearance; ``decode_appearance`` below).
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


class Color:
    """FORWARD's appearance param, low nibble -- see ``decode_appearance``."""

    REMOTE_DEFAULT = const(0x0)
    BLUE = const(0x1)
    RED = const(0x2)
    GREEN = const(0x3)
    WHITE = const(0x4)
    AMBER = const(0x5)
    CYAN = const(0x6)
    OFF = const(0x7)


class Animation:
    """The same param's high nibble. SPIN and RAINBOW are ring-only (PP2)."""

    SOLID = const(0x0)
    PULSE = const(0x1)
    SPIN = const(0x2)
    RAINBOW = const(0x3)


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
# The dim end of a PULSE breath. Same hue, lower alpha, so it reads as
# breathing rather than blinking.
REMOTE_PULSE_BRIGHTNESS = 0.02

# The appearance palette: the param's low nibble indexed into PP1's colours.
# OFF is absent -- it means "show nothing", which is a dark button, not a
# colour. REMOTE_DEFAULT is the classic purple remote badge.
APPEARANCE_COLORS = {
    Color.REMOTE_DEFAULT: REMOTE_COLOR,
    Color.BLUE: Colors.BLUE,
    Color.RED: Colors.RED,
    Color.GREEN: Colors.GREEN,
    Color.WHITE: Colors.WHITE,
    Color.AMBER: Colors.AMBER,
    Color.CYAN: Colors.CYAN,
}

# A control's appearance is the first non-zero param among its FORWARD cells
# scanned in *gesture-id* order, which is not the order GESTURES lists them in.
APPEARANCE_SCAN = (Gesture.TAP, Gesture.LONG_HOLD, Gesture.HELD, Gesture.PRESS)

FACTORY_RESET_HOLD_MS = const(3000)


def decode_appearance(param):
    """``(animation, color)`` from a FORWARD param, degraded to what PP1 shows.

    Reserved and unsupported values render as the nearest supported thing and
    never error -- that is what lets the appearance catalog grow past this
    firmware. PP1 has no ring, so SPIN and RAINBOW arrive here and leave as
    SOLID.
    """
    animation = (param >> 4) & 0x0F
    color = param & 0x0F
    if animation != Animation.PULSE:
        animation = Animation.SOLID
    if color not in APPEARANCE_COLORS and color != Color.OFF:
        color = Color.REMOTE_DEFAULT
    return (animation, color)


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
        # FORWARD's param is the control appearance (decode_appearance) and is
        # deliberately not validated: reserved values degrade at render time,
        # never error, so the appearance catalog can outgrow this firmware.
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
        self._last_suspended = None

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
        suspended = self.state_machine.state.suspends_mapping
        if (slot == self._last_slot
                and revision == self._last_revision
                and suspended == self._last_suspended):
            return

        self._last_slot = slot
        self._last_revision = revision
        self._last_suspended = suspended
        self._apply_remote_leds(slot, suspended)

    def _apply_remote_leds(self, slot, suspended):
        for control_id in self.buttons:
            button = self.buttons[control_id]
            # Nothing is host-owned while a settings menu is up: the engine is
            # suspended, the buttons act the legacy way and the menu paints its
            # own feedback on them. The badge returns when the menu exits.
            if suspended:
                appearance = None
            else:
                appearance = self._remote_appearance(control_id, slot)
            current = self._remote_leds.get(control_id, None)

            if appearance is None:
                if current is not None:
                    del self._remote_leds[control_id]
                    button.end_remote()
            elif current is None:
                self._remote_leds[control_id] = appearance
                # From here the state machine paints into the button's record
                # rather than onto the LEDs, so a mode change can no longer
                # take a host-owned button away from the host (issue #38).
                button.begin_remote()
                self._render_appearance(button, appearance)
            elif appearance != current:
                # A host recoloured a button that is already remote. Repaint
                # only -- begin_remote() has already happened, and the record
                # underneath keeps tracking the state machine.
                self._remote_leds[control_id] = appearance
                self._render_appearance(button, appearance)

    def _remote_appearance(self, control_id, slot):
        """The button's appearance in ``slot``, or None to leave it alone.

        A fully host-owned button (something on it forwards, nothing on it
        still acts locally) always renders: all-zero params mean the classic
        REMOTE_DEFAULT badge. A partly-local button renders only an explicit
        appearance -- a non-zero param on one of its FORWARD cells
        (pixel-pump-two-firmware#11); with all params zero it keeps the
        state machine's own colour, so the classic badge never claims a
        button the host does not fully own.

        The appearance is the first non-zero param among the FORWARD cells,
        scanned in gesture-id order; local cells never contribute (their
        params mean other things) and never stop the scan. Zero means "no
        preference", so an implicit FORWARD never masks one a host wrote an
        appearance into.
        """
        forwards = False
        local = False
        param = 0
        for gesture in APPEARANCE_SCAN:
            action, cell_param = self.table.get_raw(control_id, gesture, slot)
            if action == Action.FORWARD:
                forwards = True
                if param == 0:
                    param = cell_param
            elif action != Action.NONE:
                local = True
        if not forwards:
            return None
        if local and param == 0:
            return None
        return decode_appearance(param)

    def _render_appearance(self, button, appearance):
        # Brightness stays device-owned: the host picks colour and animation,
        # and the user's global brightness setting still multiplies on top.
        # override, because this *is* the host's paint -- begin_remote() has
        # already closed the button to everything else.
        animation, color = appearance
        button.stop_pulsating(override=True)
        if color == Color.OFF:
            button.clear_color(override=True)
            return
        rgb = APPEARANCE_COLORS[color]
        if animation == Animation.PULSE:
            button.pulsate(rgb, REMOTE_PULSE_BRIGHTNESS, rgb, REMOTE_BRIGHTNESS,
                           override=True)
        else:
            button.set_color(rgb, REMOTE_BRIGHTNESS, override=True)


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
