"""The mapping table: reads, writes, slots, persistence and the way back.

Layer 1 of the spec's two-layer control model -- a table keyed by
`(control, gesture, slot)` holding `(action, param)`. Everything the engine is
actually for lives here:

  - bulk GET_MAPPING streams the default table and terminates exactly once
  - single GET_MAPPING agrees with the bulk dump, and addresses slots
  - the FPEDAL_AUX SEND_KEY sentinel resolves on read rather than leaking 0x00
  - the error paths: BAD_CONTROL, BAD_GESTURE (bad gesture *and* bad slot),
    BAD_ACTION (unknown, and valid-but-wrong-gesture), BAD_MAGIC
  - SET_MAPPING is live in RAM and slot-scoped; COMMIT and RESET both ACK, and
    RESET restores the defaults
  - COMMIT_MAPPINGS actually reaches flash, and SET_MAPPING actually does not
  - slot switching and the remote-mode LED, including the instant STANDALONE
    fallback when the host heartbeat lapses
  - the factory reset gesture (opt-in -- it wants a power cycle)

Slot switching needs someone to look at the pump and press a button, but the
*verdict* is still taken from the device, by reading `mode` back over CDC. A
FORWARD button that still switches modes is the failure this exists to catch,
and it is invisible on the vendor interface: publish-all reports the press
either way.

The pump is left on the default table -- the run ends with RESET_MAPPINGS.
"""

import time

from .checks_static import EXPECTED_DEFAULTS
from .firmware import (
    RESET_MAPPINGS_MAGIC,
    Action,
    ControlId,
    ErrorCode,
    Gesture,
    MappingSlot,
    MessageType,
    action_name,
    control_name,
    gesture_name,
)
from .transport import (
    HOST_TIMEOUT_S,
    ask,
    bulk_dump,
    commit_mappings,
    describe,
    expect_error,
    get_mapping,
    report,
    reset_mappings,
    set_mapping,
    unattended,
)

GROUP = "mapping"
TITLE = "Mapping table"
NEEDS_PUMP = True

STANDALONE = MappingSlot.STANDALONE
CONNECTED = MappingSlot.CONNECTED

WRONG_RESET_MAGIC = 0x1234


def _entry(frame):
    return (frame[3], (frame[4] >> 4) & 0x0F, frame[4] & 0x0F, frame[5], frame[6])


# --------------------------------------------------------------- reads


def check_bulk_dump(entries, terminators):
    """Returns (ok, {(control, slot, gesture): (action, param)})."""
    problems = []
    if entries is None:
        return report("bulk GET_MAPPING streams the default table", ["got an ERROR frame"]), {}

    got = {(c, s, g): (a, p) for c, s, g, a, p in entries}
    expected = {
        (control, slot, gesture): action
        for (control, gesture), (action, _param) in EXPECTED_DEFAULTS.items()
        for slot in (STANDALONE, CONNECTED)
    }

    for key, action in sorted(expected.items()):
        if key not in got:
            problems.append(f"missing {describe((key[0], key[1], key[2], action, 0))}")
        elif got[key][0] != action:
            problems.append(
                f"{control_name(key[0])}/{gesture_name(key[2])}"
                f" slot {key[1]} is {action_name(got[key][0])},"
                f" expected {action_name(action)}"
            )
    for key in sorted(got):
        if key not in expected:
            problems.append(f"unexpected entry {describe((key[0], key[1], key[2]) + got[key])}")

    if terminators == 0:
        problems.append("dump never terminated (no control 0xFF frame)")
    elif terminators > 1:
        problems.append(f"{terminators} terminator frames, expected 1")

    ok = report("bulk GET_MAPPING streams the default table", problems)
    if ok:
        print(f"        {len(entries)} entries, terminated once")
    return ok, got


