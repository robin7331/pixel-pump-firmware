"""`keyboard_enabled` -- the setting that changes what the device *is*.

Every other settings key changes how the pump behaves. This one decides whether
the HID keyboard interface is registered at all, which is the only device-side
way to stop macOS' Keyboard Setup Assistant. The interesting failures are
therefore descriptor-level, and none of them show up in a build:

  - the key exists in the dump, `settings:set_keyboard_enabled` echoes a
    normalised `keyboard_enabled:0|1`, the value persists, and the pump does
    *not* reboot -- the documented difference from `settings:persist`
  - after a hard reset the device presents no keyboard collection, per
    hid.enumerate() *and* per `ioreg -c IOHIDDevice`
  - with the keyboard gone the rest of the device is intact: the vendor
    interface still enumerates and answers, the full default mapping table
    still streams, and CDC/REPL still answers `version:info`
  - tapping the aux pedal with no keyboard interface does not wedge the
    firmware, in the one configuration that never had a keyboard to begin with
  - turning it back on restores the interface, and the SEND_KEY sentinel still
    resolves, so nothing needed reconfiguring
  - the aux pedal actually *types* the configured key -- the read path is
    covered by the mapping group, which only proves the table reports a
    keycode, not that the keystroke lands

Two acceptance items are deliberately absent because no script can take them:

  - macOS' Keyboard Setup Assistant staying away on a Mac that has never seen
    the pump. The dialog is driven by a per-host cache, so a machine that has
    already enumerated this pump will not show it whatever the firmware does.
    The enumeration check proves the cause is gone; only a virgin Mac proves
    the effect.
  - anything about how Board Factory reacts to a pump with no keyboard.

Reboots twice (three times if the pump started with the keyboard off), each
over CDC rather than by hand. The pump is left on its original setting.
"""

import os
import select
import subprocess
import sys
import termios
import time
import tty

import hid

from .checks_identity import check_get_info
from .checks_mapping import check_bulk_dump, check_send_key_sentinel
from .firmware import PID, VID, ControlId, MessageType
from .transport import (
    bulk_dump,
    ask,
    describe_interfaces,
    read_heartbeat,
    report,
    unattended,
    vendor_present,
)

GROUP = "keyboard"
TITLE = "keyboard_enabled"
NEEDS_PUMP = True

KEYBOARD_USAGE_PAGE = 0x01
KEYBOARD_USAGE = 0x06  # keyboard, as opposed to keypad

# HID usages 0x04-0x1D are a-z, 0x1E-0x27 are 1-9 then 0. That is the whole
# range the keystroke check can verify by reading the terminal, which is why a
# configured key outside it skips rather than fails.
_DIGITS = "1234567890"


# --------------------------------------------------------------- enumeration


def keyboard_interfaces():
    return [
        d
        for d in hid.enumerate(VID, PID)
        if d["usage_page"] == KEYBOARD_USAGE_PAGE and d["usage"] == KEYBOARD_USAGE
    ]


def ioreg_keyboard_present():
    """Ask the IOKit registry directly. True/False, or None if it could not tell.

    hid.enumerate() is hidapi's view; `ioreg -c IOHIDDevice` is the one the
    acceptance criterion is written against and the one the Keyboard Setup
    Assistant is driven from. They should agree -- checking both is what makes
    a disagreement visible instead of invisible.
    """
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDDevice", "-r", "-l"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - no ioreg is not worth failing the run over
        return None
    if out.returncode != 0:
        return None

    for block in out.stdout.split("+-o "):
        if f'"VendorID" = {VID}' not in block or f'"ProductID" = {PID}' not in block:
            continue
        if '"PrimaryUsagePage" = 1' in block and '"PrimaryUsage" = 6' in block:
            return True
    return False


# --------------------------------------------------------------- checks


