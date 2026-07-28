"""Phase 3 wire check for the Pixel Pump 1 vendor HID interface.

Verifies the four things Phase 3 changed on the wire, which the Phase 3 gate
left outstanding (see docs/plans/issue-30-micropython-1.28-protocol-v2.md):

  A. the foot pedal publishes FPEDAL (7) and *not* TRIGGER_BTN (15)
  B. the trigger button publishes TRIGGER_BTN (15) and *not* FPEDAL (7)
  C. buttons emit TAP, which they could not before Phase 3, ahead of RELEASE
  D. the heartbeat still reports model id 1 with HAS_MODEL

PP2's tools/usb-coms prints the same frames, but cannot assert the *absence*
of frames on the wrong control id, which is the regression this guards.

CPython, not MicroPython -- it runs on the host, like generateVersionFile.py.

    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid python tools/phase3_wire_check.py

Needs hidapi (`brew install hidapi`), and must be launched from Terminal:
macOS only opens a vendor HID interface for a process holding Input Monitoring,
so an agent shell or an IDE terminal will be refused with "exclusive access and
device already open" until it is granted that under
System Settings > Privacy & Security > Input Monitoring.
"""

import sys
import time

import hid

VID = 0x2E8A
PID = 0x1061
VENDOR_USAGE_PAGE = 0xFF00
REPORT_SIZE = 8

MSG_EVENT = 1
MSG_PING = 4

FPEDAL = 7
LOW = 12
TRIGGER_BTN = 15

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
EVENT_NAMES = {1: "PRESS", 2: "RELEASE", 3: "TAP", 4: "HOLD", 5: "LONG_HOLD", 6: "DELTA"}

PRESS, RELEASE, TAP, HOLD = 1, 2, 3, 4

FLAG_DEVICE_HEARTBEAT = 0x01
FLAG_DEV_BUILD = 0x02
FLAG_HAS_MODEL = 0x08
FLAG_HOST_HEARTBEAT = 0x80

# The device only publishes while it considers the host active, which times out
# 1200 ms after the last host write. 400 ms matches the daemon's interval.
HEARTBEAT_INTERVAL_S = 0.4
COLLECT_SECONDS = 12


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
            "If this says 'exclusive access and device already open', the process "
            "lacks Input Monitoring. Run this from Terminal.app and grant it under\n"
            "  System Settings > Privacy & Security > Input Monitoring."
        )


def write_frame(device, frame):
    # hidapi wants a leading report-ID byte even though this interface uses none.
    device.write(bytes((0,)) + frame)


def send_host_heartbeat(device, seq):
    stamp = int(time.monotonic() * 1000) & 0xFFFF
    frame = bytes(
        (
            2,  # protocol version
            MSG_PING,
            seq & 0xFF,
            0,
            0,
            stamp & 0xFF,
            (stamp >> 8) & 0xFF,
            FLAG_HOST_HEARTBEAT,
        )
    )
    write_frame(device, frame)
    return (seq + 1) & 0xFF


def normalize(data):
    b = bytes(data)
    # hidapi may or may not prepend the report ID.
    if len(b) == REPORT_SIZE + 1 and b[0] == 0:
        b = b[1:]
    return b[:REPORT_SIZE] if len(b) >= REPORT_SIZE else None


def collect(device, state, seconds, verbose=True):
    """Pump heartbeats and gather EVENT frames for `seconds`.

    Returns a list of (control_id, event_kind) in arrival order.
    """
    events = []
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        if time.monotonic() >= state["next_heartbeat"]:
            state["seq"] = send_host_heartbeat(device, state["seq"])
            state["next_heartbeat"] = time.monotonic() + HEARTBEAT_INTERVAL_S

        try:
            data = device.read(REPORT_SIZE + 1, 50)
        except hid.HIDException as exc:
            print(f"  read failed: {exc}")
            break

        frame = normalize(data) if data else None
        if not frame:
            continue

        msg_type = frame[1]

        if msg_type == MSG_PING and frame[7] & FLAG_DEVICE_HEARTBEAT:
            state["heartbeat"] = {
                "model": frame[3],
                "version": (frame[4], frame[5], frame[6]),
                "has_model": bool(frame[7] & FLAG_HAS_MODEL),
                "dev": bool(frame[7] & FLAG_DEV_BUILD),
            }
            continue

        if msg_type != MSG_EVENT:
            continue

        control_id, event_kind = frame[3], frame[4]
        events.append((control_id, event_kind))
        if verbose and event_kind != HOLD:  # HOLD repeats every 120 ms; too noisy
            print(
                f"  {CONTROL_NAMES.get(control_id, f'control {control_id}'):<12}"
                f" {EVENT_NAMES.get(event_kind, event_kind)}"
            )

    return events


