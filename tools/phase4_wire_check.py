"""Phase 4 wire check for the Pixel Pump 1 mapping engine.

Covers what the Phase 4 gate left open (see
docs/plans/issue-30-micropython-1.28-protocol-v2.md) -- everything the mapping
engine is actually for, none of which Phase 3's check touches:

  A. bulk GET_MAPPING streams the PP1 default table and terminates once
  B. single GET_MAPPING agrees with the bulk dump, and addresses slots
  C. the FPEDAL_AUX SEND_KEY sentinel is resolved on read, not reported as 0x00
  D. the error paths: BAD_CONTROL, BAD_GESTURE (bad gesture *and* bad slot),
     BAD_ACTION (unknown, and valid-but-wrong-gesture), BAD_MAGIC
  E. SET_MAPPING is live in RAM and slot-scoped; COMMIT and RESET both ACK,
     and RESET restores the defaults
  F. slot switching and the remote-mode LED, including the instant STANDALONE
     fallback when the host heartbeat lapses
  G. the factory reset gesture (optional -- needs a power cycle)

Checks A-E are automatic. F needs someone to look at the pump and press a
button; the *verdict* is still taken from the device rather than from a yes/no
answer, by reading `mode` back over the CDC port -- a FORWARD button that
still switches modes is the failure this exists to catch, and it is not
visible on the vendor interface, since publish-all reports the press either
way.

CPython, not MicroPython -- it runs on the host, like generateVersionFile.py.

    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \
        python tools/phase4_wire_check.py

Needs hidapi (`brew install hidapi`), and must be launched from Terminal:
macOS only opens a vendor HID interface for a process holding Input Monitoring.
If it reports "exclusive access and device already open" from a Terminal that
*does* have it, something else holds the interface -- almost always Board
Factory's pixel-pump-daemon. Check `pgrep -fl pixel-pump-daemon` first.

The pump is left on the default table: the run ends with RESET_MAPPINGS.
"""

import glob
import subprocess
import sys
import threading
import time

import hid

VID = 0x2E8A
PID = 0x1061
VENDOR_USAGE_PAGE = 0xFF00
REPORT_SIZE = 8

MSG_EVENT = 1
MSG_COMMAND = 2
MSG_ACK = 3
MSG_PING = 4
MSG_MAPPING = 7
MSG_ERROR = 255

CMD_GET_MAPPING = 4
CMD_SET_MAPPING = 5
CMD_RESET_MAPPINGS = 6
CMD_COMMIT_MAPPINGS = 7

MAPPING_ALL = 0xFF
RESET_MAGIC = 0xDEFA

ERR_BAD_CONTROL = 3
ERR_BAD_GESTURE = 4
ERR_BAD_ACTION = 5
ERROR_NAMES = {
    1: "UNKNOWN_COMMAND",
    2: "BAD_MAGIC",
    3: "BAD_CONTROL",
    4: "BAD_GESTURE",
    5: "BAD_ACTION",
    6: "STORAGE_ERROR",
}
ERR_BAD_MAGIC = 2

# Controls
FPEDAL, FPEDAL_AUX = 7, 8
LIFT, DROP, LOW, HIGH, REVERSE, TRIGGER_BTN = 10, 11, 12, 13, 14, 15
ENCODER = 5  # a real ControlId, but PP2-only -- must be rejected here

CONTROL_NAMES = {
    7: "FPEDAL",
    8: "FPEDAL_AUX",
    10: "LIFT",
    11: "DROP",
    12: "LOW",
    13: "HIGH",
    14: "REVERSE",
    15: "TRIGGER_BTN",
}

# Gestures (mapping keys -- not the wire EventKinds)
G_TAP, G_LONG_HOLD, G_HELD, G_DELTA_CW, G_PRESS = 1, 2, 3, 4, 6
GESTURE_NAMES = {1: "TAP", 2: "LONG_HOLD", 3: "HELD", 4: "DELTA_CW", 6: "PRESS"}