def check_echo_and_persist(rig):
    """The CDC contract: echo, persistence, and *no* reboot."""
    name = "set_keyboard_enabled echoes and persists without rebooting"
    problems = []

    settings = rig.cdc.dump()
    if settings is None:
        return report(name, ["settings:dump did not answer"]), None, False

    key_present = "keyboard_enabled" in settings
    baseline = bool(settings.get("keyboard_enabled", True))
    echo = rig.cdc.set_keyboard_enabled(False)

    if not key_present and echo is None:
        # Neither half of the feature is there. That is a firmware that predates
        # the setting, not a bug in one -- and the remaining checks would all
        # fail in ways that read like descriptor problems. Say which it is here,
        # before anyone spends a morning on the enumeration check.
        print("  STOP  This firmware has no keyboard_enabled at all: the key is absent")
        print("        from settings:dump *and* set_keyboard_enabled does not answer.")
        print(f"        version:info -> {rig.cdc.version_info()}")
        print("        Flash the current build and run this again -- nothing below")
        print("        can pass until then.")
        return False, baseline, False

    if not key_present:
        # The command works but the key vanished from storage: migrate_settings()
        # deletes anything missing from DEFAULT_SETTINGS, the same trap `mappings`
        # sits in. That IS the bug this line exists to catch.
        problems.append("keyboard_enabled absent from settings.json -- migrate_settings() ate it")
    print(f"    baseline: keyboard_enabled = {baseline}")

    if echo is None:
        problems.append("no keyboard_enabled: echo -- docs/usb-communication.md requires one")
    elif echo != "keyboard_enabled:0":
        problems.append(f"echoed {echo!r}, expected 'keyboard_enabled:0'")
    else:
        print(f"    set_keyboard_enabled:0 -> {echo}")

    # Any nonzero int is on, and the echo is normalised rather than a parrot.
    raw = rig.cdc.send("settings:set_keyboard_enabled:7")
    normalised = [
        ln.strip() for ln in raw.splitlines() if ln.strip().startswith("keyboard_enabled:")
    ]
    if normalised and normalised[0] != "keyboard_enabled:1":
        problems.append(
            f"7 echoed as {normalised[0]!r}, expected the normalised 'keyboard_enabled:1'"
        )
    elif not normalised:
        problems.append("no echo for a nonzero value")
    else:
        print(f"    set_keyboard_enabled:7 -> {normalised[0]}  (normalised)")

    echo = rig.cdc.set_keyboard_enabled(False)
    if echo != "keyboard_enabled:0":
        problems.append("could not settle on keyboard_enabled:0")

    settings = rig.cdc.dump()
    if settings is None:
        problems.append("settings:dump stopped answering after the write")
    elif settings.get("keyboard_enabled") is not False:
        problems.append(
            f"dump says keyboard_enabled = {settings.get('keyboard_enabled')!r} after writing 0"
        )
    else:
        print("    settings:dump confirms it persisted as false")

    # It must not reboot: hosts write several keys before rebooting themselves.
    if rig.session is not None:
        if read_heartbeat(rig.session, timeout_s=3.0) is None:
            problems.append(
                "pump stopped heartbeating -- set_keyboard_enabled appears to have reset it"
            )
        else:
            print("    pump still heartbeating -- the write did not reboot it")
    if not vendor_present():
        problems.append("vendor interface vanished -- the write re-enumerated the device")

    return report(name, problems), baseline, True


def check_no_keyboard_interface():
    """The acceptance item: no keyboard collection at all."""
    problems = []

    found = keyboard_interfaces()
    if found:
        problems.append(f"hid.enumerate() still lists {len(found)} keyboard interface(s)")
    else:
        print("    hid.enumerate(): no keyboard collection")

    via_ioreg = ioreg_keyboard_present()
    if via_ioreg is None:
        print("    (ioreg unavailable -- checked hidapi's view only)")
    elif via_ioreg:
        problems.append("ioreg -c IOHIDDevice still shows a keyboard collection for this VID/PID")
    else:
        print("    ioreg -c IOHIDDevice: no keyboard collection")

    if not vendor_present():
        problems.append(
            "the vendor interface went missing too -- the device lost more than the keyboard"
        )

    print("    interfaces now present:")
    describe_interfaces()

    return report("keyboard interface absent with keyboard_enabled=0", problems)