def check_single(session, bulk):
    problems = []
    frame = get_mapping(session, ControlId.LIFT, STANDALONE, Gesture.PRESS)
    if frame is None or frame[1] != MessageType.MAPPING:
        problems.append("no MAPPING frame for LIFT/STANDALONE/PRESS")
    else:
        entry = _entry(frame)
        if entry[:3] != (ControlId.LIFT, STANDALONE, Gesture.PRESS):
            problems.append(f"echoed the wrong target: {describe(entry)}")
        if entry[3] != Action.MODE_LIFT:
            problems.append(f"action is {action_name(entry[3])}")
        key = (ControlId.LIFT, STANDALONE, Gesture.PRESS)
        if bulk and bulk.get(key, (None,))[0] != entry[3]:
            problems.append("disagrees with the bulk dump")

    # A cell the default table leaves empty must read back as NONE, not absent.
    frame = get_mapping(session, ControlId.LIFT, CONNECTED, Gesture.HELD)
    if frame is None or frame[1] != MessageType.MAPPING:
        problems.append("no MAPPING frame for an unmapped cell")
    elif frame[5] != Action.NONE:
        problems.append(f"unmapped cell reads {action_name(frame[5])}")

    return report("single GET_MAPPING addresses cells and slots", problems)


def check_send_key_sentinel(bulk):
    problems = []
    for gesture in (Gesture.TAP, Gesture.LONG_HOLD):
        label = gesture_name(gesture)
        entry = bulk.get((ControlId.FPEDAL_AUX, STANDALONE, gesture))
        if entry is None:
            problems.append(f"FPEDAL_AUX {label} missing from the dump")
            continue
        action, param = entry
        if action != Action.SEND_KEY:
            problems.append(f"FPEDAL_AUX {label} is not SEND_KEY")
        elif param == 0x00:
            problems.append(
                f"FPEDAL_AUX {label} reports param 0x00 -- the sentinel leaked "
                "instead of being resolved to the configured keycode"
            )
    ok = report("SEND_KEY sentinel resolves on read", problems)
    if ok:
        tap = bulk[(ControlId.FPEDAL_AUX, STANDALONE, Gesture.TAP)][1]
        hold = bulk[(ControlId.FPEDAL_AUX, STANDALONE, Gesture.LONG_HOLD)][1]
        print(f"        aux pedal sends {tap:#04x} on tap, {hold:#04x} on long hold")
    return ok


# --------------------------------------------------------------- writes


def check_errors(session):
    problems = []

    problems.append(
        expect_error(
            get_mapping(session, ControlId.ENCODER, STANDALONE, Gesture.TAP),
            ErrorCode.BAD_CONTROL,
            "ENCODER (a PP2 control)",
        )
    )
    problems.append(
        expect_error(
            get_mapping(session, ControlId.LIFT, STANDALONE, Gesture.DELTA_CW),
            ErrorCode.BAD_GESTURE,
            "DELTA_CW on a button",
        )
    )
    # Slot rides in the gesture byte and ErrorCode has no BAD_SLOT, so an
    # out-of-range slot is specified to report as BAD_GESTURE.
    problems.append(
        expect_error(
            get_mapping(session, ControlId.LIFT, 9, Gesture.TAP),
            ErrorCode.BAD_GESTURE,
            "out-of-range slot",
        )
    )
    problems.append(
        expect_error(
            set_mapping(session, ControlId.LIFT, STANDALONE, Gesture.TAP, 0x99),
            ErrorCode.BAD_ACTION,
            "unknown action",
        )
    )
    # PUMP_TRIGGER is a real action, but momentary: only HELD can carry it.
    problems.append(
        expect_error(
            set_mapping(session, ControlId.LIFT, STANDALONE, Gesture.TAP, Action.PUMP_TRIGGER),
            ErrorCode.BAD_ACTION,
            "PUMP_TRIGGER on TAP",
        )
    )
    problems.append(
        expect_error(
            reset_mappings(session, WRONG_RESET_MAGIC, timeout_s=2.0),
            ErrorCode.BAD_MAGIC,
            "RESET_MAPPINGS with a wrong magic",
        )
    )

    return report("error paths", [p for p in problems if p])


