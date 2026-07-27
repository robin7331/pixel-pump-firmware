# Issue #30 — MicroPython v1.28 + vendor-HID protocol v2

Implementation plan for [robin7331/pixel-pump-firmware#30](https://github.com/robin7331/pixel-pump-firmware/issues/30):
modernize the Pixel Pump 1 firmware so it works with Board Factory, then freeze this repo.

- **Written:** 2026-07-27
- **Branch:** `firmware-v2`
- **Canonical protocol spec:** `pixel-pump-two-firmware` → `docs/usb-communication.md` (a verbatim
  copy lands in this repo as part of Phase 1)
- **Host to test against:** `board-factory/rust/pixel-pump-daemon/`

Phases are sequenced so each one ends at something testable on real hardware, and so the riskiest
unknowns get answered first. There is no test framework here — every gate is a manual check on a
physical pump.

---

## Phase 0 — De-risk: does runtime USB work on RP2040 at v1.28, and does it still fit?

Nothing else is worth building until this is answered.

1. Update CI to v1.28.0 and drop the patch step (Phase 1 work, pulled forward for the `EMPTY` build
   only), then build `firmware-blank.uf2` locally via `act`.
2. **Measure the binary against the flash ceiling.** 2 MB − 1408 KiB littlefs ≈ 640 KiB for
   firmware. We are jumping eight MicroPython releases *and* freezing three USB packages. If the
   full build does not fit, the shape of this whole issue changes — `MICROPY_HW_FLASH_STORAGE_BYTES`
   cannot move without wiping `settings.json` on every field unit.
3. Flash blank onto a real PP1, then `mpremote run` a ~30-line smoke script (scratchpad, not
   committed): `usb.device.get().init(KeyboardInterface(), VendorHIDInterface(), builtin_driver=True)`,
   send a heartbeat, tap a key. PP2's `src/` will not run here (RP2350, different pins), so a
   purpose-built script is the honest test.
4. Point PP2's `tools/usb-coms/main.py` at it — it already does device discovery, heartbeat
   decoding and `--command get-version`.

**Gate:** composite keyboard + vendor HID enumerates, CDC/REPL survives, image fits with headroom.

---

## Phase 1 — Toolchain & board files

- `boards/PIXEL_PUMP/`: split the manifests PP2-style —
  - `manifest_shared.py` — port manifest + `require("usb-device")`, `require("usb-device-hid")`,
    `require("usb-device-keyboard")`
  - `manifest.py` — shared + frozen `src/`
  - `manifest_empty.py` — shared only, so the `mpremote mount` dev workflow can `import usb.device`
  - `mpconfigvariant_EMPTY.cmake`
- `mpconfigboard.cmake`: `BOARD_VARIANT` → `MICROPY_BOARD_VARIANT`; keep `set(PICO_BOARD "pico")`.
- `mpconfigboard.h`: drop `MICROPY_HW_USB_HID`; **keep `MICROPY_HW_FLASH_STORAGE_BYTES (1408 * 1024)`
  verbatim**.
- Add `pins.csv` (`PUMP`=GPIO5, `VALVE_NC`=3, `VALVE_NO`=2, `VALVE_3W`=4, `TRIGGER_BTN`=13,
  `FPEDAL`=6, `FPEDAL_AUX`=7, `BTN_LIFT`=8, `BTN_DROP`=9, `BTN_HIGH`=10, `BTN_LOW`=11,
  `BTN_REVERSE`=12, `UI_LED_DATA`=14). Verify the rp2 port actually picks it up from the board dir
  before relying on `Pin.board.*`.
- Delete `drivers/rp2_hid/` entirely.
- Sync `tools/generateVersionFile.py` from PP2 (adds `version_tuple` + `dev`, needed by the USB stack).
- All three workflows: v1.28.0, no patch step, and **fix the artifact paths** — the blank build now
  lands in `build-PIXEL_PUMP-EMPTY/`, not `build-PIXEL_PUMP/`. This is not in the issue checklist and
  will silently break the release upload.
- Copy `docs/usb-communication.md` from PP2 verbatim.

---

## Phase 2 — USB stack port + protocol v2

New `src/pixel_pump/usb/`: `protocol.py`, `vendor_hid.py`, `usb_manager.py`, `keyboard.py`.

- **`protocol.py`** — port from PP2, then apply v2: `PROTOCOL_VERSION = 2`, `MODEL_ID = 1`,
  `MessageType.MAPPING = 7`, `Flags.HAS_MODEL = 0x08`, ControlIds 7–15, CommandIds 3–7,
  ErrorCodes 3–6, model byte in `encode_heartbeat_frame`, new `encode_mapping_frame`, `GET_INFO`
  ACK layout.
- **`vendor_hid.py`** — port as-is + `send_mapping()`.
- **`usb_manager.py`** — port; extend `_handle_command` with `GET_INFO`, `GET_MAPPING` (including the
  `0xFF` bulk dump and its terminator frame), `SET_MAPPING`, `RESET_MAPPINGS` (magic `0xDEFA`),
  `COMMIT_MAPPINGS`. Replace PP2's `_emit_keyboard_fallback` with PP1 semantics (the mapping engine
  takes this over in Phase 4).