def check_rest_of_device(rig):
    """Everything that is not the keyboard still works."""
    name = "vendor HID, mapping table and CDC survive without the keyboard"
    problems = []

    if rig.session is None:
        return report(name, ["no vendor session"]), {}

    print("    (the two checks below belong to the identity and mapping groups,")
    print("     re-run on a device whose interface set just changed underneath them)")
    if not check_get_info(rig.session):
        problems.append("GET_INFO no longer answers correctly")

    entries, terminators = bulk_dump(rig.session)
    ok, bulk = check_bulk_dump(entries, terminators)
    if not ok:
        problems.append("the default mapping table no longer streams intact")

    if rig.cdc is not None and rig.cdc.available:
        info = rig.cdc.version_info()
        if info is None:
            problems.append(
                "CDC/REPL stopped answering version:info -- builtin_driver lost the interface"
            )
        else:
            print(f"    version:info -> {info}")
    else:
        problems.append(
            "no CDC port after the reboot -- with the keyboard gone the CDC "
            "interface is the one most likely to have been renumbered away"
        )

    return report(name, problems), bulk


def check_aux_pedal_quiet(rig):
    """The aux pedal with no keyboard to send to. Needs someone to tap it."""
    name = "aux pedal does not wedge the firmware with no keyboard"
    print("\nAux pedal with the keyboard disabled (needs the pedal plugged in).")
    print("           Nothing should type, and more importantly nothing should hang:")
    print("           issue #29 was a send path that assumed an absent host was harmless,")
    print("           and this is the configuration that never has one.")
    if unattended():
        print(f"  SKIP  {name} (unattended run)")
        return None
    if not ask("Run it?"):
        print(f"  SKIP  {name}")
        return None

    problems = []
    rig.session.start_keepalive()
    print("\n    Tap the secondary (aux) foot pedal once.")
    seen = rig.session.read(
        30.0,
        want=(MessageType.EVENT,),
        match=lambda f: f[3] == ControlId.FPEDAL_AUX,
    )
    if seen is None:
        problems.append(
            "no FPEDAL_AUX event within 30 s -- pedal not connected, or nothing was published"
        )
    else:
        print("    FPEDAL_AUX event received")
        # The failure worth catching is a firmware that stops after the press.
        time.sleep(1.0)
        if read_heartbeat(rig.session, timeout_s=3.0) is None:
            problems.append("no heartbeat after the pedal tap -- the firmware appears to have stalled")
        else:
            print("    still heartbeating a second later -- the send path did not block")

    rig.session.stop_keepalive()
    return report(name, problems)


def check_keyboard_returns(rig, bulk):
    """Turning it back on restores the interface and the configured keys."""
    name = "keyboard interface returns, with the keys unchanged"
    problems = []

    echo = rig.cdc.set_keyboard_enabled(True)
    if echo != "keyboard_enabled:1":
        problems.append(f"echoed {echo!r}, expected 'keyboard_enabled:1'")

    if not rig.reboot():
        return report(name, ["the pump did not come back after reset:hard"]), None

    found = keyboard_interfaces()
    if not found:
        problems.append("no keyboard interface after re-enabling -- the setting is one-way")
    else:
        print(f"    hid.enumerate(): keyboard collection back ({len(found)} interface)")

    via_ioreg = ioreg_keyboard_present()
    if via_ioreg is False:
        problems.append("ioreg still shows no keyboard collection, though hidapi does")
    elif via_ioreg:
        print("    ioreg -c IOHIDDevice: keyboard collection back")

    # Nothing should have needed reconfiguring: the mapping table never changed,
    # so the sentinel must still resolve to the configured keycode.
    entries, terminators = bulk_dump(rig.session)
    ok, fresh = check_bulk_dump(entries, terminators)
    if not ok:
        problems.append("the mapping table did not survive the round trip")
    elif not check_send_key_sentinel(fresh):
        problems.append("the SEND_KEY sentinel no longer resolves")
    elif bulk and fresh != bulk:
        print("    (mapping table differs from the keyboard-off dump -- see above)")

    return report(name, problems), fresh


def _expected_character(settings):
    """The character the aux pedal should type, or (None, reason)."""
    key = settings.get("secondary_pedal_key")
    modifier = settings.get("secondary_pedal_key_modifier", 0)
    if key is None:
        return None, "secondary_pedal_key missing from settings.json"
    if modifier:
        # A ctrl/alt/gui chord is not a character, and reading one in raw mode
        # would hand the terminal an interrupt rather than a keystroke.
        return None, f"a modifier is configured ({modifier:#04x}), so the tap is a chord"
    if 0x04 <= key <= 0x1D:
        return chr(ord("a") + key - 0x04), None
    if 0x1E <= key <= 0x27:
        return _DIGITS[key - 0x1E], None
    return None, f"key {key:#04x} is not a letter or digit, so it cannot be read back"


