"""Issue #33 acceptance -- settings.json's `keyboard_enabled`.

The setting landed after the Phase 6 gate closed and nothing about it has been
checked on hardware. It is also the only settings key that changes what the
device *is* at enumeration rather than how it behaves, so the interesting
failures are descriptor-level and none of them show up in a build:

  A. the key exists in the dump, `settings:set_keyboard_enabled` echoes a
     normalised `keyboard_enabled:0|1`, the value persists, and the pump does
     *not* reboot -- the documented difference from `settings:persist`
  B. after a hard reset the device presents no keyboard collection, per
     hid.enumerate() *and* per `ioreg -c IOHIDDevice`
  C. with the keyboard gone the rest of the device is intact: the vendor
     interface still enumerates and answers, the full default mapping table
     still streams, and CDC/REPL still answers `version:info`
  D. tapping the aux pedal with no keyboard interface does not wedge the
     firmware (issue #29's shape, in the one configuration that never had a
     keyboard to begin with) -- optional, needs someone to tap the pedal
  E. turning it back on restores the keyboard interface, and the SEND_KEY
     sentinel still resolves, so nothing needed reconfiguring
  F. the aux pedal actually *types* the configured key -- optional, and the
     one check here that also closes a Phase 4 gap, which only ever proved the
     mapping table reports the keycode, not that the keystroke lands

Two acceptance items are deliberately absent because no script can take them:

  - macOS' Keyboard Setup Assistant staying away on a Mac that has never seen
    the pump. The dialog is driven by a per-host cache, so a machine that has
    already enumerated this pump will not show it whatever the firmware does.
    Check B proves the cause is gone; only a virgin Mac proves the effect.
  - anything about how Board Factory reacts to a pump with no keyboard.

Reboots twice (three times if the pump started with the keyboard off), each
over CDC rather than by hand. The pump is left on its original setting.

`--auto` skips D and F -- everything that needs a person -- so A, B, C and E
can run unattended. They are also skipped automatically when stdin is not a
terminal, since their prompts would raise rather than ask.

CPython, not MicroPython -- it runs on the host, like the other checkers.

    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \
        python tools/issue33_acceptance.py

Needs the vendor HID interface, so it must be launched from Terminal -- macOS
only opens one for a process holding Input Monitoring. If it reports
"exclusive access and device already open" from a Terminal that *does* have it,
check `pgrep -fl pixel-pump-daemon` before touching System Settings.
"""

import json
import os
import select
import subprocess
import sys
import termios
import time
import tty

import hid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase4_wire_check import (  # noqa: E402
    FPEDAL_AUX,
    MSG_EVENT,
    PID,
    VENDOR_USAGE_PAGE,
    VID,
    ModeProbe,
    Session,
    ask,
    bulk_dump,
    check_bulk_dump,
    check_send_key_sentinel,
    open_vendor_interface,
    report,
    warn_if_daemon_running,
)
from phase6_acceptance import check_get_info, read_heartbeat  # noqa: E402

KEYBOARD_USAGE_PAGE = 0x01
KEYBOARD_USAGE = 0x06  # keyboard, as opposed to keypad

# The boot sequence (rainbow sweep + valve clicks) runs before the main loop,
# so give a rebooted pump time to become answerable rather than racing it.
BOOT_SETTLE_S = 2.0
ENUMERATION_TIMEOUT_S = 30.0

# HID usages 0x04-0x1D are a-z, 0x1E-0x27 are 1-9 then 0. That is the whole
# range check F can verify by reading the terminal, which is why a configured
# key outside it skips rather than fails.
_DIGITS = "1234567890"


def unattended():
    """True when nobody can be asked to tap a pedal."""
    return "--auto" in sys.argv[1:] or not sys.stdin.isatty()


# --------------------------------------------------------------- enumeration


def keyboard_interfaces():
    return [
        d
        for d in hid.enumerate(VID, PID)
        if d["usage_page"] == KEYBOARD_USAGE_PAGE and d["usage"] == KEYBOARD_USAGE
    ]


def vendor_present():
    return any(d["usage_page"] == VENDOR_USAGE_PAGE for d in hid.enumerate(VID, PID))


