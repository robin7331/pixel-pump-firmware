"""Control ids, gestures and the device heartbeat.

What this guards is mostly the *absence* of frames on the wrong control id --
the trigger button and the foot pedal are separate controls that both run the
vacuum, and a regression that merges them looks completely normal to anyone
watching a raw frame dump. PP2's `tools/usb-coms` prints the same frames but
cannot assert that a control stayed quiet.

  - the foot pedal publishes FPEDAL and *not* TRIGGER_BTN
  - the trigger button publishes TRIGGER_BTN and *not* FPEDAL
  - buttons emit TAP, ahead of RELEASE
  - the heartbeat reports model id 1 with HAS_MODEL set

Only the heartbeat check runs unattended; the other three need someone to work
the pump, so `--auto` skips them.
"""

import time

from .firmware import ControlId, EventKind, Flags, MessageType, ModelId, control_name, event_name
from .transport import report, unattended

GROUP = "wire"
TITLE = "Control ids, gestures and the heartbeat"
NEEDS_PUMP = True

COLLECT_SECONDS = 12


def collect(session, state, seconds, verbose=True):
    """Beat, and gather EVENT frames for `seconds`.

    Returns a list of (control_id, event_kind) in arrival order. The device
    heartbeat is stashed in `state` as it goes by, since it arrives on the same
    stream and there is no second chance to ask for it.
    """
    events = []
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        # session.read() beats for us and returns the next frame of any type.
        frame = session.read(min(0.3, max(0.05, deadline - time.monotonic())))
        if frame is None:
            continue

        msg_type = frame[1]

        if msg_type == MessageType.PING and frame[7] & Flags.DEVICE_HEARTBEAT:
            state["heartbeat"] = {
                "model": frame[3],
                "version": (frame[4], frame[5], frame[6]),
                "has_model": bool(frame[7] & Flags.HAS_MODEL),
                "dev": bool(frame[7] & Flags.DEV_BUILD),
            }
            continue

        if msg_type != MessageType.EVENT:
            continue

        control_id, event_kind = frame[3], frame[4]
        events.append((control_id, event_kind))
        if verbose and event_kind != EventKind.HOLD:  # HOLD repeats every 120 ms
            print(f"  {control_name(control_id):<12} {event_name(event_kind)}")

    return events


def prompt(text):
    print(f"\n{text}")
    print(f"  (collecting for {COLLECT_SECONDS} s -- go)")


def check_split(name, events, expected_control, forbidden_control):
    seen = {e for c, e in events if c == expected_control}
    leaked = [e for c, e in events if c == forbidden_control]

    problems = []
    if EventKind.PRESS not in seen:
        problems.append(f"no PRESS on {control_name(expected_control)}")
    if EventKind.RELEASE not in seen:
        problems.append(f"no RELEASE on {control_name(expected_control)}")
    if leaked:
        problems.append(
            f"{len(leaked)} frame(s) leaked onto {control_name(forbidden_control)}"
        )
    return report(name, problems)


def check_tap(events):
    kinds = [e for c, e in events if c == ControlId.LOW]
    problems = []
    if EventKind.TAP not in kinds:
        problems.append("no TAP frame on LOW")
    elif (
        EventKind.RELEASE in kinds
        and kinds.index(EventKind.TAP) > kinds.index(EventKind.RELEASE)
    ):
        problems.append("TAP arrived after RELEASE (spec says it precedes it)")
    return report("buttons emit TAP", problems)


def check_heartbeat(heartbeat):
    problems = []
    if heartbeat is None:
        problems.append("no device heartbeat seen at all")
    else:
        if heartbeat["model"] != ModelId.PIXEL_PUMP_1:
            problems.append(f"model id is {heartbeat['model']}, expected 1")
        if not heartbeat["has_model"]:
            problems.append("HAS_MODEL flag not set")
    return report("heartbeat reports model 1", problems)


def run(rig):
    session = rig.session
    state = {"heartbeat": None}

    print("Waiting for the device to mark this host active...")
    collect(session, state, 2.0, verbose=False)

    results = []

    if unattended():
        print("  SKIP  the three gesture checks (unattended run -- they need someone")
        print("        to work the pump). Listening for a heartbeat only.")
        collect(session, state, 3.0, verbose=False)
    else:
        prompt(
            "Press and hold the FOOT PEDAL (the vacuum pedal, GPIO6)\n"
            "  for about a second, then release."
        )
        results.append(
            check_split(
                "foot pedal publishes FPEDAL",
                collect(session, state, COLLECT_SECONDS),
                ControlId.FPEDAL,
                ControlId.TRIGGER_BTN,
            )
        )

        prompt(
            "Press and hold the TRIGGER BUTTON on the pump\n"
            "  for about a second, then release."
        )
        results.append(
            check_split(
                "trigger button publishes TRIGGER_BTN",
                collect(session, state, COLLECT_SECONDS),
                ControlId.TRIGGER_BTN,
                ControlId.FPEDAL,
            )
        )

        prompt(
            "Quickly TAP the LOW button a few times.\n"
            "  A tap is a release inside 300 ms, so be brisk."
        )
        results.append(check_tap(collect(session, state, COLLECT_SECONDS)))

    results.append(check_heartbeat(state["heartbeat"]))

    heartbeat = state["heartbeat"]
    if heartbeat:
        major, minor, patch = heartbeat["version"]
        dev = " (dev)" if heartbeat["dev"] else ""
        print(f"        firmware {major}.{minor}.{patch}{dev}")

    return results
