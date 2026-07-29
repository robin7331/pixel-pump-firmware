"""Checks that need no pump: does the firmware still mean what the checks assume?

Every other group asserts things about a device. This one asserts things about
`src/`, which is why it runs first and why it can run in CI.

The point is the *default mapping table*. `checks_mapping.py` verifies that a
defaulted pump streams classic Pixel Pump behaviour, and to do that it has to
know what classic behaviour is. That expectation is written out below by hand,
deliberately: importing `mapping.py`'s `DEFAULTS` and comparing the device
against it would only prove the device agrees with the firmware source, which
is not the claim anyone cares about.

The cost of an independent copy is that it can drift. This check is what makes
the drift loud and immediate instead of a puzzling hardware failure six months
later: change `DEFAULTS` in the firmware and the very next run says which rows
moved, without a pump plugged in.

If it fires, one of the two sides is wrong and you have to decide which. A
deliberate behaviour change means updating `EXPECTED_DEFAULTS` here; anything
else means the firmware regressed.
"""

from .firmware import (
    PID,
    VID,
    Action,
    ControlId,
    FirmwareParseError,
    Gesture,
    action_name,
    board_usb_identity,
    control_name,
    firmware,
    gesture_name,
)
from .reporting import report

GROUP = "static"
TITLE = "Source checks (no pump needed)"
NEEDS_PUMP = False

# Classic Pixel Pump 1 behaviour, stated independently of mapping.py.
#
# LIFT/DROP/REVERSE fire on press-down and LOW/HIGH on release, which is why
# they use PRESS and TAP respectively. That split is legacy fidelity, not
# taste -- an updated pump has to feel like the one it replaced.
EXPECTED_DEFAULTS = {
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
    # param 0x00 is the sentinel meaning "the key the stdin protocol configured".
    (ControlId.FPEDAL_AUX, Gesture.TAP): (Action.SEND_KEY, 0x00),
    (ControlId.FPEDAL_AUX, Gesture.LONG_HOLD): (Action.SEND_KEY, 0x00),
}

# Names the hardware checks reference by hand. Renaming one in the firmware
# would break them at import time, in a traceback rather than a verdict.
REQUIRED_ACTIONS = (
    "NONE",
    "FORWARD",
    "SEND_KEY",
    "PUMP_TRIGGER",
    "MODE_LIFT",
    "MODE_DROP",
    "MODE_REVERSE",
)
REQUIRED_GESTURES = ("PRESS", "TAP", "HELD", "LONG_HOLD", "DELTA_CW")


def _row(control, gesture, action, param):
    return (
        f"{control_name(control)}/{gesture_name(gesture)} -> "
        f"{action_name(action)}" + (f"({param:#04x})" if param else "")
    )


def check_defaults_match_firmware():
    """The checker's idea of classic behaviour still matches `mapping.py`."""
    problems = []
    try:
        actual = firmware().defaults
    except FirmwareParseError as exc:
        return report(
            "default mapping table agrees with mapping.py",
            [f"could not parse the firmware's DEFAULTS: {exc}"],
        )

    for key, expected in sorted(EXPECTED_DEFAULTS.items()):
        if key not in actual:
            problems.append(f"firmware dropped {_row(key[0], key[1], *expected)}")
        elif actual[key] != expected:
            problems.append(
                f"{control_name(key[0])}/{gesture_name(key[1])} is "
                f"{action_name(actual[key][0])}({actual[key][1]:#04x}) in the firmware, "
                f"expected {action_name(expected[0])}({expected[1]:#04x})"
            )
    for key in sorted(actual):
        if key not in EXPECTED_DEFAULTS:
            problems.append(f"firmware added {_row(key[0], key[1], *actual[key])}")

    ok = report("default mapping table agrees with mapping.py", problems)
    if ok:
        print(f"        {len(actual)} rows, identical on both sides")
    else:
        print(
            "        One of the two is wrong. A deliberate change means updating\n"
            "        EXPECTED_DEFAULTS in tools/pumpcheck/checks_static.py; anything\n"
            "        else means the firmware regressed."
        )
    return ok


def check_vocabulary():
    """Every Action and Gesture the hardware checks name still exists."""
    problems = []
    try:
        actions = firmware().enum("Action")
        gestures = firmware().enum("Gesture")
    except FirmwareParseError as exc:
        return report("mapping.py still defines the vocabulary checks use", [str(exc)])

    for name in REQUIRED_ACTIONS:
        if name not in actions:
            problems.append(f"Action.{name} is gone")
    for name in REQUIRED_GESTURES:
        if name not in gestures:
            problems.append(f"Gesture.{name} is gone")

    ok = report("mapping.py still defines the vocabulary checks use", problems)
    if ok:
        print(f"        {len(actions)} actions, {len(gestures)} gestures")
    return ok


def check_usb_identity():
    """The VID/PID every check finds the pump by still matches the board file."""
    problems = []
    try:
        declared = board_usb_identity()
    except OSError as exc:
        return report("USB identity agrees with mpconfigboard.h", [f"could not read it: {exc}"])

    for name, ours in (("VID", VID), ("PID", PID)):
        theirs = declared.get(name)
        if theirs is None:
            problems.append(f"mpconfigboard.h declares no MICROPY_HW_USB_{name}")
        elif theirs != ours:
            problems.append(
                f"{name} is {theirs:#06x} in mpconfigboard.h, {ours:#06x} in the checker"
            )

    ok = report("USB identity agrees with mpconfigboard.h", problems)
    if ok:
        print(f"        {VID:#06x}:{PID:#06x}")
    else:
        print(
            "        Every check finds the pump by these, so a mismatch reads as an\n"
            "        unplugged pump. Fix firmware.py, not the board file -- 0x1061 is\n"
            "        what hosts in the field already discover the pump by."
        )
    return ok


def run(rig=None):
    """Returns a list of pass/fail. Takes no rig -- nothing here touches a pump."""
    return [check_defaults_match_firmware(), check_vocabulary(), check_usb_identity()]