def ioreg_keyboard_present():
    """Ask the IOKit registry directly. Returns True/False, or None if it could not tell.

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


def describe_interfaces():
    for d in sorted(hid.enumerate(VID, PID), key=lambda e: (e["usage_page"], e["usage"])):
        print(
            f"        usage page {d['usage_page']:#06x} usage {d['usage']:#04x}"
            f"  {d.get('product_string') or ''}"
        )


def wait_for_vendor(present, timeout_s=ENUMERATION_TIMEOUT_S):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if vendor_present() == present:
            return True
        time.sleep(0.25)
    return False


# --------------------------------------------------------------- CDC


class CDC(ModeProbe):
    """phase4's CDC probe, plus the commands this check needs.

    Subclassed rather than rewritten so the port discovery, the pyserial
    fallback and `version:info` all stay in one place.
    """

    def send(self, line, wait_s=0.8):
        """Write one command, return whatever came back as raw text."""
        if not self.available:
            return ""
        try:
            self.serial.reset_input_buffer()
            self.serial.write(line.encode() + b"\r\n")
            self.serial.flush()
            time.sleep(wait_s)
            return self.serial.read(self.serial.in_waiting or 1).decode(errors="replace")
        except Exception:  # noqa: BLE001 - a dead port reads the same as a silent one
            return ""

    def dump(self):
        """settings.json as a dict, or None."""
        raw = self.send("settings:dump")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    return None
        return None

    def set_keyboard_enabled(self, enabled):
        """Returns the echoed line, e.g. "keyboard_enabled:0", or None."""
        raw = self.send(f"settings:set_keyboard_enabled:{1 if enabled else 0}")
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("keyboard_enabled:"):
                return line
        return None

    def reset_hard(self):
        """Fire and forget -- the port disappears mid-write."""
        if not self.available:
            return
        try:
            self.serial.write(b"reset:hard\r\n")
            self.serial.flush()
        except Exception:  # noqa: BLE001 - the reset taking the port with it is the point
            pass
        self.close()
        self.serial = None


# --------------------------------------------------------------- rig


class Rig:
    """The vendor session and the CDC port, reopened together across a reboot."""

    def __init__(self):
        self.device = None
        self.session = None
        self.cdc = None

    def open(self):
        self.device = open_vendor_interface()
        self.session = Session(self.device)
        self.session.heartbeat(force=True)
        self.session.idle(1.5)
        self.cdc = CDC()
        return self.cdc.available

    def close(self):
        if self.session is not None:
            self.session.stop_keepalive()
        if self.device is not None:
            try:
                self.device.close()
            except Exception:  # noqa: BLE001
                pass
            self.device = None
        if self.cdc is not None:
            self.cdc.close()
            self.cdc = None

    def reboot(self):
        """reset:hard, then wait the pump back onto the bus. False if it never came back."""
        print("    Rebooting over CDC (reset:hard)...")
        if self.session is not None:
            self.session.stop_keepalive()
        if self.device is not None:
            try:
                self.device.close()
            except Exception:  # noqa: BLE001
                pass
            self.device = None
        self.session = None

        self.cdc.reset_hard()
        self.cdc = None

        # Watch it leave first. If it never visibly leaves, macOS may just be
        # holding the entry -- say so and carry on rather than declaring
        # success on what could be the pre-reset enumeration.
        if not wait_for_vendor(False, timeout_s=10.0):
            print("    (never saw the device leave the bus -- continuing anyway)")
        if not wait_for_vendor(True):
            print(f"    Device did not re-enumerate within {ENUMERATION_TIMEOUT_S:.0f} s.")
            return False

        time.sleep(BOOT_SETTLE_S)
        self.device = open_vendor_interface()
        self.session = Session(self.device)
        self.session.heartbeat(force=True)
        self.session.idle(1.5)
        self.cdc = CDC()
        print("    Back on the bus.")
        return True


# --------------------------------------------------------------- checks


def check_echo_and_persist(rig):
    """A -- the CDC contract: echo, persistence, and *no* reboot."""
    problems = []

    settings = rig.cdc.dump()
    if settings is None:
        return (
            report("A  set_keyboard_enabled echoes and persists", ["settings:dump did not answer"]),
            None,
            False,
        )

    key_present = "keyboard_enabled" in settings
    baseline = bool(settings.get("keyboard_enabled", True))
    echo = rig.cdc.set_keyboard_enabled(False)

    if not key_present and echo is None:
        # Neither half of the feature is there. That is a firmware that predates
        # issue #33, not a bug in one -- and the remaining checks would all fail
        # in ways that read like descriptor problems. Say which it is, here,
        # before anyone spends a morning on check B.
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
    normalised = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("keyboard_enabled:")]
    if normalised and normalised[0] != "keyboard_enabled:1":
        problems.append(f"7 echoed as {normalised[0]!r}, expected the normalised 'keyboard_enabled:1'")
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
            problems.append("pump stopped heartbeating -- set_keyboard_enabled appears to have reset it")
        else:
            print("    pump still heartbeating -- the write did not reboot it")
    if not vendor_present():
        problems.append("vendor interface vanished -- the write re-enumerated the device")

    return (
        report("A  set_keyboard_enabled echoes and persists without rebooting", problems),
        baseline,
        True,
    )


def check_no_keyboard_interface():
    """B -- the acceptance item: no keyboard collection at all."""
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
        problems.append("the vendor interface went missing too -- the device lost more than the keyboard")

    print("    interfaces now present:")
    describe_interfaces()

    return report("B  keyboard interface absent with keyboard_enabled=0", problems)


def check_rest_of_device(rig):
    """C -- everything that is not the keyboard still works."""
    problems = []

    if rig.session is None:
        return report("C  vendor HID, mapping table and CDC survive", ["no vendor session"]), {}

    print("    (the two checks below are phase 4's and phase 6's, re-run on a")
    print("     device whose interface set just changed underneath them)")
    if not check_get_info(rig.session):
        problems.append("GET_INFO no longer answers correctly")

    entries, terminators = bulk_dump(rig.session)
    ok, bulk = check_bulk_dump(entries, terminators)
    if not ok:
        problems.append("the default mapping table no longer streams intact")

    if rig.cdc is not None and rig.cdc.available:
        info = rig.cdc.version_info()
        if info is None:
            problems.append("CDC/REPL stopped answering version:info -- builtin_driver lost the interface")
        else:
            print(f"    version:info -> {info}")
    else:
        problems.append(
            "no CDC port after the reboot -- with the keyboard gone the CDC "
            "interface is the one most likely to have been renumbered away"
        )

    return report("C  vendor HID, mapping table and CDC survive without the keyboard", problems), bulk


def check_aux_pedal_quiet(rig):
    """D -- the aux pedal with no keyboard to send to. Optional: needs a pedal."""
    print("\nCHECK D -- aux pedal with the keyboard disabled (needs the pedal plugged in).")
    print("           Nothing should type, and more importantly nothing should hang:")
    print("           issue #29 was a send path that assumed an absent host was harmless,")
    print("           and this is the configuration that never has one.")
    if unattended():
        print("  SKIP  D  aux pedal with the keyboard disabled (unattended run)")
        return None
    if not ask("Run it?"):
        print("  SKIP  D  aux pedal with the keyboard disabled")
        return None

    problems = []
    rig.session.start_keepalive()
    print("\n    Tap the secondary (aux) foot pedal once.")
    seen = rig.session.read(
        30.0,
        want=(MSG_EVENT,),
        match=lambda f: f[3] == FPEDAL_AUX,
    )
    if seen is None:
        problems.append("no FPEDAL_AUX event within 30 s -- pedal not connected, or nothing was published")
    else:
        print("    FPEDAL_AUX event received")
        # The failure worth catching is a firmware that stops after the press.
        time.sleep(1.0)
        if read_heartbeat(rig.session, timeout_s=3.0) is None:
            problems.append("no heartbeat after the pedal tap -- the firmware appears to have stalled")
        else:
            print("    still heartbeating a second later -- the send path did not block")

    rig.session.stop_keepalive()
    return report("D  aux pedal does not wedge the firmware with no keyboard", problems)


def check_keyboard_returns(rig, bulk):
    """E -- turning it back on restores the interface and the configured keys."""
    problems = []

    echo = rig.cdc.set_keyboard_enabled(True)
    if echo != "keyboard_enabled:1":
        problems.append(f"echoed {echo!r}, expected 'keyboard_enabled:1'")

    if not rig.reboot():
        return report("E  keyboard interface returns", ["the pump did not come back after reset:hard"]), None

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

    return report("E  keyboard interface returns, with the keys unchanged", problems), fresh


def _expected_character(settings):
    """The character the aux pedal should type, or (None, reason)."""
    key = settings.get("secondary_pedal_key")
    modifier = settings.get("secondary_pedal_key_modifier", 0)
    if key is None:
        return None, "secondary_pedal_key missing from settings.json"
    if modifier:
        # A ctrl/alt/gui chord is not a character, and reading one in raw mode
        # would hand the terminal an interrupt rather than a keystroke.
        return None, f"a modifier is configured ({modifier:#04x}), so the tap is a chord, not a character"
    if 0x04 <= key <= 0x1D:
        return chr(ord("a") + key - 0x04), None
    if 0x1E <= key <= 0x27:
        return _DIGITS[key - 0x1E], None
    return None, f"key {key:#04x} is not a letter or digit, so it cannot be read back from the terminal"


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
    """F -- the keystroke actually arrives. Also closes a Phase 4 gap.

    Phase 4's check C proved GET_MAPPING *reports* the configured keycode. That
    is the read path; this is the one that matters to a user, and it has never
    been demonstrated on this firmware.
    """
    print("\nCHECK F -- the aux pedal actually types (needs the pedal, and this")
    print("           Terminal window focused when you tap it).")

    if unattended():
        print("  SKIP  F  keystroke lands (unattended run -- needs a pedal tap into a")
        print("           focused terminal, so it cannot run without one)")
        return None

    settings = rig.cdc.dump() if rig.cdc and rig.cdc.available else None
    if settings is None:
        print("  SKIP  F  keystroke lands (no CDC port to read the configured key from)")
        return None

    expected, reason = _expected_character(settings)
    if expected is None:
        print(f"  SKIP  F  keystroke lands ({reason})")
        return None

    print(f"           settings.json says the tap sends {settings['secondary_pedal_key']:#04x}"
          f" -- the character {expected!r}.")
    if not ask("Run it?"):
        print("  SKIP  F  keystroke lands")
        return None

    print(f"\n    Keep this window focused and tap the aux pedal once. ({expected!r} expected)")
    print("    Reading raw keystrokes for 30 s -- do not type anything yourself.")
    # The keepalive keeps beating from its thread throughout, which is what
    # makes a 30 s blocking read safe here (see phase4_wire_check).
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

    return report("F  aux pedal types the configured key", problems)


# --------------------------------------------------------------- main


def main():
    warn_if_daemon_running()

    rig = Rig()
    if not rig.open():
        print(f"!! No CDC port ({rig.cdc.reason}). Every check here writes settings")
        print("   over it, so there is nothing to run without one.")
        rig.close()
        return 1
    print(f"Opened {rig.device.manufacturer} {rig.device.product}")
    print("    interfaces at start:")
    describe_interfaces()

    print("\nIssue #33 acceptance -- keyboard_enabled\n")
    results = []
    baseline = None
    try:
        print("[Check A] The CDC contract")
        ok, baseline, supported = check_echo_and_persist(rig)
        results.append(ok)
        if not supported:
            return 1

        print("\n[Check B] Enumeration with the keyboard disabled")
        if not rig.reboot():
            print("  FAIL  B  keyboard interface absent: the pump did not come back")
            return 1
        results.append(check_no_keyboard_interface())

        print("\n[Check C] Everything that is not the keyboard")
        ok, bulk = check_rest_of_device(rig)
        results.append(ok)

        pedal = check_aux_pedal_quiet(rig)
        if pedal is not None:
            results.append(pedal)

        print("\n[Check E] Turning it back on")
        ok, _ = check_keyboard_returns(rig, bulk)
        results.append(ok)

        keystroke = check_keystroke_lands(rig)
        if keystroke is not None:
            results.append(keystroke)

        # Leave the pump the way it was found.
        if baseline is False:
            print("\nRestoring the original setting (keyboard_enabled was off when this started).")
            rig.cdc.set_keyboard_enabled(False)
            rig.reboot()
    finally:
        rig.close()

    passed = all(results)
    print("\n" + ("ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED"))
    print(f"Pump left with keyboard_enabled = {baseline if baseline is not None else 'on'}.")
    print(
        "\nStill open, and not automatable: the Keyboard Setup Assistant must stay\n"
        "away on a Mac that has NEVER enumerated this pump. Check B proves the\n"
        "keyboard collection is gone, which is the cause; the dialog is driven by\n"
        "a per-host cache, so this Mac cannot demonstrate the effect."
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