def check_set_commit_reset(session):
    problems = []

    frame = set_mapping(session, ControlId.LIFT, CONNECTED, Gesture.PRESS, Action.FORWARD)
    if frame is None or frame[1] != MessageType.ACK:
        problems.append("SET_MAPPING did not ACK")

    frame = get_mapping(session, ControlId.LIFT, CONNECTED, Gesture.PRESS)
    if frame is None or frame[5] != Action.FORWARD:
        problems.append("SET_MAPPING did not take effect in RAM")

    frame = get_mapping(session, ControlId.LIFT, STANDALONE, Gesture.PRESS)
    if frame is None or frame[5] != Action.MODE_LIFT:
        problems.append("writing CONNECTED also changed STANDALONE")

    frame = commit_mappings(session)
    if frame is None or frame[1] != MessageType.ACK:
        problems.append("COMMIT_MAPPINGS did not ACK")

    frame = reset_mappings(session, RESET_MAPPINGS_MAGIC)
    if frame is None or frame[1] != MessageType.ACK:
        problems.append("RESET_MAPPINGS did not ACK")

    frame = get_mapping(session, ControlId.LIFT, CONNECTED, Gesture.PRESS)
    if frame is None or frame[5] != Action.MODE_LIFT:
        problems.append("RESET_MAPPINGS did not restore the default")

    return report("SET / COMMIT / RESET round-trip", problems)


def check_commit_persistence(session, probe):
    """COMMIT_MAPPINGS reaches flash, proven without a reboot.

    Watching a factory reset wipe a committed override is exactly as consistent
    with the commit never having reached flash in the first place -- the
    evidence gets destroyed by the thing being used to test it.

    Reading settings.json over CDC *between* the commit and the reset settles
    it, and the same read proves the other half of the contract for free:
    SET_MAPPING alone must be RAM only. GET_MAPPING cannot distinguish the two,
    since it answers from RAM either way.

    Leaves the table clean -- it ends on RESET_MAPPINGS, like the rest.
    """
    name = "COMMIT_MAPPINGS reaches flash, SET_MAPPING does not"
    problems = []
    if not probe.available:
        return report(name, [f"needs the CDC port to read settings.json ({probe.reason})"])

    # [control, slot, gesture, action, param], as mapping.py's commit() stores it.
    expected_row = [ControlId.REVERSE, CONNECTED, Gesture.PRESS, Action.FORWARD, 0]

    if (reset_mappings(session, RESET_MAPPINGS_MAGIC) or [0, 0])[1] != MessageType.ACK:
        return report(name, ["could not reset to a clean table first"])

    rows = probe.mappings()
    if rows is None:
        return report(name, ["settings.json has no readable mappings key"])
    if rows:
        problems.append(f"flash still held {len(rows)} row(s) straight after a reset: {rows}")

    frame = set_mapping(session, ControlId.REVERSE, CONNECTED, Gesture.PRESS, Action.FORWARD)
    if frame is None or frame[1] != MessageType.ACK:
        return report(name, ["SET_MAPPING did not ACK"])

    rows = probe.mappings()
    if rows is None:
        problems.append("settings:dump stopped answering after SET_MAPPING")
    elif expected_row in rows:
        problems.append(
            "SET_MAPPING alone wrote the row to flash -- it is specified as RAM only, "
            "so COMMIT_MAPPINGS would be doing nothing and every host write would "
            "burn a flash cycle"
        )
    else:
        print("        SET_MAPPING left flash untouched")

    if (commit_mappings(session) or [0, 0])[1] != MessageType.ACK:
        problems.append("COMMIT_MAPPINGS did not ACK")

    rows = probe.mappings()
    if rows is None:
        problems.append("settings:dump stopped answering after COMMIT_MAPPINGS")
    elif expected_row not in rows:
        problems.append(
            f"committed row absent from flash -- settings.json holds {rows}, "
            f"expected to find {expected_row}"
        )
    else:
        print(f"        COMMIT_MAPPINGS wrote {describe(tuple(expected_row))} to flash")
        if len(rows) > 1:
            problems.append(f"flash holds {len(rows)} rows, expected only the one: {rows}")

    if (reset_mappings(session, RESET_MAPPINGS_MAGIC) or [0, 0])[1] != MessageType.ACK:
        problems.append("RESET_MAPPINGS did not ACK")

    rows = probe.mappings()
    if rows is None:
        problems.append("settings:dump stopped answering after RESET_MAPPINGS")
    elif rows:
        problems.append(f"RESET_MAPPINGS left {rows} behind in flash")
    else:
        print("        RESET_MAPPINGS cleared flash again")

    return report(name, problems)


# --------------------------------------------------------------- live