def prompt(text):
    print(f"\n{text}")
    print(f"  (collecting for {COLLECT_SECONDS} s -- go)")


def report(name, problems):
    if problems:
        print(f"  FAIL  {name}: " + "; ".join(problems))
        return False
    print(f"  PASS  {name}")
    return True


def check_split(name, events, expected_control, forbidden_control):
    seen = {e for c, e in events if c == expected_control}
    leaked = [e for c, e in events if c == forbidden_control]

    problems = []
    if PRESS not in seen:
        problems.append(f"no PRESS on {CONTROL_NAMES[expected_control]}")
    if RELEASE not in seen:
        problems.append(f"no RELEASE on {CONTROL_NAMES[expected_control]}")
    if leaked:
        problems.append(
            f"{len(leaked)} frame(s) leaked onto {CONTROL_NAMES[forbidden_control]}"
        )
    return report(name, problems)


def check_tap(events):
    kinds = [e for c, e in events if c == LOW]
    problems = []
    if TAP not in kinds:
        problems.append("no TAP frame on LOW")
    elif RELEASE in kinds and kinds.index(TAP) > kinds.index(RELEASE):
        problems.append("TAP arrived after RELEASE (spec says it precedes it)")
    return report("C  buttons emit TAP", problems)


def check_heartbeat(heartbeat):
    problems = []
    if heartbeat is None:
        problems.append("no device heartbeat seen at all")
    else:
        if heartbeat["model"] != 1:
            problems.append(f"model id is {heartbeat['model']}, expected 1")
        if not heartbeat["has_model"]:
            problems.append("HAS_MODEL flag not set")
    return report("D  heartbeat reports model 1", problems)


def main():
    device = open_vendor_interface()
    print(f"Opened {device.manufacturer} {device.product}")

    state = {"seq": 0, "next_heartbeat": 0.0, "heartbeat": None}

    print("\nWaiting for the device to mark this host active...")
    collect(device, state, 2.0, verbose=False)

    results = []

    prompt(
        "CHECK A -- press and hold the FOOT PEDAL (the vacuum pedal, GPIO6)\n"
        "           for about a second, then release."
    )
    results.append(
        check_split(
            "A  foot pedal publishes FPEDAL",
            collect(device, state, COLLECT_SECONDS),
            FPEDAL,
            TRIGGER_BTN,
        )
    )

    prompt(
        "CHECK B -- press and hold the TRIGGER BUTTON on the pump\n"
        "           for about a second, then release."
    )
    results.append(
        check_split(
            "B  trigger button publishes TRIGGER_BTN",
            collect(device, state, COLLECT_SECONDS),
            TRIGGER_BTN,
            FPEDAL,
        )
    )

    prompt(
        "CHECK C -- quickly TAP the LOW button a few times.\n"
        "           A tap is a release inside 300 ms, so be brisk."
    )
    results.append(check_tap(collect(device, state, COLLECT_SECONDS)))

    results.append(check_heartbeat(state["heartbeat"]))

    heartbeat = state["heartbeat"]
    if heartbeat:
        major, minor, patch = heartbeat["version"]
        dev = " (dev)" if heartbeat["dev"] else ""
        print(f"\n     firmware {major}.{minor}.{patch}{dev}")

    device.close()

    print("\n" + ("ALL CHECKS PASSED" if all(results) else "SOME CHECKS FAILED"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
