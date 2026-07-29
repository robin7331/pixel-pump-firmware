#!/usr/bin/env python3
"""Hardware checks for the Pixel Pump 1 firmware -- the only entry point.

CPython, not MicroPython: this runs on a developer's machine, like
generateVersionFile.py. The checks themselves live in `tools/pumpcheck/`, one
module per feature area, and they share their protocol vocabulary with the
firmware by importing `src/pixel_pump/usb/protocol.py` directly.

    # everything, in a safe order
    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \
        python tools/pump_check.py

    # one area
    ... python tools/pump_check.py mapping

    # no pump, no hardware, runnable in CI
    python tools/pump_check.py static

    # skip everything that needs a person
    ... python tools/pump_check.py --auto

Needs hidapi (`brew install hidapi`) for anything but `static`. If opening the
vendor interface fails with "exclusive access and device already open", the
likely cause is Board Factory's `pixel-pump-daemon` holding it -- check
`pgrep -fl pixel-pump-daemon` before assuming a permissions problem.

**When a feature changes, change the check.** The default mapping table is
guarded automatically: `static` diffs `checks_static.EXPECTED_DEFAULTS` against
`src/pixel_pump/mapping.py` and fails loudly if they part ways. Nothing else is
automatic, so a new control, gesture, command or settings key wants a matching
check in the group that owns it.
"""

import importlib
import os
import sys

# Running this as a path (`python tools/pump_check.py`) already puts tools/ on
# sys.path; being explicit also covers being invoked from elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Canonical order, and each name maps to `pumpcheck/checks_<name>.py`.
# `identity` runs last because its opt-in check ends with the pump sitting in
# BOOTSEL, which no later group could talk to.
GROUPS = ("static", "wire", "mapping", "keyboard", "identity")


def load(group):
    """Import a group on demand.

    Lazily, so `static` still runs on a machine with no hidapi -- that group
    touches no hardware and is the one CI can use.
    """
    return importlib.import_module(f"pumpcheck.checks_{group}")


def usage():
    print(__doc__.strip())
    print("\nGroups:\n")
    for group in GROUPS:
        try:
            module = load(group)
        except ImportError as exc:
            print(f"  {group:<10} (unavailable: {exc})")
            continue
        print(f"  {group:<10} {module.TITLE}")
    print("\n  all        every group, in the order above (the default)")


def parse_args(argv):
    """Returns the group names to run, or None to print usage and stop."""
    names = [arg for arg in argv if not arg.startswith("-")]
    flags = [arg for arg in argv if arg.startswith("-")]

    for flag in flags:
        if flag not in ("--auto",):
            return None

    if not names or "all" in names:
        return list(GROUPS)

    unknown = [name for name in names if name not in GROUPS]
    if unknown:
        print(f"Unknown group(s): {', '.join(unknown)}\n")
        return None

    # Canonical order regardless of how they were typed -- see GROUPS.
    return [group for group in GROUPS if group in names]


def main(argv):
    if "--list" in argv or "-h" in argv or "--help" in argv:
        usage()
        return 0

    selected = parse_args(argv)
    if selected is None:
        usage()
        return 2

    try:
        modules = [load(group) for group in selected]
    except ImportError as exc:
        print(f"Could not load a check group: {exc}")
        print("\nEverything but `static` needs hidapi -- try:")
        print("    brew install hidapi")
        print("    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \\")
        print("        python tools/pump_check.py")
        return 2

    from pumpcheck.reporting import unattended  # noqa: PLC0415 - hid-free import

    if unattended():
        print("Unattended run: skipping every check that needs someone at the pump.\n")

    needs_pump = any(module.NEEDS_PUMP for module in modules)
    rig = None
    results = []

    if needs_pump:
        from pumpcheck.rig import Rig  # noqa: PLC0415 - needs hidapi
        from pumpcheck.transport import warn_if_daemon_running  # noqa: PLC0415

        warn_if_daemon_running()
        rig = Rig()
        if not rig.open():
            print(f"!! No CDC port ({rig.cdc.reason}). Checks that read settings.json")
            print("   will report themselves as unable to run.\n")
        print(f"Opened {rig.device.manufacturer} {rig.device.product}\n")

    try:
        for module in modules:
            print(f"=== {module.TITLE} ===\n")
            outcome = module.run(rig) if module.NEEDS_PUMP else module.run()
            results.extend(outcome)
            print("")
    finally:
        if rig is not None:
            rig.close()

    passed = all(results)
    print(f"{sum(1 for r in results if r)}/{len(results)} checks passed")
    print("ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