# Actions
A_NONE, A_FORWARD, A_SEND_KEY = 0x00, 0x01, 0x02
A_PUMP_TRIGGER = 0x10
A_MODE_LIFT, A_MODE_DROP, A_MODE_REVERSE = 0x20, 0x21, 0x22
A_POWER_LOW, A_POWER_HIGH = 0x23, 0x24
A_BRIGHTNESS_MENU = 0x25
A_POWER_SETTINGS_LOW, A_POWER_SETTINGS_HIGH = 0x26, 0x27
ACTION_NAMES = {
    0x00: "NONE",
    0x01: "FORWARD",
    0x02: "SEND_KEY",
    0x10: "PUMP_TRIGGER",
    0x11: "PUMP_TOGGLE",
    0x12: "VENT_PULSE",
    0x20: "MODE_LIFT",
    0x21: "MODE_DROP",
    0x22: "MODE_REVERSE",
    0x23: "POWER_LOW",
    0x24: "POWER_HIGH",
    0x25: "BRIGHTNESS_MENU",
    0x26: "POWER_SETTINGS_LOW",
    0x27: "POWER_SETTINGS_HIGH",
}

STANDALONE, CONNECTED = 0, 1

# Classic PP1 behaviour, expected in *both* slots on a defaulted pump.
DEFAULT_TABLE = {
    (LIFT, G_PRESS): A_MODE_LIFT,
    (LIFT, G_LONG_HOLD): A_BRIGHTNESS_MENU,
    (DROP, G_PRESS): A_MODE_DROP,
    (LOW, G_TAP): A_POWER_LOW,
    (LOW, G_LONG_HOLD): A_POWER_SETTINGS_LOW,
    (HIGH, G_TAP): A_POWER_HIGH,
    (HIGH, G_LONG_HOLD): A_POWER_SETTINGS_HIGH,
    (REVERSE, G_PRESS): A_MODE_REVERSE,
    (TRIGGER_BTN, G_HELD): A_PUMP_TRIGGER,
    (FPEDAL, G_HELD): A_PUMP_TRIGGER,
    (FPEDAL_AUX, G_TAP): A_SEND_KEY,
    (FPEDAL_AUX, G_LONG_HOLD): A_SEND_KEY,
}

HEARTBEAT_INTERVAL_S = 0.4
HOST_TIMEOUT_S = 1.2  # device drops to STANDALONE this long after the last write


# --------------------------------------------------------------- transport