def check_slot_switch(session, probe):
    """Remote-mode LED and the STANDALONE fallback, live."""
    name = "slot switching and remote-mode LED"
    problems = []

    print("\nSlot switching and the remote-mode LED.")
    if unattended():
        print(f"  SKIP  {name} (unattended run -- it needs someone at the pump)")
        return None
    if not probe.available:
        return report(name, [f"needs the CDC probe to judge local actions ({probe.reason})"])
    print("           Mode changes are read back over the CDC port; button")
    print("           presses are detected from EVENT frames, so there is no")
    print("           Enter to press -- just use the pump when asked.")

    # A background heartbeat holds the host active while prompts block. Without
    # it the device times out mid-question and every answer measures STANDALONE.
    session.start_keepalive()

    # Park the pump in Drop, so a stray switch to Lift is unambiguous.
    print("\n    Setup: press the DROP button on the pump.")
    if session.await_press(ControlId.DROP) is None:
        session.stop_keepalive()
        return report(name, ["no DROP press seen"])
    time.sleep(0.4)
    if probe.mode() != 1:
        problems.append("could not park the pump in Drop mode")

    # Hand LIFT to the host, in the CONNECTED slot only.
    for gesture in (Gesture.PRESS, Gesture.LONG_HOLD):
        frame = set_mapping(session, ControlId.LIFT, CONNECTED, gesture, Action.FORWARD)
        if frame is None or frame[1] != MessageType.ACK:
            problems.append(f"could not FORWARD LIFT/{gesture_name(gesture)}")
    session.idle(0.5)

    if not ask("Is the LIFT button now a distinct purple/violet, unlike the others?"):
        problems.append("remote-mode LED did not appear on LIFT")

    print("\n    Now press the LIFT button once.")
    seen = session.await_press(ControlId.LIFT)
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
    reset_mappings(session, RESET_MAPPINGS_MAGIC)

    return report(name, problems)


def check_factory_reset(rig):
    """LIFT + DROP at power-on restores the defaults. Opt-in: wants a power cycle.

    This is the only way back from a table that forwards every button, so it is
    worth proving rather than assuming.
    """
    name = "factory reset gesture"
    print("\nFactory reset gesture (needs a power cycle).")
    if unattended():
        print(f"  SKIP  {name} (unattended run)")
        return None
    if not ask("Run it?"):
        print(f"  SKIP  {name}")
        return None

    session = rig.session

    # Give the reset something to undo, and persist it.
    set_mapping(session, ControlId.REVERSE, CONNECTED, Gesture.PRESS, Action.FORWARD)
    if (commit_mappings(session) or [0, 0])[1] != MessageType.ACK:
        return report(name, ["could not commit a test override"])
    print("    Committed REVERSE/CONNECTED/PRESS -> FORWARD to flash.")

    rig.release_device()
    print("\n    1. Unplug the pump.")
    print("    2. Hold LIFT and DROP together.")
    print("    3. Plug it back in, keeping both held for 3 seconds.")
    print("    4. The LEDs flash white three times to confirm.")
    input("    Hit Enter once you have done that and the pump has booted. ")

    if not rig.reopen_after_power_cycle():
        return report(name, ["could not reopen the vendor interface"])

    frame = get_mapping(rig.session, ControlId.REVERSE, CONNECTED, Gesture.PRESS)
    problems = []
    if frame is None or frame[1] != MessageType.MAPPING:
        problems.append("no MAPPING frame after the reset")
    elif frame[5] == Action.FORWARD:
        problems.append("the committed override survived -- the gesture did not reset flash")
    elif frame[5] != Action.MODE_REVERSE:
        problems.append(
            f"REVERSE/CONNECTED/PRESS is {action_name(frame[5])}, expected MODE_REVERSE"
        )
    return report(name, problems)


def run(rig):
    session, probe = rig.session, rig.cdc
    results = []

    entries, terminators = bulk_dump(session)
    ok, bulk = check_bulk_dump(entries, terminators)
    results.append(ok)
    results.append(check_single(session, bulk))
    results.append(check_send_key_sentinel(bulk) if bulk else False)
    results.append(check_errors(session))
    results.append(check_set_commit_reset(session))
    results.append(check_commit_persistence(session, probe))

    slot = check_slot_switch(session, probe)
    if slot is not None:
        results.append(slot)

    factory = check_factory_reset(rig)
    if factory is not None:
        results.append(factory)

    print("\n    Pump left on the default mapping table.")
    return results
