try:
    from micropython import const
except ImportError:

    def const(value):
        return value


REPORT_SIZE = const(8)
PROTOCOL_VERSION = const(2)

BOOTLOADER_MAGIC = const(0xB007)
RESET_MAPPINGS_MAGIC = const(0xDEFA)

# Control 0xFF in a GET_MAPPING request asks for a bulk dump; the same value in
# a MAPPING frame marks the end of that dump.
MAPPING_ALL = const(0xFF)


class MessageType:
    EVENT = const(1)
    COMMAND = const(2)
    ACK = const(3)
    PING = const(4)
    MAPPING = const(7)
    ERROR = const(255)


class Flags:
    DEVICE_HEARTBEAT = const(0x01)
    DEV_BUILD = const(0x02)
    HAS_VERSION = const(0x04)
    HAS_MODEL = const(0x08)
    HOST_HEARTBEAT = const(0x80)


class ModelId:
    # Which firmware supplies which model is a per-device fact and lives in
    # usb_manager.py, so this file stays identical across both repos.
    UNKNOWN = const(0)
    PIXEL_PUMP_1 = const(1)
    PIXEL_PUMP_2 = const(2)


class CommandId:
    GET_VERSION = const(1)
    ENTER_BOOTLOADER = const(2)
    GET_INFO = const(3)
    GET_MAPPING = const(4)
    SET_MAPPING = const(5)
    RESET_MAPPINGS = const(6)
    COMMIT_MAPPINGS = const(7)


class ErrorCode:
    UNKNOWN_COMMAND = const(1)
    BAD_MAGIC = const(2)
    BAD_CONTROL = const(3)
    BAD_GESTURE = const(4)
    BAD_ACTION = const(5)
    STORAGE_ERROR = const(6)


class ControlId:
    ACTION = const(1)
    MENU = const(2)
    BACK = const(3)
    ZOOM = const(4)
    ENCODER = const(5)
    ENCODER_BUTTON = const(6)
    FPEDAL = const(7)
    FPEDAL_AUX = const(8)
    LIFT = const(10)
    DROP = const(11)
    LOW = const(12)
    HIGH = const(13)
    REVERSE = const(14)
    TRIGGER_BTN = const(15)


class EventKind:
    PRESS = const(1)
    RELEASE = const(2)
    TAP = const(3)
    HOLD = const(4)
    LONG_HOLD = const(5)
    DELTA = const(6)


class MappingSlot:
    STANDALONE = const(0)
    CONNECTED = const(1)


_CONTROL_NAMES = {
    ControlId.ACTION: "ACTION",
    ControlId.MENU: "MENU",
    ControlId.BACK: "BACK",
    ControlId.ZOOM: "ZOOM",
    ControlId.ENCODER: "ENCODER",
    ControlId.ENCODER_BUTTON: "ENCODER_BUTTON",
    ControlId.FPEDAL: "FPEDAL",
    ControlId.FPEDAL_AUX: "FPEDAL_AUX",
    ControlId.LIFT: "LIFT",
    ControlId.DROP: "DROP",
    ControlId.LOW: "LOW",
    ControlId.HIGH: "HIGH",
    ControlId.REVERSE: "REVERSE",
    ControlId.TRIGGER_BTN: "TRIGGER_BTN",
}

_EVENT_NAMES = {
    EventKind.PRESS: "PRESS",
    EventKind.RELEASE: "RELEASE",
    EventKind.TAP: "TAP",
    EventKind.HOLD: "HOLD",
    EventKind.LONG_HOLD: "LONG_HOLD",
    EventKind.DELTA: "DELTA",
}

_MESSAGE_NAMES = {
    MessageType.EVENT: "EVENT",
    MessageType.COMMAND: "COMMAND",
    MessageType.ACK: "ACK",
    MessageType.PING: "PING",
    MessageType.MAPPING: "MAPPING",
    MessageType.ERROR: "ERROR",
}

_COMMAND_NAMES = {
    CommandId.GET_VERSION: "GET_VERSION",
    CommandId.ENTER_BOOTLOADER: "ENTER_BOOTLOADER",
    CommandId.GET_INFO: "GET_INFO",
    CommandId.GET_MAPPING: "GET_MAPPING",
    CommandId.SET_MAPPING: "SET_MAPPING",
    CommandId.RESET_MAPPINGS: "RESET_MAPPINGS",
    CommandId.COMMIT_MAPPINGS: "COMMIT_MAPPINGS",
}

_ERROR_NAMES = {
    ErrorCode.UNKNOWN_COMMAND: "UNKNOWN_COMMAND",
    ErrorCode.BAD_MAGIC: "BAD_MAGIC",
    ErrorCode.BAD_CONTROL: "BAD_CONTROL",
    ErrorCode.BAD_GESTURE: "BAD_GESTURE",
    ErrorCode.BAD_ACTION: "BAD_ACTION",
    ErrorCode.STORAGE_ERROR: "STORAGE_ERROR",
}

