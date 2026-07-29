"""Talking to a pump: the vendor HID session, the CDC port, and reporting.

Everything here is shared by every check group. The protocol vocabulary comes
from `firmware.py`, which imports the firmware's own `protocol.py` -- nothing in
this file restates a control id, a command id or an error code.

Two hard-won rules are encoded here rather than left to whoever writes the next
check:

  * **A blocking prompt is a disconnection.** The device drops to STANDALONE
    1200 ms after the last host write, so any `input()` times the host out
    mid-question and the answer then describes a standalone pump. `Session`
    can beat from a background thread; use `start_keepalive()` around anything
    that blocks. A press with no EVENT frame means the host was inactive --
    report that as inconclusive, never as a failed mapping.
  * **"Already open" is usually not a permissions problem.** Board Factory's
    `pixel-pump-daemon` holds the vendor interface exclusively whenever its dev
    app runs, and macOS reports a held interface exactly like a missing grant.
    `warn_if_daemon_running()` says so before the open fails.
"""

import glob
import json
import subprocess
import sys
import threading
import time

import hid

from .firmware import (
    MAPPING_ALL,
    PID,
    REPORT_SIZE,
    VENDOR_USAGE_PAGE,
    VID,
    CommandId,
    EventKind,
    MessageType,
    error_name,
)

# Re-exported so a check module imports one place for both.
from .reporting import ask, describe, report, unattended  # noqa: F401

HEARTBEAT_INTERVAL_S = 0.4  # matches the daemon's interval
HOST_TIMEOUT_S = 1.2  # device drops to STANDALONE this long after the last write

BOOTSEL_VOLUME = "/Volumes/RPI-RP2"


# --------------------------------------------------------------- opening


def warn_if_daemon_running():
    """Board Factory's daemon holds the interface, and macOS blames permissions.

    hidapi reports a held interface and a missing grant with the same message,
    which sends you to System Settings for a problem that is not there. Say so
    up front instead.
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


def vendor_paths():
    return [d for d in hid.enumerate(VID, PID) if d["usage_page"] == VENDOR_USAGE_PAGE]


def vendor_present():
    return bool(vendor_paths())


def open_vendor_interface():
    paths = vendor_paths()
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
            "'exclusive access and device already open' almost always means\n"
            "something else holds the interface. Check\n"
            "    pgrep -fl pixel-pump-daemon\n"
            "and quit Board Factory if it is running.\n\n"
            "It can also be a missing Input Monitoring grant, but that is no\n"
            "longer the usual cause -- opening this interface from an agent\n"
            "shell works. Try the daemon first."
        )


def describe_interfaces():
    for d in sorted(hid.enumerate(VID, PID), key=lambda e: (e["usage_page"], e["usage"])):
        print(
            f"        usage page {d['usage_page']:#06x} usage {d['usage']:#04x}"
            f"  {d.get('product_string') or ''}"
        )


def write_frame(device, frame):
    # hidapi wants a leading report-ID byte even though this interface uses none.
    device.write(bytes((0,)) + frame)


def normalize(data):
    b = bytes(data)
    # hidapi may or may not prepend the report ID.
    if len(b) == REPORT_SIZE + 1 and b[0] == 0:
        b = b[1:]
    return b[:REPORT_SIZE] if len(b) >= REPORT_SIZE else None


# --------------------------------------------------------------- session


class Session:
    """Vendor HID transport that keeps the host heartbeat alive.

    The heartbeat can run on a background thread, which matters more than it
    sounds -- see this module's docstring. Beating only from the main loop meant
    every interactive check silently measured a disconnected pump.
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
                    (
                        2,
                        MessageType.PING,
                        self.seq,
                        0,
                        0,
                        stamp & 0xFF,
                        (stamp >> 8) & 0xFF,
                        0x80,  # Flags.HOST_HEARTBEAT
                    )
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
                bytes((2, MessageType.COMMAND, self.seq, command_id, b4, b5, b6, b7)),
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
            want=(MessageType.EVENT,),
            match=lambda f: f[3] == control and f[4] == EventKind.PRESS,
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


