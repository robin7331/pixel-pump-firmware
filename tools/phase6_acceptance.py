"""Phase 6 acceptance checks -- the device half of what Board Factory needs.

Covers the three acceptance items that neither phase3_wire_check.py nor
phase4_wire_check.py touches:

  A. GET_VERSION answers, and its version agrees with the heartbeat and with
     the legacy `version:info` on the CDC port -- three paths, one answer
  B. GET_INFO answers model 1 + protocol 2 with HAS_MODEL set, and the
     heartbeat carries the same model
  C. ENTER_BOOTLOADER with a wrong magic is refused as BAD_MAGIC *and the pump
     keeps running* -- the half that matters, since a spurious reboot
     mid-assembly is the failure this magic exists to prevent
  D. ENTER_BOOTLOADER with 0xB007 ACKs and lands the device in BOOTSEL

D reboots the pump, so it runs last and is opt-in. Recovering from it is a
power cycle (the RP2040 leaves BOOTSEL on its own) or a fresh UF2 copy.

What this deliberately does NOT cover, because no script can: "behaves
identically to a legacy unit", which wants two pumps side by side, and "the
daemon connects", which wants the actual daemon. This checks that the firmware
answers correctly, not that Board Factory is happy -- those are different
claims and only the second one closes the issue.

Shares its transport with phase4_wire_check.py rather than re-implementing it;
the daemon warning, the Input Monitoring advice and the background heartbeat
all matter just as much here.

    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \
        python tools/phase6_acceptance.py

Needs the vendor HID interface, so it must be launched from Terminal -- macOS
only opens one for a process holding Input Monitoring.
"""

import os
import sys
import time

import hid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase4_wire_check import (  # noqa: E402
    MSG_ACK,
    MSG_ERROR,
    MSG_PING,
    ModeProbe,
    Session,
    expect_error,
    open_vendor_interface,
    report,
    warn_if_daemon_running,
)

CMD_GET_VERSION = 1
CMD_ENTER_BOOTLOADER = 2
CMD_GET_INFO = 3

ERR_BAD_MAGIC = 2

FLAG_DEVICE_HEARTBEAT = 0x01
FLAG_DEV_BUILD = 0x02
FLAG_HAS_VERSION = 0x04
FLAG_HAS_MODEL = 0x08

BOOTLOADER_MAGIC = 0xB007
WRONG_MAGIC = 0x1234

MODEL_PIXEL_PUMP_1 = 1
PROTOCOL_V2 = 2

BOOTSEL_VOLUME = "/Volumes/RPI-RP2"


def version_of(frame):
    return (frame[4], frame[5], frame[6])


def fmt(version):
    return "{}.{}.{}".format(*version)


def read_heartbeat(session, timeout_s=3.0):
    """A device heartbeat, not our own -- HOST_HEARTBEAT is the echo of ours."""
    return session.read(
        timeout_s,
        want=(MSG_PING,),
        match=lambda f: bool(f[7] & FLAG_DEVICE_HEARTBEAT),
    )


# --------------------------------------------------------------- checks


def check_get_version(session, probe):
    problems = []

    session.command(CMD_GET_VERSION)
    ack = session.read(2.0, want=(MSG_ACK,), match=lambda f: f[3] == CMD_GET_VERSION)
    if ack is None:
        return report("GET_VERSION", ["no ACK"]), None

    wire_version = version_of(ack)
    if not ack[7] & FLAG_HAS_VERSION:
        problems.append("HAS_VERSION not set on the ACK")
    print(f"    GET_VERSION -> {fmt(wire_version)}"
          + ("  (dev build)" if ack[7] & FLAG_DEV_BUILD else ""))

    beat = read_heartbeat(session)
    if beat is None:
        problems.append("no device heartbeat inside 3 s")
    else:
        beat_version = version_of(beat)
        if beat_version != wire_version:
            problems.append(
                f"heartbeat says {fmt(beat_version)}, GET_VERSION says {fmt(wire_version)}"
            )

    # The legacy stdin path is a genuinely independent route to the same fact:
    # it reads version.py through the CDC port, not the HID stack.
    if probe.available:
        info = probe.version_info()
        if info is None:
            problems.append("version:info did not answer on the CDC port")
        else:
            print(f"    version:info -> {info}")
            tag = info.split(",")[0].strip()
            if tag == "local":
                print("    (local build -- version.py has placeholders, "
                      "so 0.0.0 on the wire is expected)")
            else:
                expected = tag.lstrip("v").split("-")[0]
                if expected != fmt(wire_version):
                    problems.append(
                        f"version:info tag {tag} disagrees with the wire {fmt(wire_version)}"
                    )
    else:
        print("    (CDC port unavailable -- skipped the version:info cross-check)")

    return report("GET_VERSION agrees across heartbeat and CDC", problems), wire_version


def check_get_info(session):
    problems = []

    session.command(CMD_GET_INFO)
    ack = session.read(2.0, want=(MSG_ACK,), match=lambda f: f[3] == CMD_GET_INFO)
    if ack is None:
        return report("GET_INFO", ["no ACK"])

    model, protocol_level = ack[4], ack[5]
    print(f"    GET_INFO -> model {model}, protocol {protocol_level}")

    if model != MODEL_PIXEL_PUMP_1:
        problems.append(f"model is {model}, expected {MODEL_PIXEL_PUMP_1}")
    if protocol_level != PROTOCOL_V2:
        problems.append(f"protocol level is {protocol_level}, expected {PROTOCOL_V2}")
    if not ack[7] & FLAG_HAS_MODEL:
        problems.append("HAS_MODEL not set on the ACK")

    beat = read_heartbeat(session)
    if beat is None:
        problems.append("no device heartbeat inside 3 s")
    else:
        if beat[3] != MODEL_PIXEL_PUMP_1:
            problems.append(f"heartbeat model is {beat[3]}, expected {MODEL_PIXEL_PUMP_1}")
        if not beat[7] & FLAG_HAS_MODEL:
            problems.append("heartbeat does not set HAS_MODEL")

    return report("GET_INFO reports model 1, protocol 2", problems)


