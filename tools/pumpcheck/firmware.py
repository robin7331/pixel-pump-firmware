"""What the checker reads out of `src/` instead of restating.

Two different things live here, and the difference is the whole point:

  * **Vocabulary is imported.** `src/pixel_pump/usb/protocol.py` carries a
    CPython shim for `micropython.const` and imports nothing else, so the host
    can import it directly. Control ids, event kinds, message types, command
    ids, error codes, flags and the name tables therefore have exactly one
    definition, shared with the firmware. Adding a control id cannot leave the
    checker behind, because there is no second copy to leave behind.

  * **Expectations are not.** `checks_static.py` states what PP1 *should* do
    independently, and diffs that against the firmware. Importing the default
    mapping table would make the hardware checks tautological -- they would
    prove the device agrees with `mapping.py` rather than that it behaves like
    a legacy pump.

`mapping.py` cannot be imported (it pulls in `utime` and `.enums`), so the
`Gesture` and `Action` vocabularies and the `DEFAULTS` table are read out of it
with `ast`. That is a parse, not an execution: nothing in `mapping.py` runs.
"""

import ast
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
MAPPING_PY = os.path.join(SRC_ROOT, "pixel_pump", "mapping.py")
MPCONFIGBOARD_H = os.path.join(REPO_ROOT, "boards", "PIXEL_PUMP", "mpconfigboard.h")

if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from pixel_pump.usb import protocol  # noqa: E402

# Re-exported so checks import one module rather than reaching through two.
ControlId = protocol.ControlId
EventKind = protocol.EventKind
MessageType = protocol.MessageType
CommandId = protocol.CommandId
ErrorCode = protocol.ErrorCode
MappingSlot = protocol.MappingSlot
ModelId = protocol.ModelId
Flags = protocol.Flags

REPORT_SIZE = protocol.REPORT_SIZE
PROTOCOL_VERSION = protocol.PROTOCOL_VERSION
BOOTLOADER_MAGIC = protocol.BOOTLOADER_MAGIC
RESET_MAPPINGS_MAGIC = protocol.RESET_MAPPINGS_MAGIC
MAPPING_ALL = protocol.MAPPING_ALL

control_name = protocol.control_name
event_name = protocol.event_name
command_name = protocol.command_name
error_name = protocol.error_name
model_name = protocol.model_name

# USB identity. Not in protocol.py -- it is a board fact, so it is stated here
# and cross-checked against mpconfigboard.h by `checks_static.py`. Every check
# finds the pump by these, so a PID change that missed this file would look like
# an unplugged pump rather than a mismatch.
VID = 0x2E8A
PID = 0x1061
VENDOR_USAGE_PAGE = 0xFF00


def board_usb_identity():
    """`{"VID": ..., "PID": ...}` as `mpconfigboard.h` declares them."""
    found = {}
    with open(MPCONFIGBOARD_H, "r", encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"\s*#define\s+MICROPY_HW_USB_(VID|PID)\s*\(?(0x[0-9A-Fa-f]+)\)?", line)
            if match:
                found[match.group(1)] = int(match.group(2), 16)
    return found


# ------------------------------------------------------------- mapping.py


class FirmwareParseError(RuntimeError):
    """`mapping.py` did not look the way this parser expects."""


_MISSING = object()


def _literal(node):
    """Unwrap `const(N)` and plain literals; `_MISSING` for anything else."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "const"
        and len(node.args) == 1
    ):
        node = node.args[0]
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return _MISSING


def _class_constants(class_node):
    values = {}
    for stmt in class_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        value = _literal(stmt.value)
        if value is _MISSING:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


class _Firmware:
    """`Gesture`, `Action`, module constants and `DEFAULTS`, parsed not run."""

    def __init__(self, path):
        self.path = path
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        self.classes = {}
        self.constants = {}
        defaults_node = None

        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                self.classes[stmt.name] = _class_constants(stmt)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id == "DEFAULTS":
                        defaults_node = stmt.value
                    else:
                        value = _literal(stmt.value)
                        if value is not _MISSING:
                            self.constants[target.id] = value

        # ControlId is imported by mapping.py rather than defined in it.
        self.classes.setdefault("ControlId", {})
        for name in dir(ControlId):
            if name.isupper():
                self.classes["ControlId"][name] = getattr(ControlId, name)

        if defaults_node is None:
            raise FirmwareParseError(f"no module-level DEFAULTS assignment in {path}")
        self.defaults = self._resolve_defaults(defaults_node)

    def _resolve(self, node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            table = self.classes.get(node.value.id)
            if table is None or node.attr not in table:
                raise FirmwareParseError(f"cannot resolve {node.value.id}.{node.attr}")
            return table[node.attr]
        if isinstance(node, ast.Name):
            if node.id not in self.constants:
                raise FirmwareParseError(f"cannot resolve bare name {node.id}")
            return self.constants[node.id]
        value = _literal(node)
        if value is _MISSING:
            raise FirmwareParseError(f"unsupported expression at line {node.lineno}")
        return value

    def _resolve_defaults(self, node):
        if not isinstance(node, ast.Dict):
            raise FirmwareParseError("DEFAULTS is not a dict literal")
        table = {}
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Tuple) or len(key_node.elts) != 2:
                raise FirmwareParseError("a DEFAULTS key is not a (control, gesture) pair")
            if not isinstance(value_node, ast.Tuple) or len(value_node.elts) != 2:
                raise FirmwareParseError("a DEFAULTS value is not an (action, param) pair")
            key = tuple(self._resolve(part) for part in key_node.elts)
            table[key] = tuple(self._resolve(part) for part in value_node.elts)
        return table

    def enum(self, name):
        if name not in self.classes:
            raise FirmwareParseError(f"{self.path} defines no class {name}")
        return dict(self.classes[name])


_parsed = None


def firmware():
    """The parsed view of `mapping.py`, read once."""
    global _parsed
    if _parsed is None:
        _parsed = _Firmware(MAPPING_PY)
    return _parsed


class _Namespace:
    """Attribute access over a parsed enum, so `Action.FORWARD` still reads right."""

    def __init__(self, name, values):
        self._name = name
        self.__dict__.update(values)

    def __repr__(self):
        return f"<{self._name} from mapping.py>"


Gesture = _Namespace("Gesture", firmware().enum("Gesture"))
Action = _Namespace("Action", firmware().enum("Action"))

GESTURE_NAMES = {value: name for name, value in firmware().enum("Gesture").items()}
ACTION_NAMES = {value: name for name, value in firmware().enum("Action").items()}


def gesture_name(gesture):
    return GESTURE_NAMES.get(gesture, str(gesture))


def action_name(action):
    return ACTION_NAMES.get(action, hex(action))
