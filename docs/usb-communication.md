# Pixel Pump USB Communication Protocol

**Canonical home: `robin7331/pixel-pump-two-firmware` → `docs/usb-communication.md`.**
Copies of this file may exist in `pixel-pump-firmware` (Pixel Pump 1) — never edit a
copy independently; change it here and re-sync.

This is the contract between the Pixel Pump firmwares and desktop hosts
(Board Factory's `pixel-pump-daemon`). It specifies **protocol version 2**.
The protocol is designed to be frozen after v2: the firmware-side vocabulary
is closed, and all future control features are host-side intents.

## Implementation status

| Codebase | Status | Tracking |
|---|---|---|
| Pixel Pump 2 firmware (this repo) | v2 implemented and hardware-verified; releases through v0.1.2 (2026-07-29) were test-only and never distributed. Unreleased: the appearance addendum (implemented, not yet bench-verified) and the VID move to `0x1137` (issue #9) — the VID move must not ship before hosts discover on the new identity | robin7331/pixel-pump-two-firmware#4, #7, #9 |
| Pixel Pump 1 firmware | v2 released as v2.0.0 (2026-07-29) from `main` and served by the update feed (issue #30 complete); units in the field run legacy v1 (no vendor HID at all — stdin line protocol only) until they are updated. Appearance addendum implemented, not yet bench-verified | robin7331/pixel-pump-firmware#30, #35 |
| Board Factory daemon (`board-factory/rust/pixel-pump-daemon`) | v2 + multi-PID discovery shipped (#4 closed 2026-07-29); identity-pair discovery for the `0x1137` VID move not started | robin7331/board-factory#4, #11 |

Sections that only exist in v2 are marked **[v2]**. Everything else shipped
with v1 and is unchanged.

## Devices & model IDs

USB identity — Manufacturer `Robins Tools`:

| Device / firmware | VID | PID | Product string |
|---|---|---|---|
| Pixel Pump 1 (all firmware) | `0x2E8A` | `0x1061` | `Pixel Pump` |
| Pixel Pump 2, legacy (v1) firmware | `0x2E8A` | `0x1061` | `Pixel Pump 2` |
| Pixel Pump 2, v2 firmware **[v2]** | `0x1137` | `0x1062` | `Pixel Pump 2` |

`0x2E8A` is Raspberry Pi's VID (PIDs registered through their program);
`0x1137` is Robins Tools' own VID
(robin7331/pixel-pump-two-firmware#9). Test releases of the v2 firmware
through v0.1.2 enumerated as `0x2E8A:0x1062`; none were distributed, so
hosts need not match that identity.

USB serial number = the MCU's unique flash ID (stable per physical unit;
hosts use it to remember per-device configuration).

**[v2]** PP2 moves to its own PID with the v2 firmware so the generations
are distinguishable at enumeration, and then to Robins Tools' own VID
`0x1137` (robin7331/pixel-pump-two-firmware#9). Note the identity is a
*firmware* property, not a hardware one: PP2 field units still on legacy
firmware keep `0x2E8A:0x1061` forever, so that identity remains ambiguous
(legacy PP2 or PP1). Hosts must match **both identities in the table** and
treat the in-protocol model ID as the authoritative discriminator. The two
are not redundant: the VID:PID identifies the product to the OS, the model
ID tells the host which dialect and mapping table apply. A host seeing them
disagree (e.g. PID `0x1062` reporting model `1`) should treat that as an
error, not pick a winner.
Sequencing constraint: an identity flip must not ship before hosts discover
on the new identity, or updated pumps disappear from their hosts. The PID
flip cleared this gate (robin7331/board-factory#4, closed 2026-07-29); the
VID move is gated the same way (robin7331/board-factory#11).

**[v2]** Model IDs distinguish the generations on the wire:

| ID | Device |
|---|---|
| `0` | Unknown / legacy firmware (never sent explicitly) |
| `1` | Pixel Pump 1 |
| `2` | Pixel Pump 2 |

The model appears in every device heartbeat (byte 3, with flag `0x08`) and in
the `GET_INFO` ACK. Legacy firmware always sent `0` in heartbeat byte 3 and
never sets `HAS_MODEL`, so v2 hosts read legacy devices as "model unknown"
(treat as PP2 on v1 firmware). A legacy *PP1* exposes no vendor HID interface
at all — it appears as a keyboard-only device; the only path is a firmware
update.

## USB interfaces

The firmware initializes a composite HID setup:

- HID Keyboard interface — **[v2]** present only while the persisted
  `keyboard_enabled` setting is on (see below); legacy firmware always
  presents it
- Vendor-defined HID interface (`Pixel Pump Vendor HID`) — the host transport

Vendor HID report descriptor:
- Usage Page `0xFF00` (vendor-defined), Usage `0x01`
- Input report 8 bytes, Output report 8 bytes, no report IDs at protocol level

Practical host notes (hidapi):
- Writes prepend report ID `0x00` (`[0x00] + 8-byte frame`)
- Reads may include a leading report ID byte; strip it if present
- Device selection scoring: usage page `0xFF00`, usage `0x01` (the daemon
  additionally prefers interface number > 0)

### [v2] `keyboard_enabled` setting

Whether the keyboard interface is registered at enumeration is a persisted
device setting (`settings.json`):

| Firmware | Default | Rationale |
|---|---|---|
| Pixel Pump 2 | off | Board Factory is the desktop client; a fresh unit is a clean vendor-HID device |
| Pixel Pump 1 | on | aux-pedal keystrokes are shipped behaviour; an update must not remove them |

macOS opens the Keyboard Setup Assistant for any device exposing a keyboard
top-level collection whose identity it hasn't cached; not presenting one is
the only device-side way to avoid the dialog (hardware-verified 2026-07-28 —
`bCountryCode` does not drive it). With the setting off:

- no keyboard interface appears in the configuration descriptor; the vendor
  interface is unaffected, but its interface number shifts — one more reason
  hosts must select by usage page/usage, never by index
- `SEND_KEY` mapping actions are silent no-ops; the mapping table itself is
  untouched, so turning the setting back on restores the default iBOM keys
  without reconfiguration
- the `keyboard_only` connection state is unreachable

The setting rides the CDC line protocol, not vendor HID (the v2 command
vocabulary is frozen):

    settings:set_keyboard_enabled:0|1

The device persists the value, replies `keyboard_enabled:0|1` on the same
serial port, and applies it at the next boot — it changes enumeration, so it
cannot take effect live. On PP1 this is one more command in the legacy stdin
protocol; PP2 introduces a minimal CDC line protocol carrying only this
command. Tracking: robin7331/pixel-pump-two-firmware#6 (PP2),
robin7331/pixel-pump-firmware#33 (PP1).

## Frame format (8 bytes)

All protocol frames are exactly 8 bytes:

| Byte | Name | Type | Description |
|---|---|---|---|
| 0 | `version` | u8 | Protocol version — `2` per this spec; legacy firmware sends `1`. Hosts must accept `>= 1` |
| 1 | `msg_type` | u8 | Message type enum |
| 2 | `seq` | u8 | Sequence counter (wraps at 255) |
| 3 | `control_id` | u8 | Control identifier / command id echo / model (heartbeats) |
| 4 | `event_kind` | u8 | Event kind (or per-command payload) |
| 5 | `value_lo` | u8 | Value low byte |
| 6 | `value_hi` | u8 | Value high byte |
| 7 | `flags` | u8 | Bitfield/marker flags |

`value` is signed 16-bit little-endian (`int16`) unless a command defines
bytes 4..7 otherwise.

## Enums

### MessageType

| Value | Name | Direction |
|---|---|---|
| 1 | `EVENT` | device → host |
| 2 | `COMMAND` | host → device |
| 3 | `ACK` | device → host |
| 4 | `PING` | both (heartbeats) |
| 7 | `MAPPING` **[v2]** | device → host |
| 255 | `ERROR` | device → host |

### Flags (byte 7)

| Bit | Name | Meaning |
|---|---|---|
| `0x01` | `DEVICE_HEARTBEAT` | Device heartbeat `PING` |
| `0x02` | `DEV_BUILD` | Firmware build has commits past the release tag (or no tag) |
| `0x04` | `HAS_VERSION` | Bytes 4..6 of this frame carry the firmware semver |
| `0x08` | `HAS_MODEL` **[v2]** | Byte 3 of this PING/ACK carries a model ID |
| `0x80` | `HOST_HEARTBEAT` | Host heartbeat `PING` |

`HAS_VERSION` is the compatibility gate: legacy firmware never sets it, so a
host can never misread legacy heartbeat bytes as a version. `HAS_MODEL` plays
the same role for the model byte.

### ControlId

| ID | Name | Device |
|---|---|---|
| 1 | `ACTION` | PP2 |
| 2 | `MENU` | PP2 |
| 3 | `BACK` | PP2 |
| 4 | `ZOOM` | PP2 |
| 5 | `ENCODER` | PP2 |
| 6 | `ENCODER_BUTTON` | PP2 |
| 7 | `FPEDAL` **[v2]** | both |
| 8 | `FPEDAL_AUX` **[v2]** | both |
| 10 | `LIFT` **[v2]** | PP1 |
| 11 | `DROP` **[v2]** | PP1 |
| 12 | `LOW` **[v2]** | PP1 |
| 13 | `HIGH` **[v2]** | PP1 |
| 14 | `REVERSE` **[v2]** | PP1 |
| 15 | `TRIGGER_BTN` **[v2]** | PP1 |

`TRIGGER_BTN` is deliberately **split from `FPEDAL`**: legacy PP1 firmware
merged the trigger button (GPIO13) and foot pedal (GPIO6) into one input; v2
firmware treats them as separate controls so the pedal can run the vacuum
while the button is remapped.

Unknown control IDs must decode as `UNKNOWN` and pass through (the shipping
daemon already behaves this way).

### EventKind (wire events)

| Value | Name | Timing (from `IOEventSource` defaults) |
|---|---|---|
| 1 | `PRESS` | immediate on activation edge |
| 2 | `RELEASE` | on deactivation edge |
| 3 | `TAP` | release after > 50 ms and < 300 ms press |
| 4 | `HOLD` | continuously while pressed (USB-throttled to 120 ms/control) |
| 5 | `LONG_HOLD` | while held, after > 750 ms |
| 6 | `DELTA` | encoder detent, signed `value` |

Ordering nuances: on quick release `TAP` precedes `RELEASE` in that tick;
`HOLD` may share a tick with `PRESS`.

### CommandId (host → device, byte 3 of `COMMAND` frames)

| ID | Name |
|---|---|
| 1 | `GET_VERSION` |
| 2 | `ENTER_BOOTLOADER` |
| 3 | `GET_INFO` **[v2]** |
| 4 | `GET_MAPPING` **[v2]** |
| 5 | `SET_MAPPING` **[v2]** |
| 6 | `RESET_MAPPINGS` **[v2]** |
| 7 | `COMMIT_MAPPINGS` **[v2]** |

### ErrorCode (bytes 5..6 of `ERROR` frames, u16 LE)

| Code | Name |
|---|---|
| 1 | `UNKNOWN_COMMAND` |
| 2 | `BAD_MAGIC` |
| 3 | `BAD_CONTROL` **[v2]** |
| 4 | `BAD_GESTURE` **[v2]** |
| 5 | `BAD_ACTION` **[v2]** (unknown, or invalid for this model/gesture) |
| 6 | `STORAGE_ERROR` **[v2]** |

## Heartbeat contract

### Host → device (required for "active" state)

The device marks the host active only when it receives reports on the vendor
HID OUT path. Defaults: activity timeout `1200 ms`, open grace `0 ms`.
Opening the interface is not enough — send periodic frames at < 1200 ms
(recommended ~`400 ms`, the daemon's interval).

Host heartbeat convention: `msg_type = PING`, `flags = 0x80`, `value` =
host-defined (opaque to firmware), `seq` increments per frame.

### Device → host

While the vendor interface is open, firmware emits a `PING` every `500 ms`
(shares the device TX seq counter with all other device frames):

| Byte | Value |
|---|---|
| 0 | `2` (protocol version; legacy firmware: `1`) |
| 1 | `4` (`PING`) |
| 2 | device TX seq |
| 3 | **model ID [v2]** (legacy firmware: `0`) |
| 4..6 | fw major / minor / patch |
| 7 | `0x01 \| 0x04 \| 0x08` (`\| 0x02` on dev builds); legacy: `0x05`/`0x07` |

Hosts must mask flag bits (never compare the flags byte for equality) and can
passively track firmware version and model from any heartbeat.

## Connection state model (firmware)

- `no_data`: neither keyboard nor vendor path available
- `keyboard_only`: keyboard HID open, vendor not active (unreachable while
  `keyboard_enabled` is off)
- `vendor_hid`: vendor host active (host RX within timeout)

`vendor_open` (interface opened) and `vendor_active` (recent host RX) are
distinct; state resolution: `vendor_active` → `vendor_hid`, else keyboard
open → `keyboard_only`, else `no_data`.

## Device → host event pipeline

Events are queued and sent over vendor HID while `vendor_active`:

- Max queue 32 frames (oldest dropped on overflow); drain up to 4 frames per
  120 Hz tick (~480 frames/s); non-blocking sends
- Consecutive `ENCODER DELTA` events merge (value saturates to int16, flags OR)
- `HOLD` rate-limited to one per 120 ms per control
- Queue clears when `vendor_active` drops

**[v2] Publish-all rule:** while the vendor host is active, the firmware
publishes EVENT frames for **all** controls — including pedals and controls
mapped to local actions — regardless of the mapping table. The mapping table
governs only local behavior. This lets the host observe pump state changes
(e.g. mode switches) without owning the buttons.

## [v2] Control mapping model

### Two layers

**Layer 1 — device (persisted).** A table keyed by `(control, gesture, slot)`
with value `(action, param)`.

- `slot`: `0` = STANDALONE, `1` = CONNECTED. The device uses the CONNECTED
  column while the vendor host is active and falls back to STANDALONE the
  moment the host heartbeat times out — unplugging can never leave buttons
  dead.
- Actions are device-executed, keyboard emulation, `FORWARD` (no local
  action), or `NONE`.

**Layer 2 — host.** Board Factory maps forwarded events — including chords —
to app intents. The device never learns what an intent is; the intent catalog
grows forever without firmware changes.

**Invariant (maintained by the host's remap UI):** a control assigned a host
intent gets `FORWARD` in its device CONNECTED cell; a control assigned a
device action has no host intent. This prevents double execution under the
publish-all rule.

### Gesture IDs (mapping keys — wire EventKinds are unchanged)

| ID | Name | Valid on |
|---|---|---|
| 1 | `TAP` | buttons, pedals |
| 2 | `LONG_HOLD` | buttons, pedals |
| 3 | `HELD` | buttons, pedals (momentary: act on press, undo on release) |
| 4 | `DELTA_CW` | encoder |
| 5 | `DELTA_CCW` | encoder |
| 6 | `PRESS` | buttons, pedals (fires once on the activation edge) |

`PRESS` exists so legacy PP1 button feel is reproducible: mode buttons act
the moment they are pressed, not on release. It keys off the existing
`ACTIVATE` IO event; no wire change. Multiple gestures on one control each
fire per their own trigger (e.g. `PRESS` + `LONG_HOLD` reproduces legacy
"switch mode on press, open menu at 750 ms"). Combining `PRESS` and `HELD` on
one control is discouraged — `HELD` already acts on the press edge.

### Action registry

`(action u8, param u8)`; validity is per-model. Invalid action for the target
model/gesture → `ERROR BAD_ACTION`.

| ID | Name | Devices | Param | Meaning |
|---|---|---|---|---|
| `0x00` | `NONE` | both | — | do nothing |
| `0x01` | `FORWARD` | both | `(animation << 4) \| color` — the control's appearance (see §Control appearance) | no local action (host intent handles it) |
| `0x02` | `SEND_KEY` | both | HID keycode | keyboard emulation. TAP: tap key. HELD: press/release with control. DELTA: one tap per detent |
| `0x10` | `PUMP_TRIGGER` | both | — | HELD: run vacuum while active (device-appropriate choreography: PP2 motor+vent, PP1 state-machine `trigger_on/off`) |
| `0x11` | `PUMP_TOGGLE` | both | — | TAP: toggle pump run state |
| `0x12` | `VENT_PULSE` | both | duration ×10 ms (0 = 500 ms) | open vent, auto-close |
| `0x20` | `MODE_LIFT` | PP1 | — | switch state machine to Lift |
| `0x21` | `MODE_DROP` | PP1 | — | switch to Drop |
| `0x22` | `MODE_REVERSE` | PP1 | — | switch to Reverse |
| `0x23` | `POWER_LOW` | PP1 | — | select low power mode |
| `0x24` | `POWER_HIGH` | PP1 | — | select high power mode |
| `0x25` | `BRIGHTNESS_MENU` | PP1 | — | enter brightness settings state |
| `0x26` | `POWER_SETTINGS_LOW` | PP1 | — | enter low-power adjustment state |
| `0x27` | `POWER_SETTINGS_HIGH` | PP1 | — | enter high-power adjustment state |

While a PP1 settings state (brightness / power adjustment) is active, the
legacy in-menu button behavior applies and the mapping engine is suspended
until the menu exits.

### [v2] Control appearance (`FORWARD`'s param)

`FORWARD`'s param byte declares the control's **appearance** — how the device
lights a host-owned control. Nibble-packed, the same trick byte 5 of the
mapping commands already uses for slot and gesture:

    param = (animation << 4) | color

The appearance is declared at mapping time and persisted with the table like
any other param; the device owns rendering, timing, brightness and the
STANDALONE fallback. There is no live LED channel (see §Non-goals).

Colors (low nibble):

| Value | Name | Note |
|---|---|---|
| `0x0` | `REMOTE_DEFAULT` | the device's classic remote rendering — prior behavior |
| `0x1` | `BLUE` | |
| `0x2` | `RED` | |
| `0x3` | `GREEN` | |
| `0x4` | `WHITE` | |
| `0x5` | `AMBER` | |
| `0x6` | `CYAN` | |
| `0x7` | `OFF` | host owns the control, device shows nothing |
| `0x8`–`0xF` | reserved | render as `REMOTE_DEFAULT` |

Animations (high nibble):

| Value | Name | PP1 | PP2 |
|---|---|---|---|
| `0x0` | `SOLID` | yes | yes |
| `0x1` | `PULSE` | yes | yes |
| `0x2` | `SPIN` | — | yes |
| `0x3` | `RAINBOW` | — | yes |
| `0x4`–`0xF` | reserved | | |

**Degradation rule.** Unsupported or reserved values render as the nearest
supported thing — never an error. Unknown animation → `SOLID`; unknown color
→ `REMOTE_DEFAULT`; an animation the device cannot show → `SOLID`.
`SET_MAPPING` accepts **any** param on `FORWARD` (there is no `BAD_ACTION`
for appearance) and `GET_MAPPING` returns it verbatim — degradation happens
at render time only. This is deliberate: it lets the appearance catalog grow
past what any one pump implements, so a newer host talking to an older pump
degrades gracefully instead of failing. Same spirit as "unknown control IDs
must decode as `UNKNOWN` and pass through."

**Per-control resolution.** The appearance is rendered per *control*, but
stored per `(control, gesture, slot)`. Two rules bridge the gap, both pure
functions of the table:

- A control renders an appearance only while it is **host-owned** in the
  active slot: at least one of its gestures is `FORWARD` and none is mapped
  to a local action (`NONE` cells are neutral). A button that keeps, say, a
  local long-press action is not remote, and badging it would lie.
- The control's appearance is the **first non-zero param among its `FORWARD`
  cells, scanned in gesture-ID order** (`TAP`=1 … `PRESS`=6). If every
  `FORWARD` param is zero, the appearance is `SOLID` + `REMOTE_DEFAULT`.
  Zero params are "no preference" — implicit `FORWARD` cells (PP2's
  CONNECTED default) never mask an explicitly written appearance. Hosts
  should still write the same appearance to every `FORWARD` cell of a
  control.

**Per-device rendering:**

- **PP1 — static badge.** Each host-owned button shows its appearance on its
  own LEDs, replacing the state-machine color. `REMOTE_DEFAULT` is the
  classic purple remote badge; `OFF` leaves the button dark; `SPIN` and
  `RAINBOW` degrade to `SOLID`.
- **PP2 — momentary echo.** One ring serves five controls, so a static
  per-control badge is impossible. On a host-owned control's activation edge
  (the wire `PRESS`), the ring briefly (~200 ms) shows that control's
  appearance, then settles back to idle. `ENCODER` never echoes — a fast
  spin would strobe; its appearance instead **replaces the ring's idle**
  (the vendor-connected rainbow) while the vendor host is active, live on
  `SET_MAPPING`. Colors `REMOTE_DEFAULT` and `OFF` render as *no
  indication* — no echo, idle untouched — so a default table (all params
  zero) lights nothing new. The sleep gesture owns the ring outright: the
  MENU hold-progress preview and the sleep animation always win over an
  echo.

**Backward compatibility.** `param = 0x00` → `SOLID` + `REMOTE_DEFAULT`,
byte-identical to prior behavior (PP1's fixed purple badge, nothing on PP2).
Every mapping table committed in the field is already correct; no migration,
no version gate — the protocol version byte stays `2`.

### Persistence semantics

- `SET_MAPPING` updates RAM only (no flash wear during interactive config);
  `COMMIT_MAPPINGS` persists.
- Storage: `settings.json` (SettingsManager). Only non-default entries are
  stored; missing/corrupt file → defaults.
- **Factory reset gesture** (escape hatch): hold at power-on for 3 s —
  PP1: LIFT+DROP, PP2: MENU+ACTION. LED flash confirms; resets mappings to
  defaults and persists.
- Host-owned controls render their persisted appearance (`FORWARD`'s param —
  see §Control appearance): a static per-button badge on PP1, a momentary
  ring echo on PP2. All-zero params reproduce the pre-appearance behavior —
  the fixed purple remote badge on PP1, nothing on PP2.

### Default mapping tables

Defaults reproduce legacy shipping behavior. The host writes the CONNECTED
column when the user picks a preset or customizes.

**Pixel Pump 2**

| Control | Gesture | STANDALONE | CONNECTED |
|---|---|---|---|
| FPEDAL | HELD | `PUMP_TRIGGER` | `PUMP_TRIGGER` |
| FPEDAL_AUX | TAP | `SEND_KEY(N)` | `FORWARD` |
| FPEDAL_AUX | LONG_HOLD | `SEND_KEY(UP)` | `FORWARD` |
| ACTION | TAP | `SEND_KEY(N)` | `FORWARD` |
| BACK | TAP | `SEND_KEY(UP)` | `FORWARD` |
| MENU | TAP | `SEND_KEY(ESC)` | `FORWARD` |
| ZOOM | TAP | `SEND_KEY(Z)` | `FORWARD` |
| ENCODER_BUTTON | TAP | `SEND_KEY(ENTER)` | `FORWARD` |
| ENCODER | DELTA_CW | `SEND_KEY(RIGHT)` | `FORWARD` |
| ENCODER | DELTA_CCW | `SEND_KEY(LEFT)` | `FORWARD` |

All other gestures default to `FORWARD` connected, `NONE` standalone.
Deliberate change vs v1 firmware: the aux pedal previously sent keyboard keys
even while a host was connected; v2 forwards it so it can carry intents.
PP2's deep sleep (2.5 s MENU hold) is hardwired outside the mapping engine.

**Pixel Pump 1**

Classic behavior in **both** columns — a freshly updated PP1 behaves exactly
like legacy firmware until the host writes the CONNECTED column.

| Control | Gesture | Both slots (default) |
|---|---|---|
| LIFT | PRESS | `MODE_LIFT` |
| LIFT | LONG_HOLD | `BRIGHTNESS_MENU` |
| DROP | PRESS | `MODE_DROP` |
| LOW | TAP | `POWER_LOW` |
| LOW | LONG_HOLD | `POWER_SETTINGS_LOW` |
| HIGH | TAP | `POWER_HIGH` |
| HIGH | LONG_HOLD | `POWER_SETTINGS_HIGH` |
| REVERSE | PRESS | `MODE_REVERSE` |
| TRIGGER_BTN | HELD | `PUMP_TRIGGER` |
| FPEDAL | HELD | `PUMP_TRIGGER` |
| FPEDAL_AUX | TAP | `SEND_KEY(<legacy configured key>)` |
| FPEDAL_AUX | LONG_HOLD | `SEND_KEY(<legacy configured long key>)` |

Gesture fidelity to legacy: LIFT/DROP/REVERSE acted on press-down → `PRESS`;
LOW/HIGH acted on release (`TOUCH_UP`) → `TAP`. Accepted deviation: legacy
fired LOW/HIGH on *any* release; `TAP` requires < 300 ms, so a 300–750 ms
hold-and-release now does nothing. ≥ 750 ms opens the settings state, as
before.

Board Factory's "keep pump controls" preset changes only `TRIGGER_BTN HELD →
FORWARD` in the CONNECTED column; "use as remote" sets all six buttons to
`FORWARD` (pedal keeps the vacuum).

## Host commands

Byte 3 of a `COMMAND` frame is the command id. Any other host frame (e.g.
heartbeat `PING`) only counts as host activity. Commands define their own
payload layout in bytes 4..7.

### `GET_VERSION` (1)

Request: bytes 4..7 = 0. → ACK: byte 3 = `1`, bytes 4..6 = fw
major/minor/patch, flags `0x04` (`| 0x02` on dev builds). Version bytes sit
at 4..6 in both heartbeat and ACK — one parser suffices.

### `ENTER_BOOTLOADER` (2)

Request: magic `0xB007` u16 LE in bytes 5..6 (i.e. byte 5 = `0x07`, byte 6 =
`0xB0`). Wrong magic → `ERROR BAD_MAGIC`, no reboot — a corrupted frame must
never DFU the pump mid-assembly.

→ ACK (byte 3 = `2`, payload 0), then firmware waits ≥ 100 ms for the USB
flush (default 150 ms) and calls `machine.bootloader()`. The device
re-enumerates as the BOOTSEL mass-storage drive; hosts should treat the HID
disconnect + BOOTSEL volume as success. The reboot happens even if the host
closes the interface right after the ACK. Hardware note: pump and valve
MOSFET gates have 100k pulldowns, so both outputs are safely off in the
bootloader.

### [v2] `GET_INFO` (3)

Request: bytes 4..7 = 0. → ACK: byte 3 = `3`, byte 4 = model ID, byte 5 =
protocol level (`2`), byte 6 = 0, flags `0x08`.

### [v2] `GET_MAPPING` (4)

Request: byte 4 = control, byte 5 = `(slot << 4) | gesture`. → one `MAPPING`
frame. **Bulk:** control `0xFF` → device streams all non-`NONE` entries as
`MAPPING` frames, terminated by a `MAPPING` frame with control `0xFF`.
Invalid control/gesture → `ERROR`.

`MAPPING` frame (device → host):

| Byte | Value |
|---|---|
| 0 | `2` |
| 1 | `7` (`MAPPING`) |
| 2 | device TX seq |
| 3 | control (`0xFF` = end-of-dump marker) |
| 4 | `(slot << 4) \| gesture` |
| 5 | action |
| 6 | param |
| 7 | `0` |

### [v2] `SET_MAPPING` (5)

Request: byte 4 = control, byte 5 = `(slot << 4) | gesture`, byte 6 = action,
byte 7 = param. → ACK. Effective immediately in RAM (not persisted).

### [v2] `RESET_MAPPINGS` (6)

Request: magic `0xDEFA` u16 LE in bytes 5..6. → ACK. Restores defaults and
persists.

### [v2] `COMMIT_MAPPINGS` (7)

Request: bytes 4..7 = 0. → ACK after flash write, or `ERROR STORAGE_ERROR`.

### Errors

`ERROR` frame: byte 3 = echoed command id (`0` if unparseable), bytes 5..6 =
error code u16 LE, other bytes 0.

## Sequence number semantics

No retransmission or sequence validation. Device TX seq increments on each
successful device send (EVENT, PING, ACK, MAPPING, ERROR). Host TX seq is
host-defined. ACK/ERROR frames echo the command id, not the host seq — hosts
resend `GET_VERSION`/`GET_INFO` on reconnect; heartbeats repair missed state.

## Compatibility matrix

| Host | Firmware | Result |
|---|---|---|
| v1 host (shipping daemon) | v2 firmware | Wire-compatible: version byte, model byte, `MAPPING` frames, and flag `0x08` are ignored/passed through (verified against `protocol.rs`/`usb_hid.rs`). **Caveat:** identity flips change *discovery*, never the wire — a single-PID host will not find a v2 PP2 on its new PID, and a `0x2E8A`-only host will not find a pump on VID `0x1137`; host discovery must ship before/with each flip (robin7331/board-factory#4, #11) |
| v2 host | v1 PP2 firmware | Works, degraded: no model (assume PP2), mapping commands → `UNKNOWN_COMMAND` |
| v2 host | legacy PP1 firmware | No vendor HID interface — keyboard-only device; prompt a firmware update |
| v2 host | v2 firmware | Full feature set |
| Any v1-era host matching heartbeats with `flags == 0x01` | v2 firmware | Must mask bit 0 instead — heartbeat flags are now `0x0D`/`0x0F` |

## Integration checklist for hosts

1. Open HID devices matching either identity from §Devices & model IDs —
   `0x1137:0x1062` or `0x2E8A:0x1061` — and select the vendor interface
   (usage page `0xFF00`, usage `0x01`); normalize reports to 8 bytes,
   stripping a leading `0x00` report ID.
2. Send host heartbeat `PING` (~400 ms; must stay < 1200 ms).
3. Send `GET_INFO` (and `GET_VERSION`) on connect; also passively read model
   + version from heartbeats (`HAS_MODEL`/`HAS_VERSION` flags — always mask
   bits, never compare flags for equality).
4. Select the device profile by model; model `0` → legacy PP2 (no mapping
   support — expect `UNKNOWN_COMMAND`).
5. Consume `EVENT` frames (`value` is signed int16); expect burst delivery
   (queueing, 4 frames/tick drain); remember all controls publish while you
   are active — act only on controls your config assigned intents to.
6. Read the mapping table with bulk `GET_MAPPING` on connect; write with
   `SET_MAPPING` (live), persist with `COMMIT_MAPPINGS`. When assigning a
   host intent, write the control's appearance into `FORWARD`'s param —
   the same value on every `FORWARD` cell of that control (§Control
   appearance).
7. Detect chords host-side from `PRESS`/`RELEASE`; chords exist only as host
   intents.
8. To flash firmware: `ENTER_BOOTLOADER` with magic `0xB007`, wait for HID
   disconnect + BOOTSEL volume.

## Non-goals (deliberate, to keep the firmware freezable)

- Chord detection in firmware (host-side only)
- Modifier keys in `SEND_KEY` (PP1's legacy stdin protocol retains modifier
  configuration)
- Host-*driven* LEDs — there is no live LED channel, no new command, no new
  frame type. The host declares an appearance at mapping time (`FORWARD`'s
  param, persisted with the table); the device owns rendering, timing,
  brightness and the STANDALONE fallback
- Remappable sleep on PP2 (stays hardwired in `PowerStateManager`)

## Reference host implementations

- Production: `board-factory/rust/pixel-pump-daemon/` (`protocol.rs`,
  `usb_hid.rs`)
- Diagnostics: `tools/usb-coms/main.py` in this repo (device discovery on
  both PIDs, heartbeats, frame decoding, `--command get-version` /
  `get-info` / `enter-bootloader`)

## Changelog

- **v2, VID move** (2026-07-30, robin7331/pixel-pump-two-firmware#9): PP2 v2
  firmware moves from Raspberry Pi's VID `0x2E8A` to Robins Tools' own VID
  `0x1137`; the PID stays `0x1062` and nothing on the wire changes. Test
  releases through v0.1.2 enumerated as `0x2E8A:0x1062` but were never
  distributed, so hosts match two identities. Release gated on host
  discovery (robin7331/board-factory#11).
- **v2, appearance addendum** (spec 2026-07-29,
  robin7331/pixel-pump-two-firmware#7): `FORWARD`'s previously unused param
  byte declares a control appearance — `(animation << 4) | color`, rendered
  device-side (PP1 static per-button badge, PP2 momentary ring echo +
  encoder idle). Reserved values degrade at render time, never error.
  `param = 0x00` is byte-identical to prior behavior, so no migration and no
  version-byte change.
- **v2** (spec 2026-07-27; implementation state in the status table): protocol
  version byte `2`; model IDs + `HAS_MODEL` flag + `GET_INFO`; ControlIds
  7–15 (pedals both devices, PP1 buttons, trigger/pedal split); two-layer
  mapping model with STANDALONE/CONNECTED slots, `MAPPING` message type,
  mapping commands, error codes 3–6; publish-all rule; factory reset gesture;
  PP2 aux pedal forwards when connected; PP2 moves to its own PID
  (`0x1062` — PP1 keeps `0x1061`); persisted `keyboard_enabled` setting
  deciding whether the keyboard interface enumerates (PP2 default off, PP1
  default on).
- **v1**: 8-byte frames, events, heartbeats with version reporting,
  `GET_VERSION`, `ENTER_BOOTLOADER`.
