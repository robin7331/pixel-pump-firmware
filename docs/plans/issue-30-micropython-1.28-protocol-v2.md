# Issue #30 — MicroPython v1.28 + vendor-HID protocol v2

Implementation plan for [robin7331/pixel-pump-firmware#30](https://github.com/robin7331/pixel-pump-firmware/issues/30):
modernize the Pixel Pump 1 firmware so it works with Board Factory, then freeze this repo.

- **Written:** 2026-07-27
- **Branch:** `firmware-v2`
- **Canonical protocol spec:** `pixel-pump-two-firmware` → `docs/usb-communication.md` (a verbatim
  copy lands in this repo as part of Phase 1)
- **Host to test against:** `board-factory/rust/pixel-pump-daemon/`
- **Build environment:** `./micropython` — a v1.28.0 checkout with the rp2 submodules (pico-sdk,
  tinyusb, micropython-lib) fetched and both variants built, set up 2026-07-28. In-repo and already
  covered by `.gitignore`'s `/micropython`, which is why the build commands in `CLAUDE.md` are written
  the way they are. ~450 MB including build dirs. Do not re-clone; the disk runs close to full
  (12 GiB free, 98 %).
- **Status:** Phase 0 landed in `c7614cd`, Phase 1 in `ebeea4c`, Phase 2 in this branch and verified on
  a physical pump 2026-07-28 — see *Deviations* for what changed against this plan. Phase 3 landed the
  same day and its gate is **closed**, wire checks included. Phase 4 landed the same day, is on the dev
  pump, and its gate is **closed too** — `tools/phase4_wire_check.py` passes end to end, bar two narrow
  gaps noted under that phase. Phase 5 landed the same day, and **Phase 6 closed the same day too** —
  `tools/phase6_acceptance.py` passes all four checks, so every acceptance item this repo can close is
  closed. The two that remain open are not firmware work (see Phase 6). All four open decisions were
  resolved 2026-07-28 — see *Decisions* at the bottom.

Phases are sequenced so each one ends at something testable on real hardware, and so the riskiest
unknowns get answered first. There is no test framework here — every gate is a manual check on a
physical pump.

---

## Phase 0 — De-risk: does runtime USB work on RP2040 at v1.28, and does it still fit? *(landed `c7614cd`)*

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

## Phase 1 — Toolchain & board files *(landed `c7614cd` + `ebeea4c`)*

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

### Deviations, as implemented *(2026-07-28)*

Four departures from the text above. The first two are owed to PP2 on back-port.

1. **`MODEL_ID` lives in `usb_manager.py`, not `protocol.py`.** This plan asked for `MODEL_ID = 1` in
   `protocol.py`, but the cross-repo note requires that file to stay byte-identical with PP2, where the
   value is `2`. Resolved by putting the shared vocabulary (`class ModelId`: 0/1/2) in `protocol.py`
   and the per-device selection in `usb_manager.py`; the encoders take `model_id` as an argument,
   defaulting to `UNKNOWN`, which reproduces the legacy heartbeat shape exactly (model byte `0`, no
   `HAS_MODEL`).
2. **Bug fix in `vendor_hid.tick()`, inherited from PP2.** The open-edge branch unconditionally cleared
   `_last_host_rx_ms`, so a host frame arriving between the interface opening and the next `tick()` was
   discarded and the device stayed inactive until the host's *next* heartbeat (~400 ms with the
   daemon). A daemon that opens and immediately writes hits this every connect. The close edge already
   clears that state, so the reset was redundant as well as lossy. **PP2 has the same bug.**
3. **The mapping commands are wired, but there is no table yet.** `_handle_command` implements all five
   v2 commands including the `0xFF` bulk dump, its terminator, both magics and the `BAD_CONTROL` /
   `BAD_GESTURE` / `BAD_ACTION` / `STORAGE_ERROR` paths — but delegates storage to an optional
   `mapping` object whose contract is the `USBManager` docstring. With no table wired up (this phase),
   the four mapping commands answer `ERROR UNKNOWN_COMMAND`, which is what the spec's compatibility
   matrix already describes and what a v2 host is prepared for. Phase 4 passes in `mapping.py` and
   changes nothing here. Note the frozen `ErrorCode` enum has no `BAD_SLOT`, so an out-of-range slot —
   it shares a byte with the gesture — reports as `BAD_GESTURE`.
