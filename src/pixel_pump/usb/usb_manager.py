import machine
import usb.device
import utime

from .keyboard import Keyboard
from .protocol import (
    BOOTLOADER_MAGIC,
    MAPPING_ALL,
    PROTOCOL_VERSION,
    REPORT_SIZE,
    RESET_MAPPINGS_MAGIC,
    CommandId,
    ErrorCode,
    EventKind,
    Flags,
    MappingSlot,
    MessageType,
    ModelId,
    decode_frame,
    decode_slot_gesture,
    info_ack_payload,
)
from .vendor_hid import VendorHIDInterface

try:
    from ..version import dev as FW_DEV_BUILD
    from ..version import version_tuple as FW_VERSION
except ImportError:
    # version.py predating the version_tuple/dev fields
    FW_VERSION = (0, 0, 0)
    FW_DEV_BUILD = True

# Which model this firmware is. Lives here rather than in protocol.py so that
# file stays byte-identical with the Pixel Pump 2 copy.
MODEL_ID = ModelId.PIXEL_PUMP_1


class USBConnectionState:
    NO_DATA = "no_data"
    KEYBOARD_ONLY = "keyboard_only"
    VENDOR_HID = "vendor_hid"


class USBManager:
    """Composite keyboard + vendor HID, and the host command handler.

    ``keyboard_enabled=False`` builds the device without the keyboard
    interface. It is read from settings.json before this object is constructed,
    because enumeration happens in here and cannot be revisited afterwards --
    the setting takes effect on the next boot, never on the running device.

    The mapping commands (GET_MAPPING, SET_MAPPING, RESET_MAPPINGS,
    COMMIT_MAPPINGS) delegate to the optional ``mapping`` object, which must
    provide:

        has_control(control) -> bool
        has_gesture(control, gesture) -> bool
        is_valid_action(control, gesture, action, param) -> bool
        get(control, gesture, slot) -> (action, param)
        set(control, gesture, slot, action, param) -> None
        entries() -> iterable of (control, slot, gesture, action, param),
                     excluding NONE actions
        reset() -> bool   (restore defaults and persist)
        commit() -> bool  (persist the in-RAM table)

    With no mapping table wired up the four commands answer
    ``ERROR UNKNOWN_COMMAND``, which is exactly how the spec's compatibility
    matrix describes a device without mapping support. Phase 4 supplies the
    table; nothing here changes when it does.
    """

    def __init__(
        self,
        usb_interface_active=True,
        keyboard_enabled=True,
        mapping=None,
        hold_repeat_ms=120,
        max_queue_size=32,
        # A bulk GET_MAPPING dump of a fully populated PP1 table is 8 controls
        # x 2 slots x 4 gestures = 64 frames plus the terminator, and dropping
        # the terminator leaves the host waiting forever.
        max_response_queue_size=96,
        vendor_host_activity_timeout_ms=1200,
        vendor_host_open_grace_ms=0,
        device_heartbeat_interval_ms=500,
        bootloader_flush_delay_ms=150,
        on_usb_data_connection_changed=None,
        on_usb_connection_state_changed=None,
        debug=False,
    ):
        self.debug = debug
        self.mapping = mapping
        self.hold_repeat_ms = hold_repeat_ms
        self.max_queue_size = max_queue_size
        self.max_response_queue_size = max_response_queue_size
        self.device_heartbeat_interval_ms = max(1, int(device_heartbeat_interval_ms))
        # Spec requires >= 100ms between the ENTER_BOOTLOADER ACK and the reboot
        self.bootloader_flush_delay_ms = max(100, int(bootloader_flush_delay_ms))
        self.on_usb_data_connection_changed = on_usb_data_connection_changed
        self.on_usb_connection_state_changed = on_usb_connection_state_changed

        self.keyboard = Keyboard(enabled=keyboard_enabled)
        self.vendor = VendorHIDInterface(
            on_frame_received=self._on_vendor_frame_received,
            host_activity_timeout_ms=vendor_host_activity_timeout_ms,
            host_open_grace_ms=vendor_host_open_grace_ms,
            debug=debug,
        )

        if usb_interface_active:
            # builtin_driver keeps the CDC/REPL interface alive, which the
            # legacy stdin protocol in CommunicationManager still needs.
            #
            # With the keyboard disabled its interface is simply left out of the
            # descriptor set, so the device presents no keyboard top-level
            # collection and macOS has nothing to run its Keyboard Setup
            # Assistant for. The vendor interface is unaffected beyond taking a
            # lower interface number; hosts find it by usage page.
            if keyboard_enabled:
                usb.device.get().init(
                    self.keyboard.kb, self.vendor, builtin_driver=True
                )
            else:
                usb.device.get().init(self.vendor, builtin_driver=True)

        self._was_vendor_open = False
        self._was_vendor_active = False
        self._has_usb_data_connection = False
        self._connection_state = USBConnectionState.NO_DATA
        self._event_queue = []
        self._response_queue = []
        self._last_hold_sent_ms = {}
        self._last_device_heartbeat_sent_ms = None
        self._bootloader_at_ms = None

    def publish_event(self, control_id, event_kind, value=0, flags=0):
        # Publish-all rule: while the vendor host is active every control is
        # published, whatever the mapping table says it does locally.
        if event_kind == EventKind.HOLD and not self._should_emit_hold(control_id):
            return

        if self.vendor.is_host_active():
            self._enqueue_event(control_id, event_kind, value, flags)
        elif self._event_queue:
            self._event_queue.clear()

    def tick(self):
        if self._bootloader_at_ms is not None:
            if utime.ticks_diff(utime.ticks_ms(), self._bootloader_at_ms) >= 0:
                machine.bootloader()

        self.keyboard.tick()
        self.vendor.tick()

        vendor_open = self.vendor.is_host_open()
        vendor_active = self.vendor.is_host_active()
        keyboard_open = self.keyboard.is_host_open()
        self._update_connection_state(keyboard_open, vendor_open, vendor_active)

        if vendor_open != self._was_vendor_open:
            self._was_vendor_open = vendor_open
            if self.debug:
                print("USB Vendor Host Open:", vendor_open)
            if not vendor_open:
                self._last_device_heartbeat_sent_ms = None
                self._response_queue.clear()

        if vendor_active != self._was_vendor_active:
            self._was_vendor_active = vendor_active
            if self.debug:
                print("USB Vendor Host Active:", vendor_active)
            if not vendor_active and self._event_queue:
                self._event_queue.clear()

        self._send_device_heartbeat_if_due(vendor_open)

        if vendor_open:
            self._flush_response_queue()

        if not vendor_active:
            return

        sent = 0
        while self._event_queue and sent < 4:
            control_id, event_kind, value, flags = self._event_queue[0]
            if self.vendor.send_event(
                control_id, event_kind, value=value, flags=flags, timeout_ms=0
            ):
                self._event_queue.pop(0)
                sent += 1
            else:
                break

    def is_vendor_host_open(self):
        return self.vendor.is_host_open()

    def is_vendor_host_active(self):
        return self.vendor.is_host_active()

    def has_usb_data_connection(self):
        return self._has_usb_data_connection

    def get_connection_state(self):
        return self._connection_state

    def refresh_connection_state(self, notify=False):
        self.vendor.tick()
        keyboard_open = self.keyboard.is_host_open()
        vendor_open = self.vendor.is_host_open()
        vendor_active = self.vendor.is_host_active()
        return self._update_connection_state(
            keyboard_open, vendor_open, vendor_active, notify=notify
        )

    def _should_emit_hold(self, control_id):
        now_ms = utime.ticks_ms()
        last_ms = self._last_hold_sent_ms.get(control_id, None)
        if (
            last_ms is not None
            and utime.ticks_diff(now_ms, last_ms) < self.hold_repeat_ms
        ):
            return False
        self._last_hold_sent_ms[control_id] = now_ms
        return True

    def _enqueue_event(self, control_id, event_kind, value, flags):
        if (
            event_kind == EventKind.DELTA
            and self._event_queue
            and self._event_queue[-1][0] == control_id
            and self._event_queue[-1][1] == EventKind.DELTA
        ):
            prev_control, prev_event, prev_value, prev_flags = self._event_queue[-1]
            merged_value = prev_value + value
            if merged_value > 32767:
                merged_value = 32767
            elif merged_value < -32768:
                merged_value = -32768
            self._event_queue[-1] = (
                prev_control,
                prev_event,
                merged_value,
                prev_flags | flags,
            )
            return

        if len(self._event_queue) >= self.max_queue_size:
            self._event_queue.pop(0)

        self._event_queue.append((control_id, event_kind, value, flags))

    def _on_vendor_frame_received(self, frame, report_id, report_type):
        if self.debug:
            try:
                decoded = decode_frame(frame)
                print(
                    "USB Vendor Frame:",
                    decoded,
                    "report_id=",
                    report_id,
                    "report_type=",
                    report_type,
                )
            except ValueError:
                print("USB Vendor Raw:", frame)

        if len(frame) == REPORT_SIZE and frame[1] == MessageType.COMMAND:
            self._handle_command(frame)

    def _handle_command(self, frame):
        command_id = frame[3]

        if command_id == CommandId.GET_VERSION:
            self._enqueue_ack(
                CommandId.GET_VERSION,
                payload=FW_VERSION,
                flags=self._version_flags(),
            )
        elif command_id == CommandId.ENTER_BOOTLOADER:
            magic = frame[5] | (frame[6] << 8)
            if magic == BOOTLOADER_MAGIC:
                self._enqueue_ack(CommandId.ENTER_BOOTLOADER, arm_bootloader=True)
            else:
                self._enqueue_error(CommandId.ENTER_BOOTLOADER, ErrorCode.BAD_MAGIC)
        elif command_id == CommandId.GET_INFO:
            self._enqueue_ack(
                CommandId.GET_INFO,
                payload=info_ack_payload(MODEL_ID, PROTOCOL_VERSION),
                flags=Flags.HAS_MODEL,
            )
        elif command_id == CommandId.GET_MAPPING:
            self._handle_get_mapping(frame)
        elif command_id == CommandId.SET_MAPPING:
            self._handle_set_mapping(frame)
        elif command_id == CommandId.RESET_MAPPINGS:
            self._handle_reset_mappings(frame)
        elif command_id == CommandId.COMMIT_MAPPINGS:
            self._handle_commit_mappings()
        else:
            self._enqueue_error(command_id, ErrorCode.UNKNOWN_COMMAND)

    def _handle_get_mapping(self, frame):
        if not self._require_mapping(CommandId.GET_MAPPING):
            return

        control_id = frame[4]
        slot, gesture = decode_slot_gesture(frame[5])

        if control_id == MAPPING_ALL:
            for entry in self.mapping.entries():
                self._enqueue_mapping(entry[0], entry[1], entry[2], entry[3], entry[4])
            self._enqueue_mapping_end()
            return

        if not self._check_target(CommandId.GET_MAPPING, control_id, slot, gesture):
            return

        action, param = self.mapping.get(control_id, gesture, slot)
        self._enqueue_mapping(control_id, slot, gesture, action, param)

    def _handle_set_mapping(self, frame):
        if not self._require_mapping(CommandId.SET_MAPPING):
            return

        control_id = frame[4]
        slot, gesture = decode_slot_gesture(frame[5])
        action = frame[6]
        param = frame[7]

        if not self._check_target(CommandId.SET_MAPPING, control_id, slot, gesture):
            return

        if not self.mapping.is_valid_action(control_id, gesture, action, param):
            self._enqueue_error(CommandId.SET_MAPPING, ErrorCode.BAD_ACTION)
            return

        # RAM only -- COMMIT_MAPPINGS persists.
        self.mapping.set(control_id, gesture, slot, action, param)
        self._enqueue_ack(CommandId.SET_MAPPING)

    def _handle_reset_mappings(self, frame):
        if not self._require_mapping(CommandId.RESET_MAPPINGS):
            return

        magic = frame[5] | (frame[6] << 8)
        if magic != RESET_MAPPINGS_MAGIC:
            self._enqueue_error(CommandId.RESET_MAPPINGS, ErrorCode.BAD_MAGIC)
            return

        if self.mapping.reset():
            self._enqueue_ack(CommandId.RESET_MAPPINGS)
        else:
            self._enqueue_error(CommandId.RESET_MAPPINGS, ErrorCode.STORAGE_ERROR)

    def _handle_commit_mappings(self):
        if not self._require_mapping(CommandId.COMMIT_MAPPINGS):
            return

        if self.mapping.commit():
            self._enqueue_ack(CommandId.COMMIT_MAPPINGS)
        else:
            self._enqueue_error(CommandId.COMMIT_MAPPINGS, ErrorCode.STORAGE_ERROR)

    def _require_mapping(self, command_id):
        if self.mapping is None:
            self._enqueue_error(command_id, ErrorCode.UNKNOWN_COMMAND)
            return False
        return True

    def _check_target(self, command_id, control_id, slot, gesture):
        if not self.mapping.has_control(control_id):
            self._enqueue_error(command_id, ErrorCode.BAD_CONTROL)
            return False

        # Slot rides in the gesture byte and the error enum is frozen with no
        # BAD_SLOT, so an out-of-range slot reports as BAD_GESTURE.
        if slot > MappingSlot.CONNECTED:
            self._enqueue_error(command_id, ErrorCode.BAD_GESTURE)
            return False

        if not self.mapping.has_gesture(control_id, gesture):
            self._enqueue_error(command_id, ErrorCode.BAD_GESTURE)
            return False

        return True

    def _version_flags(self):
        flags = Flags.HAS_VERSION
        if FW_DEV_BUILD:
            flags |= Flags.DEV_BUILD
        return flags

    def _enqueue_response(self, entry):
        if len(self._response_queue) >= self.max_response_queue_size:
            if self.debug:
                print("USB response queue full, dropped:", entry[0])
            return False
        self._response_queue.append(entry)
        return True

    def _enqueue_ack(self, command_id, payload=(0, 0, 0), flags=0, arm_bootloader=False):
        self._enqueue_response(("ack", command_id, payload, flags, arm_bootloader))

    def _enqueue_error(self, command_id, error_code):
        self._enqueue_response(("error", command_id, error_code))

    def _enqueue_mapping(self, control_id, slot, gesture, action, param):
        self._enqueue_response(("mapping", control_id, slot, gesture, action, param))

    def _enqueue_mapping_end(self):
        self._enqueue_response(("mapping_end",))

    def _flush_response_queue(self):
        while self._response_queue:
            entry = self._response_queue[0]
            kind = entry[0]

            if kind == "ack":
                sent = self.vendor.send_ack(
                    entry[1], payload=entry[2], flags=entry[3], timeout_ms=0
                )
            elif kind == "error":
                sent = self.vendor.send_error(entry[1], entry[2], timeout_ms=0)
            elif kind == "mapping":
                sent = self.vendor.send_mapping(
                    entry[1], entry[2], entry[3], entry[4], entry[5], timeout_ms=0
                )
            else:
                sent = self.vendor.send_mapping_end(timeout_ms=0)

            if not sent:
                return

            self._response_queue.pop(0)

            if kind == "ack" and entry[4]:
                self._bootloader_at_ms = utime.ticks_add(
                    utime.ticks_ms(), self.bootloader_flush_delay_ms
                )

    def _resolve_connection_state(self, keyboard_open, vendor_active):
        # With keyboard_enabled False there is no keyboard interface to open,
        # so keyboard_open is always False and KEYBOARD_ONLY never resolves --
        # such a pump reports NO_DATA until a vendor host talks to it. That is
        # the truth about the device, not a gap in the state machine.
        if vendor_active:
            return USBConnectionState.VENDOR_HID
        if keyboard_open:
            return USBConnectionState.KEYBOARD_ONLY
        return USBConnectionState.NO_DATA

    def _update_connection_state(
        self, keyboard_open, vendor_open, vendor_active, notify=True
    ):
        has_data_connection = keyboard_open or vendor_open
        connection_state = self._resolve_connection_state(keyboard_open, vendor_active)

        data_changed = has_data_connection != self._has_usb_data_connection
        state_changed = connection_state != self._connection_state

        self._has_usb_data_connection = has_data_connection
        self._connection_state = connection_state

        if notify and data_changed and self.on_usb_data_connection_changed:
            self.on_usb_data_connection_changed(self, has_data_connection)

        if notify and state_changed and self.on_usb_connection_state_changed:
            self.on_usb_connection_state_changed(self, connection_state)

        return connection_state

    def _send_device_heartbeat_if_due(self, vendor_open):
        if not vendor_open:
            return

        now_ms = utime.ticks_ms()
        if self._last_device_heartbeat_sent_ms is not None:
            elapsed_ms = utime.ticks_diff(now_ms, self._last_device_heartbeat_sent_ms)
            if elapsed_ms < self.device_heartbeat_interval_ms:
                return

        if self.vendor.send_heartbeat(
            FW_VERSION, dev=FW_DEV_BUILD, model_id=MODEL_ID, timeout_ms=0
        ):
            self._last_device_heartbeat_sent_ms = now_ms
