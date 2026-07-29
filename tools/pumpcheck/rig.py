"""The pump, as one object: vendor session plus CDC port, reopened together.

Check groups take a `Rig` rather than a bare device, because two of them need
the pump to survive a reboot mid-run and reconnecting is fiddly enough to be
worth doing in exactly one place.
"""

import time

from .transport import (
    ModeProbe,
    Session,
    open_vendor_interface,
    vendor_present,
)

# The boot sequence (rainbow sweep + valve clicks) runs before the main loop, so
# give a rebooted pump time to become answerable rather than racing it.
BOOT_SETTLE_S = 2.0
ENUMERATION_TIMEOUT_S = 30.0


def wait_for_vendor(present, timeout_s=ENUMERATION_TIMEOUT_S):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if vendor_present() == present:
            return True
        time.sleep(0.25)
    return False


class Rig:
    """Vendor HID session + CDC probe, opened and reopened as a pair."""

    def __init__(self):
        self.device = None
        self.session = None
        self.cdc = None

    # ------------------------------------------------------------- lifecycle

    def open(self, settle_s=1.5):
        """Open both halves. Returns True when the CDC port came up too."""
        self.device = open_vendor_interface()
        self.session = Session(self.device)
        self.session.heartbeat(force=True)
        self.session.idle(settle_s)
        self.cdc = ModeProbe()
        return self.cdc.available

    def close(self):
        if self.session is not None:
            self.session.stop_keepalive()
            self.session = None
        if self.device is not None:
            try:
                self.device.close()
            except Exception:  # noqa: BLE001 - already gone is fine
                pass
            self.device = None
        if self.cdc is not None:
            self.cdc.close()
            self.cdc = None

    def release_device(self):
        """Drop the HID handle but keep the CDC port -- for a hand power-cycle."""
        if self.session is not None:
            self.session.stop_keepalive()
            self.session = None
        if self.device is not None:
            try:
                self.device.close()
            except Exception:  # noqa: BLE001
                pass
            self.device = None

    def reboot(self):
        """`reset:hard`, then wait the pump back onto the bus.

        False if it never came back. Everything is torn down first: the reset
        takes the CDC port with it, and a stale HID handle will not survive the
        re-enumeration either.
        """
        print("    Rebooting over CDC (reset:hard)...")
        self.release_device()

        self.cdc.reset_hard()
        self.cdc = None

        # Watch it leave first. If it never visibly leaves, macOS may just be
        # holding the entry -- say so and carry on rather than declaring success
        # on what could be the pre-reset enumeration.
        if not wait_for_vendor(False, timeout_s=10.0):
            print("    (never saw the device leave the bus -- continuing anyway)")
        if not wait_for_vendor(True):
            print(f"    Device did not re-enumerate within {ENUMERATION_TIMEOUT_S:.0f} s.")
            return False

        time.sleep(BOOT_SETTLE_S)
        self.open()
        print("    Back on the bus.")
        return True

    def reopen_after_power_cycle(self, attempts=20):
        """Reopen the vendor interface after someone unplugged the pump."""
        for _ in range(attempts):
            if vendor_present():
                try:
                    self.open()
                    return True
                except SystemExit:
                    pass
            time.sleep(1.0)
        return False