- **`keyboard.py` wrapper** — `press(modifier, keycode)` / `release()` on top of `KeyboardInterface`.
  Concrete detail: micropython-lib encodes modifiers as **negative** values (`r[0] |= -k` in
  `send_keys`), while PP1's `settings.json` stores HID usages `0xE0`–`0xE7`. The wrapper translates
  `0xE0 + n → -(1 << n)`; `0x00` means no modifier. Then delete the old `src/pixel_pump/keyboard.py`
  (the `usb_hid` one).
- Init USB **once, early** in `pixel_pump.py` with `builtin_driver=True` so CDC/stdin survives for
  `CommunicationManager`; add `usb_manager.tick()` to the main loop.
- Wire publish-all from the *existing* button callbacks — real EVENT frames before the mapping
  engine exists.

**Gate:** daemon connects, reports model 1 + firmware version, receives button events, and
`ENTER_BOOTLOADER` (magic `0xB007`) lands the device in BOOTSEL. Legacy stdin `version:info` /
`settings:dump` still answer. Device behaves exactly like legacy locally.

Likely free win: issue #29 (freeze on aux pedal with no PC connected) looks like the legacy
`usb_hid.report()` blocking — worth confirming and closing here.

---

## Phase 3 — Control layer: gestures + trigger/pedal split

- **`Button` gains gesture detection** — PRESS / RELEASE / TAP (50–300 ms) / HOLD / LONG_HOLD
  (750 ms), mirroring the existing `IOEventSource` thresholds. LED and animation code untouched.
- **Split GPIO13 from GPIO6.** `trigger_button` drops `secondary_switch_pin`; FPEDAL becomes its own
  control (ControlId 7, trigger button is 15). Two independent controls both running `PUMP_TRIGGER`
  need **press refcounting** (e.g. a `_pump_holders` set), otherwise "hold pedal → tap button →
  release button" stops the pump mid-pick. The trigger button's pulsate/solid LED feedback must also
  follow pedal-driven pumping, since the pedal has no LEDs of its own.
- Remove the stray `foot_aux = Pin(7, ...)`; `secondary_pedal` (FPEDAL_AUX) already owns that pin.

**Gate:** side-by-side against a legacy unit — modes, LED feel, Reverse's 0/100/200 ms valve stagger,
pedal, aux-pedal keys all identical. This is where the accepted deviation appears: LOW/HIGH released
between 300 and 750 ms now does nothing (legacy fired on any release).

---

## Phase 4 — Mapping engine

New `src/pixel_pump/mapping.py`: gesture and action IDs, the PP1 default table (classic behavior in
**both** slots), `(control, gesture, slot) → (action, param)` storage, and `resolve()` picking the
slot from `usb_manager.is_vendor_host_active()` with instant STANDALONE fallback on heartbeat
timeout.