def _read_keystroke(timeout_s):
    """Whatever arrives on stdin, read raw so a single character needs no Enter."""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    typed = ""
    try:
        tty.setraw(fd)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                continue
            typed += os.read(fd, 16).decode(errors="replace")
            if typed:
                # Let a modifier+key or a repeat land, then stop.
                time.sleep(0.2)
                ready, _, _ = select.select([fd], [], [], 0.0)
                if ready:
                    typed += os.read(fd, 16).decode(errors="replace")
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return typed


def check_keystroke_lands(rig):
    """The keystroke actually arrives.

    The mapping group proves GET_MAPPING *reports* the configured keycode. That
    is the read path; this is the one that matters to a user.
    """
    name = "aux pedal types the configured key"
    print("\nThe aux pedal actually types (needs the pedal, and this Terminal")
    print("           window focused when you tap it).")

    if unattended():
        print(f"  SKIP  {name} (unattended run -- needs a pedal tap into a focused terminal)")
        return None

    settings = rig.cdc.dump() if rig.cdc and rig.cdc.available else None
    if settings is None:
        print(f"  SKIP  {name} (no CDC port to read the configured key from)")
        return None

    expected, reason = _expected_character(settings)
    if expected is None:
        print(f"  SKIP  {name} ({reason})")
        return None

    print(
        f"           settings.json says the tap sends {settings['secondary_pedal_key']:#04x}"
        f" -- the character {expected!r}."
    )
    if not ask("Run it?"):
        print(f"  SKIP  {name}")
        return None

    print(f"\n    Keep this window focused and tap the aux pedal once. ({expected!r} expected)")
    print("    Reading raw keystrokes for 30 s -- do not type anything yourself.")
    # The keepalive keeps beating from its thread throughout, which is what
    # makes a 30 s blocking read safe here.
    rig.session.start_keepalive()
    typed = _read_keystroke(30.0)
    rig.session.stop_keepalive()

    problems = []
    if not typed:
        problems.append(
            "nothing arrived in 30 s -- either the pedal did not fire, the "
            "keystroke went to another window, or the keyboard interface is "
            "enumerated but not sending"
        )
    elif expected not in typed:
        problems.append(f"read {typed!r}, expected to see {expected!r}")
    else:
        print(f"    read {typed!r} -- the keystroke landed")

    return report(name, problems)


def run(rig):
    if not (rig.cdc and rig.cdc.available):
        print(f"  SKIP  every check here writes settings over CDC ({rig.cdc.reason})")
        return [False]

    print("    interfaces at start:")
    describe_interfaces()

    results = []
    baseline = None

    print("\n[The CDC contract]")
    ok, baseline, supported = check_echo_and_persist(rig)
    results.append(ok)
    if not supported:
        return results

    print("\n[Enumeration with the keyboard disabled]")
    if not rig.reboot():
        print("  FAIL  keyboard interface absent: the pump did not come back")
        results.append(False)
        return results
    results.append(check_no_keyboard_interface())

    print("\n[Everything that is not the keyboard]")
    ok, bulk = check_rest_of_device(rig)
    results.append(ok)

    pedal = check_aux_pedal_quiet(rig)
    if pedal is not None:
        results.append(pedal)

    print("\n[Turning it back on]")
    ok, _ = check_keyboard_returns(rig, bulk)
    results.append(ok)

    keystroke = check_keystroke_lands(rig)
    if keystroke is not None:
        results.append(keystroke)

    # Leave the pump the way it was found.
    if baseline is False:
        print("\n    Restoring the original setting (keyboard_enabled was off at the start).")
        rig.cdc.set_keyboard_enabled(False)
        rig.reboot()

    print(f"\n    Pump left with keyboard_enabled = {baseline if baseline is not None else 'on'}.")
    print(
        "    Still open, and not automatable: the Keyboard Setup Assistant must\n"
        "    stay away on a Mac that has NEVER enumerated this pump. The\n"
        "    enumeration check proves the keyboard collection is gone, which is\n"
        "    the cause; the dialog is cache-driven, so this Mac cannot show the effect."
    )
    return results