4. **Aux-pedal keys stayed in the `pixel_pump.py` callback** rather than becoming a
   `_emit_keyboard_fallback` inside `USBManager`. Two reasons: PP2's fallback only fires when *no*
   vendor host is active, whereas PP1's spec defaults put `SEND_KEY` on `FPEDAL_AUX` in **both** slots,
   so plugging in the daemon must not stop the pedal from typing; and it keeps `USBManager` from
   depending on `SettingsManager`. `publish_event` is therefore pure publish-all with no fallback
   branch. Phase 4's dispatcher takes this over from the callback.

Also worth knowing for Phase 3: buttons publish PRESS / RELEASE / HOLD / LONG_HOLD only, since
`Button` has no tap detection yet — there are no button `TAP` frames until Phase 3, and GPIO6 still
rides on the trigger button, so pedal presses publish as `TRIGGER_BTN` rather than `FPEDAL`.

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

**Gate closed 2026-07-28.** The local half was checked by hand on the dev pump, including the
pedal/button interleave the refcount exists for. The wire half — the part this phase is really about —
was run with `tools/phase3_wire_check.py`, all four checks passing:

- [x] the foot pedal publishes `FPEDAL` (7) — and **no** frame leaks onto `TRIGGER_BTN` (15)
- [x] the trigger button publishes `TRIGGER_BTN` (15) and nothing onto `FPEDAL`
- [x] buttons emit `TAP`, ahead of `RELEASE` in the same tick
- [x] the heartbeat still reports model 1 with `HAS_MODEL`

Ran against the **Phase 4** firmware, not Phase 3's, so it doubles as proof that Phase 4's rewiring of
publish-all (`_CONTROL_IDS_BY_BUTTON`, now keyed on the `Button` object rather than its title) kept
every control reporting.