- **Dispatcher** translating actions into state-machine intents: `MODE_LIFT/DROP/REVERSE`,
  `POWER_LOW/HIGH`, `BRIGHTNESS_MENU`, `POWER_SETTINGS_LOW/HIGH`, `PUMP_TRIGGER` (via the refcount),
  `SEND_KEY`, `FORWARD`, `NONE`.
- **Persistence:** add a `mappings` key to `DEFAULT_SETTINGS` — otherwise `migrate_settings()` deletes
  it on every boot. That same loop mutates the dict while iterating it; fix while in there.
  `SET_MAPPING` = RAM only, `COMMIT_MAPPINGS` = flash, only non-default entries stored.
- **Settings-state suspension:** while a brightness/power menu state is active, bypass the mapping
  engine entirely and use the legacy `State.on_button_event` path.
- **Remote-mode LED:** fixed color on buttons whose active-slot action is `FORWARD`, re-evaluated on
  connect and on heartbeat timeout.
- **Factory reset gesture:** poll GPIO8 + GPIO9 raw at power-on for 3 s, before the boot sequence →
  reset mappings to defaults, persist, LED flash confirm.

**[Decision needed]** The spec's PP1 defaults say `FPEDAL_AUX TAP → SEND_KEY(<legacy configured key>)`,
but `SEND_KEY` carries no modifier by design, while PP1's stdin protocol configures one. Proposal:
**param `0x00` on FPEDAL_AUX means "use the legacy settings key + modifier"**; any non-zero param is a
literal keycode from the host. Keeps the old tooling meaningful without adding modifiers to the wire.

---

## Phase 5 — Code health & docs

- `is` → `==` sweep: `communication_manager.py`, `pixel_pump_state_machine.py`, `states/state.py`,
  `controls/button.py`.
- **[Decision needed]** Also fix `settings:set_power_mode` (`power_mode.HIGH` on the *module* →
  `PowerMode.HIGH`)? It raises `AttributeError` today, it is a two-line fix in a file already being
  edited, but it is technically a separate known rough edge.
- Leave the `ticks_ms()` wraparound alone in legacy code (matches surrounding style); new USB code
  uses `ticks_diff` as ported.
- Update `README.md` (stale `act` job names, deleted `tools/*.py` → mpremote) and `CLAUDE.md` (no HID
  patch, new manifest layout, USB stack, mapping engine).

---

## Phase 6 — Acceptance pass on hardware

From the issue's acceptance criteria:

- [ ] Legacy-identical out of the box (state machine, LEDs, valve timing incl. Reverse's staggered
      100/200 ms sequencing, pedal, keyboard emulation)
- [ ] Daemon connects, reports model 1 + firmware version, receives button events
- [ ] `ENTER_BOOTLOADER` (magic `0xB007`) lands the device in BOOTSEL
- [ ] Mapping read/write/commit/reset round-trips; heartbeat timeout restores STANDALONE instantly
- [ ] Legacy stdin protocol still answers (`version:info`, `settings:dump`)
- [ ] Factory reset gesture works
- [ ] **Upgrade a unit carrying a real `settings.json` and confirm the file survives** — not listed
      explicitly in the issue, but it is the entire reason the flash offset is frozen

---

## Phase 7 — Distribution *(proposed: defer)*

Publish workflow + website feed analogous to `pixel_pump_publish.yml` in the PP2 repo, so Board
Factory can auto-update PP1 firmware. Needs a website-side ingest endpoint, so it is separable —
cleaner as its own issue once the firmware lands.

---

## Cross-repo note

PP2's `protocol.py` is still `PROTOCOL_VERSION = 1` with no `MAPPING` / `GET_INFO`. Proposal: author
v2 here first and back-port the file to PP2 under `pixel-pump-two-firmware#4`, keeping the two copies
byte-identical. Needs confirmation that PP1 is the reference implementation.

---

## Open decisions

1. `SEND_KEY` param `0x00` as the "use legacy configured key + modifier" sentinel for FPEDAL_AUX
   (Phase 4).
2. Whether the `settings:set_power_mode` `AttributeError` is in scope (Phase 5).
3. Whether Phase 7 (distribution) belongs in this issue or a follow-up.
4. Whether PP1 is the reference implementation for protocol v2, with a back-port to PP2.