def read_heartbeat(session, timeout_s=3.0):
    """A device heartbeat, not our own -- HOST_HEARTBEAT is the echo of ours."""
    return session.read(
        timeout_s,
        want=(MessageType.PING,),
        match=lambda f: bool(f[7] & 0x01),  # Flags.DEVICE_HEARTBEAT
    )


# ------------------------------------------------------- mapping commands


def get_mapping(session, control, slot, gesture, timeout_s=2.0):
    session.command(CommandId.GET_MAPPING, b4=control, b5=(slot << 4) | gesture)
    return session.read(timeout_s, want=(MessageType.MAPPING, MessageType.ERROR))


def set_mapping(session, control, slot, gesture, action, param=0, timeout_s=2.0):
    session.command(
        CommandId.SET_MAPPING,
        b4=control,
        b5=(slot << 4) | gesture,
        b6=action,
        b7=param,
    )
    return session.read(timeout_s, want=(MessageType.ACK, MessageType.ERROR))


def reset_mappings(session, magic, timeout_s=3.0):
    session.command(CommandId.RESET_MAPPINGS, b5=magic & 0xFF, b6=(magic >> 8) & 0xFF)
    return session.read(timeout_s, want=(MessageType.ACK, MessageType.ERROR))


def commit_mappings(session, timeout_s=3.0):
    session.command(CommandId.COMMIT_MAPPINGS)
    return session.read(timeout_s, want=(MessageType.ACK, MessageType.ERROR))


def bulk_dump(session, timeout_s=6.0):
    """Every non-NONE entry, as (control, slot, gesture, action, param)."""
    session.command(CommandId.GET_MAPPING, b4=MAPPING_ALL)
    entries = []
    terminators = 0
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        frame = session.read(0.5, want=(MessageType.MAPPING, MessageType.ERROR))
        if frame is None:
            break
        if frame[1] == MessageType.ERROR:
            return None, 0
        if frame[3] == MAPPING_ALL:
            terminators += 1
            # Keep reading briefly: a second terminator is a bug worth catching.
            deadline = min(deadline, time.monotonic() + 0.5)
            continue
        entries.append(
            (frame[3], (frame[4] >> 4) & 0x0F, frame[4] & 0x0F, frame[5], frame[6])
        )
    return entries, terminators


# --------------------------------------------------------------- CDC probe


class ModeProbe:
    """Reads the legacy stdin protocol over the CDC port.

    The vendor interface cannot answer "did the button act locally" -- under
    publish-all the EVENT frame goes out whether the action ran or not. This
    can. It is also a route to the firmware version that does not pass through
    the HID stack at all, which is what makes it worth cross-checking against,
    and the only route to what is actually *in flash*.
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
        """settings.json as a dict, or None.

        The only route to what is in flash: the vendor interface answers
        GET_MAPPING from RAM, so it cannot tell a committed row from an
        uncommitted one.
        """
        for line in self.send("settings:dump").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    return None
        return None

    def mode(self):
        settings = self.dump()
        return None if settings is None else settings.get("mode")

    def mappings(self):
        """The persisted override rows, as lists. None if the dump failed."""
        settings = self.dump()
        if settings is None:
            return None
        rows = settings.get("mappings")
        return None if not isinstance(rows, list) else [list(row) for row in rows]

    def version_info(self):
        """`version:info` -> "tag,branch,commit_hash,timestamp", or None."""
        for line in self.send("version:info").splitlines():
            line = line.strip()
            # Four comma-separated fields, and not the settings JSON.
            if line and not line.startswith("{") and line.count(",") == 3:
                return line
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

    def close(self):
        if self.serial:
            self.serial.close()


# --------------------------------------------------------------- reporting


def expect_error(frame, expected, label):
    """None when `frame` is the expected ERROR, else a problem string."""
    if frame is None:
        return f"{label}: no response"
    if frame[1] != MessageType.ERROR:
        return f"{label}: got msg_type {frame[1]}, expected ERROR"
    code = frame[5] | (frame[6] << 8)
    if code != expected:
        return f"{label}: {error_name(code)}, expected {error_name(expected)}"
    return None