def check_bad_magic_does_not_reboot(session, probe):
    """A wrong magic must be refused *and* leave the pump running.

    The error frame alone does not prove that: the interesting failure is a
    device that answers correctly and reboots anyway. So this also confirms the
    pump is still there afterwards.
    """
    problems = []

    session.command(
        CMD_ENTER_BOOTLOADER,
        b5=WRONG_MAGIC & 0xFF,
        b6=(WRONG_MAGIC >> 8) & 0xFF,
    )
    frame = session.read(
        2.0, want=(MSG_ERROR, MSG_ACK), match=lambda f: f[3] == CMD_ENTER_BOOTLOADER
    )

    if frame is not None and frame[1] == MSG_ACK:
        problems.append("ACKed a wrong magic -- the pump is about to reboot")
    else:
        # Error code is a u16 at bytes 5-6, not byte 4; let phase4's decoder own that.
        wrong = expect_error(frame, ERR_BAD_MAGIC, "wrong magic")
        if wrong:
            problems.append(wrong)
        else:
            print(f"    wrong magic {WRONG_MAGIC:#06x} -> ERROR BAD_MAGIC")

    # Still alive? Give it longer than the 100 ms flush delay a real
    # ENTER_BOOTLOADER would have taken before rebooting.
    time.sleep(0.5)
    if read_heartbeat(session) is None:
        problems.append("pump stopped heartbeating -- it may have rebooted anyway")
    else:
        print("    pump still heartbeating afterwards")

    if os.path.isdir(BOOTSEL_VOLUME):
        problems.append(f"{BOOTSEL_VOLUME} appeared -- the pump entered BOOTSEL")

    return report("ENTER_BOOTLOADER refuses a wrong magic without rebooting", problems)


def check_enter_bootloader(session):
    problems = []

    session.command(
        CMD_ENTER_BOOTLOADER,
        b5=BOOTLOADER_MAGIC & 0xFF,
        b6=(BOOTLOADER_MAGIC >> 8) & 0xFF,
    )
    ack = session.read(
        2.0, want=(MSG_ACK, MSG_ERROR), match=lambda f: f[3] == CMD_ENTER_BOOTLOADER
    )

    if ack is None:
        problems.append("no answer to ENTER_BOOTLOADER")
    elif ack[1] == MSG_ERROR:
        code = ack[5] | (ack[6] << 8)
        problems.append(f"refused the correct magic with error {code}")
    else:
        print(f"    magic {BOOTLOADER_MAGIC:#06x} -> ACK, waiting for BOOTSEL")

    # The spec requires >= 100 ms between the ACK and the reboot so the ACK
    # actually reaches the host. Then macOS has to enumerate and mount.
    session.stop_keepalive()
    for _ in range(200):
        if os.path.isdir(BOOTSEL_VOLUME):
            break
        time.sleep(0.1)

    if os.path.isdir(BOOTSEL_VOLUME):
        print(f"    {BOOTSEL_VOLUME} mounted")
    else:
        problems.append(f"{BOOTSEL_VOLUME} did not appear within 20 s")

    return report("ENTER_BOOTLOADER lands the device in BOOTSEL", problems)


# --------------------------------------------------------------- driver


def main():
    warn_if_daemon_running()

    if os.path.isdir(BOOTSEL_VOLUME):
        sys.exit(
            f"{BOOTSEL_VOLUME} is already mounted -- the pump is sitting in the\n"
            "bootloader. Power-cycle it (or copy a UF2 across) and run this again."
        )

    device = open_vendor_interface()
    session = Session(device)
    session.start_keepalive()

    probe = ModeProbe()

    print("\nPhase 6 acceptance -- device side\n")
    results = []
    try:
        print("[Check A] Firmware version, three ways")
        ok, _ = check_get_version(session, probe)
        results.append(ok)

        print("\n[Check B] Model and protocol level")
        results.append(check_get_info(session))

        print("\n[Check C] ENTER_BOOTLOADER rejects a wrong magic")
        results.append(check_bad_magic_does_not_reboot(session, probe))

        print("\n[Check D] ENTER_BOOTLOADER, for real")
        print("    This reboots the pump into BOOTSEL. Recover with a power cycle,")
        print("    or by copying firmware.uf2 onto the RPI-RP2 volume.")
        answer = input("    Run it? [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            # The prompt above blocked for far longer than the 1200 ms host
            # timeout, but the keepalive thread has been beating throughout --
            # that is the whole reason it exists. See phase4_wire_check.
            results.append(check_enter_bootloader(session))
        else:
            print("  SKIP  ENTER_BOOTLOADER (not run)")
    finally:
        session.stop_keepalive()
        probe.close()
        try:
            device.close()
        except hid.HIDException:
            pass

    print("\n" + ("ALL CHECKS PASSED" if all(results) else "SOME CHECKS FAILED"))
    if os.path.isdir(BOOTSEL_VOLUME):
        print("Pump is in BOOTSEL -- power-cycle it, or copy a UF2 across.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