_MODEL_NAMES = {
    ModelId.UNKNOWN: "UNKNOWN",
    ModelId.PIXEL_PUMP_1: "PIXEL_PUMP_1",
    ModelId.PIXEL_PUMP_2: "PIXEL_PUMP_2",
}


def _clamp_i16(value):
    if value < -32768:
        return -32768
    if value > 32767:
        return 32767
    return value


def control_name(control_id):
    return _CONTROL_NAMES.get(control_id, "UNKNOWN")


def event_name(event_kind):
    return _EVENT_NAMES.get(event_kind, "UNKNOWN")


def message_name(message_type):
    return _MESSAGE_NAMES.get(message_type, "UNKNOWN")


def command_name(command_id):
    return _COMMAND_NAMES.get(command_id, "UNKNOWN")


def error_name(error_code):
    return _ERROR_NAMES.get(error_code, "UNKNOWN")


def model_name(model_id):
    return _MODEL_NAMES.get(model_id, "UNKNOWN")


def encode_event_frame(seq, control_id, event_kind, value=0, flags=0):
    value = _clamp_i16(int(value))
    value_u16 = value & 0xFFFF
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.EVENT,
            seq & 0xFF,
            control_id & 0xFF,
            event_kind & 0xFF,
            value_u16 & 0xFF,
            (value_u16 >> 8) & 0xFF,
            flags & 0xFF,
        )
    )


def encode_ping_frame(seq, value=0, flags=0):
    value = _clamp_i16(int(value))
    value_u16 = value & 0xFFFF
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.PING,
            seq & 0xFF,
            0,
            0,
            value_u16 & 0xFF,
            (value_u16 >> 8) & 0xFF,
            flags & 0xFF,
        )
    )


def encode_heartbeat_frame(seq, version_tuple, dev=False, model_id=ModelId.UNKNOWN):
    flags = Flags.DEVICE_HEARTBEAT | Flags.HAS_VERSION
    if dev:
        flags |= Flags.DEV_BUILD
    # Legacy firmware sent model 0 and never set HAS_MODEL; keep that exact
    # shape when no model is supplied.
    if model_id != ModelId.UNKNOWN:
        flags |= Flags.HAS_MODEL
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.PING,
            seq & 0xFF,
            model_id & 0xFF,
            version_tuple[0] & 0xFF,
            version_tuple[1] & 0xFF,
            version_tuple[2] & 0xFF,
            flags,
        )
    )


def encode_command_frame(seq, command_id, magic=0):
    magic_u16 = magic & 0xFFFF
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.COMMAND,
            seq & 0xFF,
            command_id & 0xFF,
            0,
            magic_u16 & 0xFF,
            (magic_u16 >> 8) & 0xFF,
            0,
        )
    )


def encode_ack_frame(seq, command_id, payload=(0, 0, 0), flags=0):
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.ACK,
            seq & 0xFF,
            command_id & 0xFF,
            payload[0] & 0xFF,
            payload[1] & 0xFF,
            payload[2] & 0xFF,
            flags & 0xFF,
        )
    )


def info_ack_payload(model_id, protocol_version=PROTOCOL_VERSION):
    # GET_INFO ACK: byte 3 = GET_INFO, byte 4 = model id, byte 5 = protocol
    # level, byte 6 = 0, flags = HAS_MODEL.
    return (model_id, protocol_version, 0)


def encode_mapping_frame(seq, control_id, slot, gesture, action, param):
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.MAPPING,
            seq & 0xFF,
            control_id & 0xFF,
            ((slot & 0x0F) << 4) | (gesture & 0x0F),
            action & 0xFF,
            param & 0xFF,
            0,
        )
    )


def encode_mapping_end_frame(seq):
    return encode_mapping_frame(seq, MAPPING_ALL, 0, 0, 0, 0)


def encode_error_frame(seq, command_id, error_code):
    error_u16 = error_code & 0xFFFF
    return bytes(
        (
            PROTOCOL_VERSION,
            MessageType.ERROR,
            seq & 0xFF,
            command_id & 0xFF,
            0,
            error_u16 & 0xFF,
            (error_u16 >> 8) & 0xFF,
            0,
        )
    )


def decode_frame(frame):
    if len(frame) != REPORT_SIZE:
        raise ValueError("invalid report length")

    value = frame[5] | (frame[6] << 8)
    if value & 0x8000:
        value -= 0x10000

    return {
        "version": frame[0],
        "msg_type": frame[1],
        "seq": frame[2],
        "control_id": frame[3],
        "event_kind": frame[4],
        "value": value,
        "flags": frame[7],
    }


def decode_slot_gesture(byte):
    # Byte 5 of GET_MAPPING / SET_MAPPING and byte 4 of MAPPING: (slot << 4) | gesture
    return (byte >> 4) & 0x0F, byte & 0x0F