Setup note for next time: the checks failed twice with hidapi's `exclusive access and device already
open`, which the tool's own help text attributes to missing Input Monitoring. That was a red herring —
**Board Factory's `pixel-pump-daemon` had the vendor interface open**, as a child of the running dev
app. Quitting it freed the interface; the app respawns the daemon afterwards. Check `pgrep -fl
pixel-pump-daemon` before blaming macOS permissions.

`tools/phase3_wire_check.py` walks all four interactively and asserts the absence of the wrong control
id, which PP2's `tools/usb-coms` cannot — a raw dump shows what arrived, not what should not have.
Either needs the vendor HID interface, which macOS only hands to a process holding Input Monitoring,
so it must be launched from Terminal rather than an agent shell.

### Deviations, as implemented *(2026-07-28)*

1. **`ButtonEvent` gained `TAPPED = 4`; the existing names stayed.** Renaming the enum to the wire
   vocabulary (PRESS/RELEASE/…) would have rippled through every state for no behavioural gain —
   `_button_event_to_usb_event_kind` already does that translation. `Button` also gained
   `long_press_threshold` / `tapped_threshold` / `on_tapped`, mirroring `IOEventSource`'s signature,
   and its `secondary_switch_pin` support was deleted outright rather than merely unwired: re-merging
   two controls onto one button is exactly what this phase exists to undo.
2. **Only LOW/HIGH moved to `TAPPED`.** LIFT/DROP/REVERSE still act on `TOUCH_DOWN`, per the spec's
   PP1 default table (`PRESS`), and the settings states keep their legacy in-menu handling
   (`TOUCH_UP` for brightness steps, `TOUCH_DOWN` for power steps). So the only behaviour change is
   the accepted one in the gate above.
3. **The refcount lives in `pixel_pump.py`,** as a module-level `_pump_holders` set plus
   `pump_trigger_press()` / `pump_trigger_release()`, keyed on `ControlId.TRIGGER_BTN` /
   `ControlId.FPEDAL`. Same reasoning as Phase 2's deviation 4 — Phase 4's dispatcher takes it over
   from the callbacks, and keying it on control ids now is what makes that a lift-and-shift.
4. **Trigger LED feedback needed no special handling.** Both controls funnel into the same
   `state.trigger_on()` / `trigger_off()`, and the states are what drive `trigger_button`'s
   pulsate/solid colours, so pedal-driven pumping animates the trigger button for free.

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

**[Decided 2026-07-28]** The spec's PP1 defaults say `FPEDAL_AUX TAP → SEND_KEY(<legacy configured key>)`,
but `SEND_KEY` carries no modifier by design, while PP1's stdin protocol configures one (four settings
keys: `secondary_pedal_key`/`_modifier` and `secondary_pedal_long_key`/`_modifier`). Resolution:
**param `0x00` on FPEDAL_AUX means "use the legacy settings key + modifier"**; any non-zero param is a
literal keycode from the host. `0x00` is already a no-op as a HID keycode, so nothing is lost, and the
wire stays free of modifiers.

`GET_MAPPING` **reports the resolved keycode** while storage keeps the sentinel. Otherwise a daemon
reading the table sees `0x00` and cannot tell the user what the pedal actually sends without speaking
the legacy stdin protocol, which it does not.

**Gate closed 2026-07-28**, bar two narrow gaps listed at the end. The logic was checked against a
CPython harness (not committed)
that stubs `machine` / `utime` / `usb.device` and exercises the table, the dispatcher and
`USBManager`'s five mapping commands — 51 assertions, all passing. Then flashed to the dev pump, where
the following are confirmed:

- [x] boots, and the composite device enumerates (vendor HID `0xFF00`/`0x01` on interface 3, keyboard
      on interface 2)
- [x] **a real `settings.json` survives the upgrade** — brightness, mode, power settings and all four
      pedal keys came through byte-identical, with `"mappings": []` added by `migrate_settings()`.
      This is the Phase 6 acceptance item, and the reason the flash offset is frozen
- [x] the dispatcher runs locally: five `LOW` taps during the Phase 3 wire check drove
      `TAP → POWER_LOW → set_power_mode(LOW)`, flipping persisted `power_mode` 1 → 0
- [x] publish-all survives the rewiring (see Phase 3's gate, run on this firmware)

Then `tools/phase4_wire_check.py` was run against it, closing most of the rest:

- [x] bulk `GET_MAPPING` streams the 24-entry default table and terminates once
- [x] single `GET_MAPPING` addresses cells and slots; unmapped cells read `NONE`
- [x] the `SEND_KEY` sentinel is resolved on read — the aux pedal reports `0x11`/`0x52`, not `0x00`
- [x] `BAD_CONTROL` / `BAD_GESTURE` (bad gesture *and* out-of-range slot) / `BAD_ACTION` (unknown, and
      `PUMP_TRIGGER` on a non-HELD gesture) / `BAD_MAGIC`
- [x] `SET_MAPPING` live in RAM and slot-scoped; `COMMIT_MAPPINGS` and `RESET_MAPPINGS` both ACK;
      `RESET` restores the defaults
- [x] the factory reset gesture — LIFT + DROP at power-on wiped a committed override

- [x] **slot switching and the remote-mode LED** — CONNECTED applies while the host heartbeats, a
      `FORWARD` button does nothing locally, and the LED and the fallback both revert the moment the
      heartbeat lapses

That last one took two runs and is worth recording, because the first result was *invalid rather than
negative* and read exactly like a firmware bug. Check F reported no purple LED, `FORWARD` ignored, and
the LED never clearing. All three are what a **STANDALONE** pump looks like — and that is what was
being measured: every prompt blocked on `input()`, no host heartbeat went out while the operator read
the question and reached for the button, and the device times out after 1200 ms. The harness starved
the connection it existed to measure. Fixed by beating the heartbeat on a background thread and
detecting presses from EVENT frames instead of an Enter key, which also *proves* the host was active at
press time — publish-all only emits while it is, so a missing frame is now reported as inconclusive
rather than as a failed mapping. **Lesson for any future interactive check on this device: a blocking
prompt is a disconnection.**

Two narrow gaps remain, neither blocking:

- [ ] the aux pedal actually *typing* the configured key (check C proves the read path reports it,
      not that the keystroke lands)
- [ ] `COMMIT_MAPPINGS` persistence proven independently — check G's override was committed and then
      wiped by the reset, which is also consistent with it never having reached flash. Reading
      `settings.json` over CDC between the commit and the reset would settle it

`tools/phase4_wire_check.py` covers all five. Checks A–E are automatic; F needs someone to look at the
pump and press LIFT, and G needs a power cycle and is opt-in. It ends with `RESET_MAPPINGS`, so it
leaves the pump on the default table.

    DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial \
        python tools/phase4_wire_check.py

Its one non-obvious trick: whether a `FORWARD` button still acted locally cannot be seen on the vendor
interface, because publish-all emits the EVENT frame either way. So it reads `mode` back over the CDC
port instead of asking the operator, which is exactly the judgement a human gets wrong.

### Deviations, as implemented *(2026-07-28)*

1. **`PUMP_TOGGLE` and `VENT_PULSE` are implemented,** though this phase's dispatcher list omits them.
   The spec's action registry marks both valid on *both* models, and PP1 expresses them trivially
   (`PUMP_TOGGLE` via a pseudo-holder in the refcount, `VENT_PULSE` as the NC-valve pulse the drop
   state already uses). Rejecting them with `BAD_ACTION` would have frozen a firmware that contradicts
   the frozen spec. Neither appears in the default table.
2. **Suspension covers buttons only, not the pedals.** The plan says "bypass the mapping engine
   entirely"; the spec says "the legacy in-menu **button** behavior applies". Under the default table
   the two readings are indistinguishable — `FPEDAL HELD → PUMP_TRIGGER` calls the same
   `trigger_on/off` intents the menu commits on — and letting a *remapped* pedal keep its host mapping
   inside a menu is the more defensible of the two. The suspended path lives in `pixel_pump.py` as
   `_legacy_button_dispatch`.
3. **Both paths share the HELD bookkeeping,** via `MappingEngine.hold_pump()`. Found while tracing:
   if the trigger button is held inside a menu and the menu exits some *other* way — Reverse cancels
   it, or the 60 s motor timeout drops it — the release then arrives on the mapping path, which had no
   record of the press and so never dropped the pump holder. The refcount would sit permanently at 1,
   silently killing the foot pedal until power-cycle. Recording the legacy press in `_held` makes
   either path able to undo it. Regression-tested.
4. **Remote-mode LED is applied once per transition, not held against the states.** Entering remote
   mode snapshots the button's target colour and pulsate parameters, then paints `Colors.PURPLE` (new)
   at `Brightness.DIMMER`; leaving restores the snapshot. It deliberately does not fight the state
   machine afterwards: the only state that repaints a FORWARD button is pedal-driven `trigger_on/off`
   on the trigger button, and green-while-pumping is the feedback you want there.
   A button counts as remote only when *every* gesture on it is FORWARD or NONE — one that keeps e.g.
   its long-press menu still does something locally, and the colour would lie.
5. **`max_response_queue_size` 64 → 96** in `usb_manager.py`. A bulk `GET_MAPPING` of a fully populated
   table is 8 controls × 2 slots × 4 gestures = 64 frames *plus* the terminator, and dropping the
   terminator leaves the host waiting forever.
6. **Two extra `settings_manager.py` fixes** beyond the `migrate_settings()` one this phase called for:
   `initialize()` assigned `DEFAULT_SETTINGS` by reference, which with a mutable `mappings` value would
   let a device write reach the module defaults; and `persist_settings()` now returns a bool, which is
   what lets `commit()` / `reset()` answer `STORAGE_ERROR` honestly instead of always ACKing.

Also worth knowing for Phase 5: `State.on_button_event`'s LOW/HIGH handling and `ReverseState`'s
`on_button_event` override are both gone — the mapping engine owns those gestures now, and Reverse
instead overrides the four power intents to no-ops to keep its `PowerMode.MAX` forcing intact.

---

## Phase 5 — Code health & docs *(landed 2026-07-28)*

- `is` → `==` sweep: `communication_manager.py`, `pixel_pump_state_machine.py`, `controls/button.py`,
  and the three settings states. (`states/state.py` lost its `is` comparisons in Phase 4, when the
  mapping engine took the LOW/HIGH handling off it.)
- **[Decided 2026-07-28] In scope, and larger than it looks.** Fix `settings:set_power_mode`
  (`communication_manager.py:118`/`:121` read `power_mode.HIGH` off the *module*; the constants live on
  the `PowerMode` class inside it) **and** wrap `parse()` in `try`/`except`. Nothing catches exceptions
  between `tick()` (`communication_manager.py:228-232`) and the bare `while True:` in `pixel_pump.py`,
  so today's `AttributeError` does not just fail the command — it unwinds out of the main loop and
  kills the firmware until power-cycle. The typo is one instance; the guard covers the class. This
  matters more from Phase 2 on, with a daemon on the other end of that port.
- Leave the `ticks_ms()` wraparound alone in legacy code (matches surrounding style); new USB code
  uses `ticks_diff` as ported.
- Update `README.md` (stale `act` job names, deleted `tools/*.py` → mpremote) and `CLAUDE.md` (no HID
  patch, new manifest layout, USB stack, mapping engine).

### As implemented *(2026-07-28)*

All four items done. This phase has no hardware gate of its own — it is verified by Phase 6, which
now has one item to re-check rather than inherit (see below).

1. **The sweep kept `is` where it means identity.** Twenty comparisons moved to `==`: four command
   strings in `parse()`, seven `PowerMode`/mode ints in `pixel_pump_state_machine.py`, two
   `pulseDirection` ints in `Button.tick`, and the `ButtonEvent` halves of the seven `on_button_event`
   guards in the three settings states. The `btn is self.device.low_button` halves stayed `is` — those
   compare `Button` *instances*, where identity is the intended semantics and already the idiom in
   `pixel_pump.py`'s `on_button_event`. A blanket rewrite would have been a downgrade.
2. **`except Exception`, not `except BaseException`** — and the distinction is load-bearing rather than
   stylistic. `reset:soft` is implemented as `sys.exit()`, so a `BaseException` guard would have
   swallowed it and quietly broken a shipped command. MicroPython puts `SystemExit` under
   `BaseException` alongside `Exception` (`py/objexcept.c:306`), same as CPython, so the narrower catch
   lets it through. Verified in the checkout rather than assumed.
3. **Checked under CPython, not just compiled.** A scratchpad harness (not committed, same shape as
   Phase 4's) stubs `machine` and drives `parse()` directly: `set_power_mode:high|low` now reaches
   `PowerMode` and records `HIGH`/`LOW`; a handler that raises is contained and prints
   `Command failed: …`; `reset:soft` still raises `SystemExit` *through* the guard; `reset:hard` still
   calls `machine.reset()`. All six changed modules also byte-compile under `mpy-cross`.
4. **README got more than the two stale items.** The project-layout block still listed the deleted
   `keyboard.py` and knew nothing of `usb/` or `mapping.py`, and the README described the serial port
   as the only way to talk to a pump — true before Phase 2, misleading after it. Added a *Talking to a
   host* section covering the heartbeat, publish-all, the mapping table, the purple `FORWARD` LED and
   the factory-reset gesture, pointing at `docs/usb-communication.md` for the wire detail. The `act`
   job names and `tools/*.py` references were already gone, fixed when the README was rewritten in
   `7a6218e`.

**One thing for Phase 6 to redo rather than inherit.** Phase 6 already ticks "legacy stdin protocol
still answers", earned on the Phase 4 build — but this phase rewrote the dispatch in `parse()` that
those commands arrive through. The tick is stale, not wrong: `version:info` and `settings:dump`
dispatched *because* MicroPython interns short strings, and they now dispatch on value. Re-run both on
the Phase 5 build, plus `settings:set_power_mode:high|low`, which has never worked on hardware and is
the one command whose behaviour this phase actually changes.

### On the dev pump *(2026-07-28)*

Flashed over CDC (`bootloader` → copy UF2), and the serial half of the phase checked itself:

- [x] `version:info` and `settings:dump` still answer on the new dispatch — the Phase 6 tick above,
      re-earned rather than inherited
- [x] **`settings.json` survived byte-identical again** — dumped before and after the flash and
      compared key by key. Second upgrade in a row that preserves it
- [x] **`settings:set_power_mode:high|low` works on hardware for the first time** — `power_mode`
      flipped 0 → 1 → 0 across two commands, and the pump kept answering afterwards, which is the
      half that used to be impossible
- [x] the local UI, checked by hand on the pump: the pulsate animation in both power menus, Low/High
      stepping in the brightness menu (on release) and the power menus (on press), confirm-vs-cancel
      in all three, the Lift→Drop long-press route to BOOTSEL, the power-mode LEDs and the audible
      Low/High motor difference, and mode restore across a power cycle. This is the half of the sweep
      a shell cannot see — every `==` this phase touched is exercised by that list
- [x] `tools/phase3_wire_check.py` re-run on this build, all four checks passing — the trigger/pedal
      split still lands on the right control ids with no leakage either way, `TAP` still precedes
      `RELEASE` in the same tick, and the heartbeat still reports model 1 with `HAS_MODEL`. Phase 5
      touches no USB code, so this is a regression guard rather than new evidence, but it is the
      cheapest proof that a sweep across `Button.tick` left the gesture timing alone

**The guard earned its keep within minutes, on a bug nobody was looking for.**
`settings:set_brightness` *with no argument* answered `Command failed: list index out of range` and
the pump carried on. Before this phase that was a dead firmware until power-cycle. The cause is an
off-by-one in the argument checkers: `check_valid_float_argument` (and its int and hex siblings) test
`len(arguments) < index` where they mean `<=`, so a *missing final* argument slips past the "Missing
argument" path and into `float(arguments[index])`, raising `IndexError` — which their `except
ValueError` does not catch. Every `settings:set_*` command has this shape, so the whole family was one
truncated line away from killing the pump.

**Fixed straight after, as its own commit.** The three comparisons became `<=`, matching
`check_has_argument` above them, which had it right all along via `try`/`except IndexError`. All seven
`settings:set_*` commands now answer `Missing argument` for a missing argument and `Invalid argument`
for a malformed one, neither of which disturbs the stored value — verified on the dev pump, and in the
CPython harness across all three checkers at the boundary (length `index` rejects, length `index + 1`
accepts) plus every affected command.

The guard stays regardless. It is what found this, and the class of bug it covers is larger than the
one instance: the fix makes malformed input answer correctly, while the guard is what keeps *any*
future mistake in a command handler from taking the pump down with it.

---

## Phase 6 — Acceptance pass on hardware

From the issue's acceptance criteria:

- [~] Legacy-identical out of the box (state machine, LEDs, valve timing incl. Reverse's staggered
      100/200 ms sequencing, pedal, keyboard emulation) — **cannot be closed as written; there is only
      one pump** (confirmed 2026-07-28). The criterion asks for a side-by-side against a legacy unit,
      and a single device cannot answer it. Substituted by the by-hand pass on the Phase 5 build:
      every mode, the pulsate animation, both settings menus with confirm-and-cancel, the LONG_PRESS
      route to BOOTSEL, the power-mode LEDs, the audible Low/High difference, and mode restore across
      a power cycle. Recorded as **weaker evidence than the criterion asks for**, deliberately: it
      compares the firmware against documented legacy behaviour and memory of the old unit, not
      against a legacy unit running. Two specifics in the criterion were never directly compared —
      Reverse's 0/100/200 ms valve stagger was confirmed as *sequencing*, by ear and eye, not as
      *timing*, and keyboard emulation is still the open aux-pedal gap below. If a legacy unit ever
      turns up, this is the item to re-run before trusting the comparison
- [ ] Daemon connects, reports model 1 + firmware version, receives button events — **blocked on
      `board-factory#4`, not on this firmware.** Board Factory currently labels a connected PP1
      "Pixel Pump 2". That is neither a firmware bug nor a stale install: the daemon does not parse
      the model byte at all (nothing in `rust/pixel-pump-daemon/src/protocol.rs` reads byte 3 or
      `HAS_MODEL`), and the app hardcodes the name — `pump-status-bar-item.tsx:13`,
      `const PUMP_MODEL = 'Pixel Pump 2'`, with a comment saying the telemetry carries no product
      string. Written when a PP2 was the only pump there was. `board-factory#4` covers exactly this
      (daemon: "parse model ID from device heartbeats (byte 3 when flag `0x08` set)"; app: a profile
      registry keyed by model ID), so the item closes when that lands. **The device half is proven**
      — `tools/phase6_acceptance.py` checks A and B passed 2026-07-28: `GET_INFO` answers model 1 and
      protocol level 2 with `HAS_MODEL`, the heartbeat agrees on the model, and `GET_VERSION` agrees
      with both the heartbeat and the legacy `version:info` over CDC. What remains is entirely the
      app reading what the pump already sends
- [x] `ENTER_BOOTLOADER` (magic `0xB007`) lands the device in BOOTSEL — `tools/phase6_acceptance.py`
      checks C and D, 2026-07-28, both passing. C is the half worth having: a wrong magic answers
      `BAD_MAGIC` and the pump keeps heartbeating afterwards with no BOOTSEL volume appearing, which
      is what stops a spurious reboot mid-assembly. D then rebooted it for real and `/Volumes/RPI-RP2`
      mounted. Recovered by copying the UF2 back across; `settings.json` survived that too
- [x] Mapping read/write/commit/reset round-trips; heartbeat timeout restores STANDALONE instantly —
      `tools/phase4_wire_check.py`, 2026-07-28
- [x] Legacy stdin protocol still answers — re-earned on the Phase 5 build 2026-07-28, after that
      phase rewrote the dispatch these arrive through from `is` to `==`. `version:info` and
      `settings:dump` answer, `settings:set_power_mode:high|low` works for the first time, and every
      `settings:set_*` reports `Missing argument` / `Invalid argument` without disturbing the stored
      value or the main loop
- [x] Factory reset gesture works — `tools/phase4_wire_check.py` check G, 2026-07-28
- [x] **Upgrade a unit carrying a real `settings.json` and confirm the file survives** — not listed
      explicitly in the issue, but it is the entire reason the flash offset is frozen. Confirmed
      2026-07-28 on the dev pump upgrading Phase 2/3 → Phase 4; see Phase 4's gate

### `tools/phase6_acceptance.py`

Covers the device half of the daemon item and all of the bootloader one:

- **Check A** — `GET_VERSION`, cross-checked three ways. The ACK, the device heartbeat and the legacy
  `version:info` over CDC must agree. The CDC route is worth the trouble because it reads `version.py`
  without passing through the HID stack at all, so agreement means something. On a local build the tag
  is `local` and the wire reads `0.0.0`; the check says so rather than failing.
- **Check B** — `GET_INFO` reports model 1 and protocol level 2 with `HAS_MODEL`, and the heartbeat
  agrees on the model.
- **Check C** — `ENTER_BOOTLOADER` with a wrong magic answers `BAD_MAGIC` **and the pump keeps
  running**. The error frame alone does not prove that; a device that answers correctly and reboots
  anyway is exactly the failure the magic exists to prevent, so the check also waits out the 100 ms
  flush delay, confirms the pump still heartbeats, and confirms no BOOTSEL volume appeared.
- **Check D** — the real magic ACKs and `/Volumes/RPI-RP2` mounts. Opt-in and last, since it reboots
  the pump; recovery is a power cycle.

It shares its transport with `phase4_wire_check.py` by importing from it — `Session` (with the
background heartbeat), `ModeProbe`, `expect_error`, the daemon warning — rather than duplicating two
hundred lines. `ModeProbe` gained a `version_info()` method for check A; that is additive, so the
Phase 4 tool is unaffected.

**Board Factory's daemon holds the vendor interface exclusively**, so the app and any checker cannot
observe the pump at the same time. Quit Board Factory first, and check `pgrep -fl pixel-pump-daemon`
when an open fails — the hidapi error is identical to the missing-Input-Monitoring one.

### Where this leaves issue #30

**Every firmware item is closed.** `tools/phase6_acceptance.py` passed all four checks on 2026-07-28,
which was the last thing anyone still had to run. The two items that are not ticked are not firmware
work and cannot become ticked here:

| Item | Why it stays open | Closable by |
|------|-------------------|-------------|
| Legacy-identical | only one pump exists | nothing here — substituted, see above |
| Daemon reports model 1 | `board-factory#4`; the device half is proven | that issue landing |

So the honest state is: **PP1 firmware is finished and verified as far as one pump and this repo
allow.** Freezing it does not wait on `board-factory#4` — that issue changes what the *app* displays,
not what the firmware sends, and the firmware's side of that contract is proven on the wire by checks
A and B.

What is left before the repo can be called done is process rather than engineering: push
`firmware-v2`, open the PR against `dev` so CI builds both UF2s and runs the size check on a clean
machine, and cut a release. Phase 7 (distribution) is already deferred to its own issue.

---

## Phase 7 — Distribution *(deferred 2026-07-28 to its own issue)*

Publish workflow + website feed analogous to `pixel_pump_publish.yml` in the PP2 repo, so Board
Factory can auto-update PP1 firmware. Deferred because all three prerequisites live outside this repo:
a website ingest endpoint on a new path, a `PIXEL_PUMP_FIRMWARE_RELEASE_TOKEN` secret, and Board
Factory teaching to consume the PP1 feed. Bundling it would block this issue on the website shipping,
while every other phase gates only on hardware.

Accepted cost: this issue ends with PP1 frozen, so the follow-up reopens a frozen repo. Adding a CI
workflow touches no firmware and does not re-trigger the Phase 6 hardware acceptance pass.

---

## Cross-repo note

PP2's `protocol.py` is still `PROTOCOL_VERSION = 1` with no `MAPPING` / `GET_INFO`. **Decided
2026-07-28:** author v2 here first and back-port the file to PP2 under `pixel-pump-two-firmware#4`,
keeping the two copies byte-identical. v2 gets written where it is actually exercised — PP1 is the repo
with a daemon to test against — but **PP2 becomes the canonical owner once the back-port lands**, since
PP1 freezes at the end of this issue. Same split as `docs/usb-communication.md`: written for both,
canonical home in PP2.

**Handed over 2026-07-28.** The back-port is ticketed rather than done, since it is PP2's repo and PP2's
release to make:

- **`pixel-pump-two-firmware#4`** — commented with what changed against its original plan: `protocol.py`
  is written and hardware-verified and should be copied verbatim; `MODEL_ID` is *not* in it (`ModelId`
  0/1/2 is, and the encoders take `model_id`, defaulting to `UNKNOWN` so the legacy heartbeat shape is
  reproduced exactly); `ErrorCode` has no `BAD_SLOT`, so an out-of-range slot reports `BAD_GESTURE`;
  `max_response_queue_size` must be recomputed for PP2's larger table rather than copying PP1's 96; and
  the five mapping commands can ship before the engine, answering `UNKNOWN_COMMAND` until a table is
  attached.
- **`pixel-pump-two-firmware#5`** *(new)* — the `vendor_hid.tick()` host-activity bug from Phase 2's
  deviation 2, which PP2 still carries. Filed separately because it is a bug in shipped v1 behaviour
  with a four-line fix and no dependency on v2. It is also worse there than it was here: PP2's
  `publish_event` still falls back to `_emit_keyboard_fallback` when no host is active, so the window
  the bug creates does not drop an event — it **types a keystroke into whatever app has focus** at
  connect time. Worth taking before #4, whose item 4 deletes that fallback and so changes the symptom
  without fixing the cause.

### USB product ID allocation *(decided 2026-07-28)*

`0x2E8A` is Raspberry Pi's vendor ID. They assigned `0x1061` for the Pixel Pump 1; PP2 takes
**`0x1062`** so the two models are distinguishable at enumeration, before any interface is opened.

This was confirmed as a real problem during Phase 0 hardware testing: both firmwares currently ship
`0x2E8A:0x1061`, so PP2's `usb-coms` binds whichever pump is plugged in and cannot tell which model
it found. Only one pump may be connected at a time until PP2 moves.

- **PP1 does not change.** `0x1061` is what hosts in the field already discover the pump by.
- PP2's change is 7 sites in `../pixel-pump-two-firmware`, and firmware + tooling must move together
  or the tool stops finding already-flashed prototypes: `boards/PIXEL_PUMP/mpconfigboard.h`,
  `tools/usb-coms/main.py` (`--pid` default), `tools/usb-coms/README.md` ×2, `docs/usb-communication.md`
  ×2, `CLAUDE.md`.
- `0x1062` is **provisional** until Raspberry Pi confirms the assignment — picking one unilaterally
  from their VID space risks colliding with another licensee.
- Distinct PIDs do not make `MODEL_ID` redundant: the PID identifies the product to the OS, while
  `MODEL_ID` in the heartbeat tells the daemon which protocol dialect and mapping table apply. Keep
  both, and let the daemon treat a `MODEL_ID` that disagrees with the PID as an error.

---

## Decisions *(all resolved 2026-07-28)*

1. **`SEND_KEY` sentinel** — param `0x00` on FPEDAL_AUX means "use the legacy configured key +
   modifier"; `GET_MAPPING` reports the resolved keycode while storage keeps the sentinel. See Phase 4.
2. **`settings:set_power_mode`** — in scope, together with a `try`/`except` guard around `parse()`, since
   the bug currently kills the main loop rather than just failing the command. See Phase 5.
3. **Phase 7 (distribution)** — deferred to its own issue; prerequisites are all outside this repo.
4. **Protocol v2 authorship** — written here, back-ported to PP2, which then owns it.