def warn_if_daemon_running():
    """Board Factory's daemon holds the interface, and macOS blames permissions.

    hidapi reports a held interface and a missing Input Monitoring grant with
    the same message, which sends you to System Settings for a problem that is
    not there. Say so up front instead.
    """
    try:
        found = subprocess.run(
            ["pgrep", "-fl", "pixel-pump-daemon"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - pgrep missing is not worth failing over
        return
    if found.returncode != 0 or not found.stdout.strip():
        return
    pid = found.stdout.split(None, 1)[0]
    print(
        f"!! Board Factory's pixel-pump-daemon is running (pid {pid}) and holds the\n"
        "   vendor interface exclusively. Quit Board Factory, or kill that pid,\n"
        "   before running this. Opening the device is about to fail with\n"
        "   'exclusive access and device already open' -- which is NOT a\n"
        "   permissions problem, whatever the message suggests.\n"
    )


def open_vendor_interface():
    paths = [d for d in hid.enumerate(VID, PID) if d["usage_page"] == VENDOR_USAGE_PAGE]
    if not paths:
        sys.exit(
            "No Pixel Pump vendor HID interface found "
            f"({VID:#06x}:{PID:#06x}, usage page {VENDOR_USAGE_PAGE:#06x}). "
            "Is the pump plugged in and running v2 firmware?"
        )
    try:
        return hid.Device(path=paths[0]["path"])
    except hid.HIDException as exc:
        sys.exit(
            f"Could not open the vendor interface: {exc}\n\n"
            "'exclusive access and device already open' has two causes and macOS\n"
            "reports both identically:\n"
            "  1. something else holds the interface -- check\n"
            "       pgrep -fl pixel-pump-daemon\n"
            "     and quit Board Factory if it is running;\n"
            "  2. this process lacks Input Monitoring -- run it from Terminal.app\n"
            "     and grant that under System Settings > Privacy & Security."
        )


def write_frame(device, frame):
    # hidapi wants a leading report-ID byte even though this interface uses none.
    device.write(bytes((0,)) + frame)


def normalize(data):
    b = bytes(data)
    if len(b) == REPORT_SIZE + 1 and b[0] == 0:
        b = b[1:]
    return b[:REPORT_SIZE] if len(b) >= REPORT_SIZE else None


class Session:
    """Vendor HID transport that keeps the host heartbeat alive.

    The heartbeat can run on a background thread, which matters more than it
    sounds: the device drops to STANDALONE 1200 ms after the last host write,
    and any prompt this tool puts on screen blocks for far longer than that.
    Beating only from the main loop meant every interactive check silently
    measured a disconnected pump.
    """

    def __init__(self, device):
        self.device = device
        self.seq = 0
        self.next_heartbeat = 0.0
        self._lock = threading.RLock()
        self._keepalive_stop = None
        self._keepalive_thread = None

    def heartbeat(self, force=False):
        with self._lock:
            if not force and time.monotonic() < self.next_heartbeat:
                return
            stamp = int(time.monotonic() * 1000) & 0xFFFF
            write_frame(
                self.device,
                bytes(
                    (2, MSG_PING, self.seq, 0, 0, stamp & 0xFF, (stamp >> 8) & 0xFF, 0x80)
                ),
            )
            self.seq = (self.seq + 1) & 0xFF
            self.next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL_S

    def start_keepalive(self):
        """Hold the host active across blocking prompts."""
        if self._keepalive_thread is not None:
            return
        self.heartbeat(force=True)
        self._keepalive_stop = threading.Event()

        def run():
            while not self._keepalive_stop.wait(0.1):
                try:
                    self.heartbeat()
                except Exception:  # noqa: BLE001 - device went away; just stop
                    return

        self._keepalive_thread = threading.Thread(target=run, daemon=True)
        self._keepalive_thread.start()

    def stop_keepalive(self):
        """Let the host time out on purpose."""
        if self._keepalive_thread is None:
            return
        self._keepalive_stop.set()
        self._keepalive_thread.join(timeout=1.0)
        self._keepalive_thread = None

    def command(self, command_id, b4=0, b5=0, b6=0, b7=0):
        with self._lock:
            write_frame(
                self.device,
                bytes((2, MSG_COMMAND, self.seq, command_id, b4, b5, b6, b7)),
            )
            self.seq = (self.seq + 1) & 0xFF

    def read(self, timeout_s, want=None, beat=True, match=None):
        """Next frame whose msg_type is in `want` and that `match` accepts."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if beat:
                self.heartbeat()
            try:
                with self._lock:
                    data = self.device.read(REPORT_SIZE + 1, 50)
            except hid.HIDException as exc:
                print(f"  read failed: {exc}")
                return None
            frame = normalize(data) if data else None
            if not frame:
                continue
            if want is not None and frame[1] not in want:
                continue
            if match is not None and not match(frame):
                continue
            return frame
        return None

    def await_press(self, control, timeout_s=30.0):
        """Wait for an EVENT PRESS on `control`.

        Doubles as proof that the device considered the host active when the
        button went down -- publish-all only emits while it does. Without that,
        a check on a FORWARDed button cannot tell "the mapping was respected"
        from "the connection had lapsed".
        """
        return self.read(
            timeout_s,
            want=(MSG_EVENT,),
            match=lambda f: f[3] == control and f[4] == 1,  # EventKind.PRESS
        )

    def idle(self, seconds, beat=True):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if beat:
                self.heartbeat()
            try:
                with self._lock:
                    self.device.read(REPORT_SIZE + 1, 50)
            except hid.HIDException:
                return


def get_mapping(session, control, slot, gesture, timeout_s=2.0):
    session.command(CMD_GET_MAPPING, b4=control, b5=(slot << 4) | gesture)
    return session.read(timeout_s, want=(MSG_MAPPING, MSG_ERROR))


def set_mapping(session, control, slot, gesture, action, param=0, timeout_s=2.0):
    session.command(
        CMD_SET_MAPPING,
        b4=control,
        b5=(slot << 4) | gesture,
        b6=action,
        b7=param,
    )
    return session.read(timeout_s, want=(MSG_ACK, MSG_ERROR))


def bulk_dump(session, timeout_s=6.0):
    """Every non-NONE entry, as (control, slot, gesture, action, param)."""
    session.command(CMD_GET_MAPPING, b4=MAPPING_ALL)
    entries = []
    terminators = 0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = session.read(0.5, want=(MSG_MAPPING, MSG_ERROR))
        if frame is None:
            break
        if frame[1] == MSG_ERROR:
            return None, 0
        if frame[3] == MAPPING_ALL:
            terminators += 1
            # Keep reading briefly: a second terminator is a bug worth catching.
            deadline = min(deadline, time.monotonic() + 0.5)
            continue
        entries.append((frame[3], (frame[4] >> 4) & 0x0F, frame[4] & 0x0F, frame[5], frame[6]))
    return entries, terminators


# --------------------------------------------------------------- CDC probe


class ModeProbe:
    """Reads `mode` out of settings.json over the CDC port.

    The vendor interface cannot answer "did the button act locally" -- under
    publish-all the EVENT frame goes out whether the action ran or not. This
    can.
    """

    MODES = {0: "Lift", 1: "Drop", 2: "Reverse"}

    def __init__(self):
        self.serial = None
        self.reason = None
        try:
            import serial  # noqa: F401
        except ImportError:
            self.reason = "pyserial not installed (add --with pyserial)"
            return
        self.reason = self._connect()

    def _connect(self):
        import serial

        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        if not ports:
            return "no /dev/cu.usbmodem* port found"
        try:
            self.serial = serial.Serial(ports[0], 115200, timeout=1.5)
            time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001 - any open failure is the same to us
            return f"could not open {ports[0]}: {exc}"
        return None

    @property
    def available(self):
        return self.serial is not None

    def mode(self):
        if not self.available:
            return None
        import json

        try:
            self.serial.reset_input_buffer()
            self.serial.write(b"settings:dump\r\n")
            self.serial.flush()
            time.sleep(0.8)
            raw = self.serial.read(self.serial.in_waiting or 1).decode(errors="replace")
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    return json.loads(line).get("mode")
        except Exception:  # noqa: BLE001
            return None
        return None

    def close(self):
        if self.serial:
            self.serial.close()


# --------------------------------------------------------------- reporting


def report(name, problems):
    if problems:
        print(f"  FAIL  {name}: " + "; ".join(problems))
        return False
    print(f"  PASS  {name}")
    return True


def describe(entry):
    control, slot, gesture, action, param = entry
    return (
        f"{CONTROL_NAMES.get(control, control)} "
        f"{'CONNECTED' if slot else 'STANDALONE'} "
        f"{GESTURE_NAMES.get(gesture, gesture)} -> "
        f"{ACTION_NAMES.get(action, hex(action))}"
        + (f"({param:#04x})" if param else "")
    )


def ask(question):
    while True:
        answer = input(f"    {question} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


# --------------------------------------------------------------- checks


def check_bulk_dump(entries, terminators):
    problems = []
    if entries is None:
        return report("A  bulk GET_MAPPING streams the default table", ["got an ERROR frame"]), {}

    got = {(c, s, g): (a, p) for c, s, g, a, p in entries}
    expected = {
        (control, slot, gesture): action
        for (control, gesture), action in DEFAULT_TABLE.items()
        for slot in (STANDALONE, CONNECTED)
    }

    for key, action in sorted(expected.items()):
        if key not in got:
            problems.append(f"missing {describe((key[0], key[1], key[2], action, 0))}")
        elif got[key][0] != action:
            problems.append(
                f"{CONTROL_NAMES.get(key[0], key[0])}/{GESTURE_NAMES.get(key[2], key[2])}"
                f" slot {key[1]} is {ACTION_NAMES.get(got[key][0], hex(got[key][0]))},"
                f" expected {ACTION_NAMES[action]}"
            )
    for key in sorted(got):
        if key not in expected:
            problems.append(f"unexpected entry {describe((key[0], key[1], key[2]) + got[key])}")

    if terminators == 0:
        problems.append("dump never terminated (no control 0xFF frame)")
    elif terminators > 1:
        problems.append(f"{terminators} terminator frames, expected 1")

    ok = report("A  bulk GET_MAPPING streams the default table", problems)
    if ok:
        print(f"        {len(entries)} entries, terminated once")
    return ok, got


def check_single(session, bulk):
    problems = []
    frame = get_mapping(session, LIFT, STANDALONE, G_PRESS)
    if frame is None or frame[1] != MSG_MAPPING:
        problems.append("no MAPPING frame for LIFT/STANDALONE/PRESS")
    else:
        entry = (frame[3], (frame[4] >> 4) & 0x0F, frame[4] & 0x0F, frame[5], frame[6])
        if entry[:3] != (LIFT, STANDALONE, G_PRESS):
            problems.append(f"echoed the wrong target: {describe(entry)}")
        if entry[3] != A_MODE_LIFT:
            problems.append(f"action is {ACTION_NAMES.get(entry[3], hex(entry[3]))}")
        if bulk and bulk.get((LIFT, STANDALONE, G_PRESS), (None,))[0] != entry[3]:
            problems.append("disagrees with the bulk dump")

    # A cell the default table leaves empty must read back as NONE, not absent.
    frame = get_mapping(session, LIFT, CONNECTED, G_HELD)
    if frame is None or frame[1] != MSG_MAPPING:
        problems.append("no MAPPING frame for an unmapped cell")
    elif frame[5] != A_NONE:
        problems.append(f"unmapped cell reads {ACTION_NAMES.get(frame[5], hex(frame[5]))}")

    return report("B  single GET_MAPPING addresses cells and slots", problems)


def check_send_key_sentinel(bulk):
    problems = []
    for gesture, label in ((G_TAP, "TAP"), (G_LONG_HOLD, "LONG_HOLD")):
        entry = bulk.get((FPEDAL_AUX, STANDALONE, gesture))
        if entry is None:
            problems.append(f"FPEDAL_AUX {label} missing from the dump")
            continue
        action, param = entry
        if action != A_SEND_KEY:
            problems.append(f"FPEDAL_AUX {label} is not SEND_KEY")
        elif param == 0x00:
            problems.append(
                f"FPEDAL_AUX {label} reports param 0x00 -- the sentinel leaked "
                "instead of being resolved to the configured keycode"
            )
    ok = report("C  SEND_KEY sentinel resolves on read", problems)
    if ok:
        tap = bulk[(FPEDAL_AUX, STANDALONE, G_TAP)][1]
        hold = bulk[(FPEDAL_AUX, STANDALONE, G_LONG_HOLD)][1]
        print(f"        aux pedal sends {tap:#04x} on tap, {hold:#04x} on long hold")
    return ok


def expect_error(frame, expected, label):
    if frame is None:
        return f"{label}: no response"
    if frame[1] != MSG_ERROR:
        return f"{label}: got msg_type {frame[1]}, expected ERROR"
    code = frame[5] | (frame[6] << 8)
    if code != expected:
        return (
            f"{label}: {ERROR_NAMES.get(code, code)}, "
            f"expected {ERROR_NAMES.get(expected, expected)}"
        )
    return None


def check_errors(session):
    problems = []

    problems.append(
        expect_error(
            get_mapping(session, ENCODER, STANDALONE, G_TAP),
            ERR_BAD_CONTROL,
            "ENCODER (a PP2 control)",
        )
    )
    problems.append(
        expect_error(
            get_mapping(session, LIFT, STANDALONE, G_DELTA_CW),
            ERR_BAD_GESTURE,
            "DELTA_CW on a button",
        )
    )
    # Slot rides in the gesture byte and ErrorCode has no BAD_SLOT, so an
    # out-of-range slot is specified to report as BAD_GESTURE.
    problems.append(
        expect_error(
            get_mapping(session, LIFT, 9, G_TAP),
            ERR_BAD_GESTURE,
            "out-of-range slot",
        )
    )
    problems.append(
        expect_error(
            set_mapping(session, LIFT, STANDALONE, G_TAP, 0x99),
            ERR_BAD_ACTION,
            "unknown action",
        )
    )
    # PUMP_TRIGGER is a real action, but momentary: only HELD can carry it.
    problems.append(
        expect_error(
            set_mapping(session, LIFT, STANDALONE, G_TAP, A_PUMP_TRIGGER),
            ERR_BAD_ACTION,
            "PUMP_TRIGGER on TAP",
        )
    )
    session.command(CMD_RESET_MAPPINGS, b5=0x34, b6=0x12)
    problems.append(
        expect_error(
            session.read(2.0, want=(MSG_ACK, MSG_ERROR)),
            ERR_BAD_MAGIC,
            "RESET_MAPPINGS with a wrong magic",
        )
    )

    return report("D  error paths", [p for p in problems if p])


def check_set_commit_reset(session):
    problems = []

    frame = set_mapping(session, LIFT, CONNECTED, G_PRESS, A_FORWARD)
    if frame is None or frame[1] != MSG_ACK:
        problems.append("SET_MAPPING did not ACK")

    frame = get_mapping(session, LIFT, CONNECTED, G_PRESS)
    if frame is None or frame[5] != A_FORWARD:
        problems.append("SET_MAPPING did not take effect in RAM")

    frame = get_mapping(session, LIFT, STANDALONE, G_PRESS)
    if frame is None or frame[5] != A_MODE_LIFT:
        problems.append("writing CONNECTED also changed STANDALONE")

    session.command(CMD_COMMIT_MAPPINGS)
    frame = session.read(3.0, want=(MSG_ACK, MSG_ERROR))
    if frame is None or frame[1] != MSG_ACK:
        problems.append("COMMIT_MAPPINGS did not ACK")

    session.command(CMD_RESET_MAPPINGS, b5=RESET_MAGIC & 0xFF, b6=RESET_MAGIC >> 8)
    frame = session.read(3.0, want=(MSG_ACK, MSG_ERROR))
    if frame is None or frame[1] != MSG_ACK:
        problems.append("RESET_MAPPINGS did not ACK")

    frame = get_mapping(session, LIFT, CONNECTED, G_PRESS)
    if frame is None or frame[5] != A_MODE_LIFT:
        problems.append("RESET_MAPPINGS did not restore the default")

    return report("E  SET / COMMIT / RESET round-trip", problems)


def check_slot_switch(session, probe):
    """Remote-mode LED and the STANDALONE fallback, live."""
    problems = []

    print("\nCHECK F -- slot switching and the remote-mode LED.")
    if not probe.available:
        return report(
            "F  slot switching and remote-mode LED",
            [f"needs the CDC probe to judge local actions ({probe.reason})"],
        )
    print("           Mode changes are read back over the CDC port; button")
    print("           presses are detected from EVENT frames, so there is no")
    print("           Enter to press -- just use the pump when asked.")

    # A background heartbeat holds the host active while prompts block. Without
    # it the device times out mid-question and every answer measures STANDALONE.
    session.start_keepalive()

    # Park the pump in Drop, so a stray switch to Lift is unambiguous.
    print("\n    Setup: press the DROP button on the pump.")
    if session.await_press(DROP) is None:
        session.stop_keepalive()
        return report("F  slot switching and remote-mode LED", ["no DROP press seen"])
    time.sleep(0.4)
    if probe.mode() != 1:
        problems.append("could not park the pump in Drop mode")

    # Hand LIFT to the host, in the CONNECTED slot only.
    for gesture in (G_PRESS, G_LONG_HOLD):
        frame = set_mapping(session, LIFT, CONNECTED, gesture, A_FORWARD)
        if frame is None or frame[1] != MSG_ACK:
            problems.append(f"could not FORWARD LIFT/{GESTURE_NAMES[gesture]}")
    session.idle(0.5)

    if not ask("Is the LIFT button now a distinct purple/violet, unlike the others?"):
        problems.append("remote-mode LED did not appear on LIFT")

    print("\n    Now press the LIFT button once.")
    seen = session.await_press(LIFT)
    time.sleep(0.4)
    if seen is None:
        # No EVENT frame means the host was not active, so whatever the pump
        # did next says nothing about the CONNECTED slot.
        problems.append(
            "no LIFT PRESS event arrived -- the host was not active, so this "
            "check is inconclusive rather than failed"
        )
    elif probe.mode() == 0:
        problems.append(
            "LIFT switched the pump to Lift mode while FORWARDed and the host "
            "was demonstrably active -- host and device would both act on it"
        )

    # Let the host heartbeat lapse -> instant fallback to STANDALONE.
    print(f"\n    Going quiet for {HOST_TIMEOUT_S + 0.8:.1f} s to time the host out...")
    session.stop_keepalive()
    session.idle(HOST_TIMEOUT_S + 0.8, beat=False)

    if not ask("Has the LIFT button gone back to its normal (unlit) look?"):
        problems.append("remote-mode LED did not clear on heartbeat timeout")

    # No EVENT frames now -- the device stops publishing once the host is gone,
    # which is itself the thing being tested. Fall back to Enter.
    input("    Press LIFT once more, then hit Enter. ")
    if probe.mode() != 0:
        problems.append(
            "LIFT did not switch to Lift mode after the host timed out -- the "
            "STANDALONE fallback did not take"
        )

    # Back to a clean table.
    session.heartbeat(force=True)
    session.idle(0.3)
    session.command(CMD_RESET_MAPPINGS, b5=RESET_MAGIC & 0xFF, b6=RESET_MAGIC >> 8)
    session.read(3.0, want=(MSG_ACK, MSG_ERROR))

    return report("F  slot switching and remote-mode LED", problems)


def check_factory_reset(session, device):
    """Optional: LIFT + DROP at power-on restores the defaults."""
    print("\nCHECK G -- factory reset gesture (needs a power cycle).")
    if not ask("Run it?"):
        print("  SKIP  G  factory reset gesture")
        return None

    # Give the reset something to undo, and persist it.
    set_mapping(session, REVERSE, CONNECTED, G_PRESS, A_FORWARD)
    session.command(CMD_COMMIT_MAPPINGS)
    if (session.read(3.0, want=(MSG_ACK, MSG_ERROR)) or [0, 0])[1] != MSG_ACK:
        return report("G  factory reset gesture", ["could not commit a test override"])
    print("    Committed REVERSE/CONNECTED/PRESS -> FORWARD to flash.")

    device.close()
    print("\n    1. Unplug the pump.")
    print("    2. Hold LIFT and DROP together.")
    print("    3. Plug it back in, keeping both held for 3 seconds.")
    print("    4. The LEDs flash white three times to confirm.")
    input("    Hit Enter once you have done that and the pump has booted. ")

    session_device = None
    for _ in range(20):
        try:
            paths = [
                d for d in hid.enumerate(VID, PID) if d["usage_page"] == VENDOR_USAGE_PAGE
            ]
            if paths:
                session_device = hid.Device(path=paths[0]["path"])
                break
        except hid.HIDException:
            pass
        time.sleep(1.0)

    if session_device is None:
        return report("G  factory reset gesture", ["could not reopen the vendor interface"])

    fresh = Session(session_device)
    fresh.heartbeat(force=True)
    fresh.idle(0.5)
    frame = get_mapping(fresh, REVERSE, CONNECTED, G_PRESS)
    problems = []
    if frame is None or frame[1] != MSG_MAPPING:
        problems.append("no MAPPING frame after the reset")
    elif frame[5] == A_FORWARD:
        problems.append("the committed override survived -- the gesture did not reset flash")
    elif frame[5] != A_MODE_REVERSE:
        problems.append(
            f"REVERSE/CONNECTED/PRESS is {ACTION_NAMES.get(frame[5], hex(frame[5]))}, "
            "expected MODE_REVERSE"
        )
    session_device.close()
    return report("G  factory reset gesture", problems)


# --------------------------------------------------------------- main


def main():
    warn_if_daemon_running()
    device = open_vendor_interface()
    print(f"Opened {device.manufacturer} {device.product}")

    session = Session(device)
    print("\nWaiting for the device to mark this host active...")
    session.heartbeat(force=True)
    session.idle(2.0)

    probe = ModeProbe()

    results = []
    print("")
    entries, terminators = bulk_dump(session)
    ok, bulk = check_bulk_dump(entries, terminators)
    results.append(ok)
    results.append(check_single(session, bulk))
    results.append(check_send_key_sentinel(bulk) if bulk else False)
    results.append(check_errors(session))
    results.append(check_set_commit_reset(session))
    results.append(check_slot_switch(session, probe))

    factory = check_factory_reset(session, device)
    if factory is not None:
        results.append(factory)
    else:
        try:
            device.close()
        except Exception:  # noqa: BLE001
            pass

    probe.close()

    print("\n" + ("ALL CHECKS PASSED" if all(results) else "SOME CHECKS FAILED"))
    print("Pump left on the default mapping table.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
