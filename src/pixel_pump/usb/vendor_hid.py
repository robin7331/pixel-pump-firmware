import utime
from usb.device.hid import HIDInterface

from . import protocol

_VENDOR_REPORT_DESC = (
    b"\x06\x00\xff"  # Usage Page (Vendor-Defined 0xFF00)
    b"\x09\x01"  # Usage (0x01)
    b"\xa1\x01"  # Collection (Application)
    b"\x15\x00"  # Logical Minimum (0)
    b"\x26\xff\x00"  # Logical Maximum (255)
    b"\x75\x08"  # Report Size (8)
    b"\x95\x08"  # Report Count (8)
    b"\x09\x01"  # Usage (0x01)
    b"\x81\x02"  # Input (Data, Variable, Absolute)
    b"\x95\x08"  # Report Count (8)
    b"\x09\x01"  # Usage (0x01)
    b"\x91\x02"  # Output (Data, Variable, Absolute)
    b"\xc0"  # End Collection
)


class VendorHIDInterface(HIDInterface):
    def __init__(
        self,
        on_frame_received=None,
        host_activity_timeout_ms=1200,
        host_open_grace_ms=0,
        debug=False,
    ):
        self._set_report_buf = bytearray(protocol.REPORT_SIZE)
        super().__init__(
            _VENDOR_REPORT_DESC,
            set_report_buf=self._set_report_buf,
            interface_str="Pixel Pump Vendor HID",
        )
        self._on_frame_received = on_frame_received
        self._seq = 0
        self._last_host_rx_ms = None
        self._host_activity_timeout_ms = max(1, int(host_activity_timeout_ms))
        self._host_open_grace_ms = max(0, int(host_open_grace_ms))
        self._opened_at_ms = None
        self._was_open = False
        self._is_host_active = False
        self.debug = debug

    def on_set_report(self, report_data, report_id, report_type):
        self._last_host_rx_ms = utime.ticks_ms()
        self._is_host_active = True
        frame = bytes(report_data[: protocol.REPORT_SIZE])

        if self.debug:
            print("USB Vendor RX:", frame)

        if self._on_frame_received:
            self._on_frame_received(frame, report_id, report_type)

    def _send_frame(self, frame, timeout_ms=0):
        if not self.is_open():
            return False

        if self.send_report(frame, timeout_ms=timeout_ms):
            self._seq = (self._seq + 1) & 0xFF
            return True

        return False

    def send_event(self, control_id, event_kind, value=0, flags=0, timeout_ms=0):
        frame = protocol.encode_event_frame(
            self._seq, control_id, event_kind, value=value, flags=flags
        )
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def send_heartbeat(
        self, version_tuple, dev=False, model_id=protocol.ModelId.UNKNOWN, timeout_ms=0
    ):
        frame = protocol.encode_heartbeat_frame(
            self._seq, version_tuple, dev=dev, model_id=model_id
        )
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def send_ack(self, command_id, payload=(0, 0, 0), flags=0, timeout_ms=0):
        frame = protocol.encode_ack_frame(
            self._seq, command_id, payload=payload, flags=flags
        )
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def send_mapping(self, control_id, slot, gesture, action, param, timeout_ms=0):
        frame = protocol.encode_mapping_frame(
            self._seq, control_id, slot, gesture, action, param
        )
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def send_mapping_end(self, timeout_ms=0):
        frame = protocol.encode_mapping_end_frame(self._seq)
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def send_error(self, command_id, error_code, timeout_ms=0):
        frame = protocol.encode_error_frame(self._seq, command_id, error_code)
        return self._send_frame(frame, timeout_ms=timeout_ms)

    def is_host_open(self):
        return self.is_open()

    def is_host_active(self):
        return self._is_host_active

    def tick(self):
        is_open = self.is_open()

        if is_open != self._was_open:
            self._was_open = is_open
            if is_open:
                self._opened_at_ms = utime.ticks_ms()
                # A host that opens the interface and writes straight away gets
                # its report in before this first tick observes the open edge.
                # The close edge already cleared the previous session's state,
                # so never discard an RX that has already landed.
                if self._last_host_rx_ms is None:
                    self._is_host_active = self._host_open_grace_ms > 0
            else:
                self._opened_at_ms = None
                self._last_host_rx_ms = None
                self._is_host_active = False
                return None

        if not is_open:
            self._is_host_active = False
            return None

        now_ms = utime.ticks_ms()

        if self._last_host_rx_ms is not None:
            idle_ms = utime.ticks_diff(now_ms, self._last_host_rx_ms)
            self._is_host_active = idle_ms <= self._host_activity_timeout_ms
            return None

        if self._opened_at_ms is None:
            self._opened_at_ms = now_ms

        if self._host_open_grace_ms <= 0:
            self._is_host_active = False
            return None

        open_ms = utime.ticks_diff(now_ms, self._opened_at_ms)
        self._is_host_active = open_ms <= self._host_open_grace_ms
        return None
