"""Verdicts and prompts.

Split out from `transport.py` so `checks_static.py` can reach it without
dragging in hidapi -- the source checks are meant to run anywhere, including a
CI box with no pump and no `hid` module installed.
"""

import sys

from .firmware import action_name, control_name, gesture_name


def report(name, problems):
    if problems:
        print(f"  FAIL  {name}: " + "; ".join(problems))
        return False
    print(f"  PASS  {name}")
    return True


def describe(entry):
    control, slot, gesture, action, param = entry
    return (
        f"{control_name(control)} "
        f"{'CONNECTED' if slot else 'STANDALONE'} "
        f"{gesture_name(gesture)} -> "
        f"{action_name(action)}" + (f"({param:#04x})" if param else "")
    )


def unattended():
    """True when nobody can be asked to press a pedal."""
    return "--auto" in sys.argv[1:] or not sys.stdin.isatty()


def ask(question):
    """Yes/no. Always False when unattended, so callers skip rather than raise."""
    if unattended():
        return False
    while True:
        answer = input(f"    {question} [y/n] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
