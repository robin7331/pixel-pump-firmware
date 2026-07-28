# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MicroPython firmware for the original Pixel Pump — a vacuum pick-and-place tool for PCB assembly.
Runs on RP2040 (Raspberry Pi Pico) with MicroPython **v1.28.0**, stock — no patches applied.

The build freezes micropython-lib's USB stack and the application code into a custom firmware image.
Two UF2s come out of every build:

- `firmware.uf2` — MicroPython **with** `src/` frozen in. What ships on a pump.
- `firmware-blank.uf2` — MicroPython plus the USB stack, without `src/`. Flash this for development,
  then push `src/` over USB.

> ⚠️ **Branch `firmware-v2` is mid-migration (issue #30, `docs/plans/`).** Phases 0–5 have landed, so
> both UF2s boot again. Phase 6 (the acceptance pass on hardware) is next, and all open decisions are
> resolved. Read `docs/plans/issue-30-micropython-1.28-protocol-v2.md` before touching USB code.
>
> Phases 2, 3 and 4 are all verified on a physical pump (2026-07-28), wire checks included —
> `tools/phase3_wire_check.py` and `tools/phase4_wire_check.py` both pass end to end. Two narrow gaps
> remain, neither blocking: the aux pedal's keystroke is confirmed only as far as the mapping table
> reporting it, and `COMMIT_MAPPINGS` persistence has not been proven independently of the reset that
> follows it. See the plan doc's Phase 4 gate.

> Successor project: `../pixel-pump-two-firmware` (RP2354A, MicroPython v1.25, async). Different
> architecture — don't copy patterns between them without checking. Two deliberate exceptions, kept
> in sync rather than reinvented: `docs/usb-communication.md` (the USB protocol spec — canonical copy
> lives in PP2, never edit this one independently) and `src/pixel_pump/usb/`, ported from PP2's
> equivalent in Phase 2.
>
> `src/pixel_pump/usb/protocol.py` is the exception to the exception: **protocol v2 was authored
> here**, and is to be back-ported to PP2 verbatim (`pixel-pump-two-firmware#4`), after which PP2 owns
> it. Keep the two copies byte-identical — that is why the model id lives in `usb_manager.py` and not
> in `protocol.py`. `vendor_hid.py` also carries a host-activity bug fix that PP2 still needs; see the
> plan doc's *Deviations*.

## Build & Development Commands

### Building firmware

There is no local Makefile. Builds are the MicroPython rp2 port compiled against `boards/PIXEL_PUMP/`,
natively — no Docker, no act. Needs `cmake` and Arm's toolchain (`brew install --cask gcc-arm-embedded`;
the `arm-none-eabi-gcc` *formula* has no newlib and fails on a missing `nosys.specs`).

**The checkout already exists at `./micropython`** — v1.28.0, submodules fetched, both variants built,
~450 MB, ignored via `.gitignore`'s `/micropython`. Do not re-clone it; the disk runs close to full.
The first two steps below are only for setting this up from scratch.

```bash
git clone --depth 1 --branch v1.28.0 https://github.com/micropython/micropython.git
cd micropython
make -C mpy-cross                 # needed to freeze src/
make -C ports/rp2 submodules      # pico-sdk, tinyusb, micropython-lib

export PP=/absolute/path/to/pixel-pump-firmware
make -C ports/rp2 BOARD_DIR=$PP/boards/PIXEL_PUMP BOARD_VARIANT=EMPTY -j8   # firmware-blank.uf2
make -C ports/rp2 BOARD_DIR=$PP/boards/PIXEL_PUMP -j8                       # firmware.uf2

$PP/tools/checkFirmwareSize.sh \
  ports/rp2/build-PIXEL_PUMP-EMPTY/firmware.bin ports/rp2/build-PIXEL_PUMP/firmware.bin
```

Each variant has its own build directory — `build-PIXEL_PUMP-EMPTY/` and `build-PIXEL_PUMP/`. The
manifests decide what is frozen: `manifest_shared.py` (port manifest + `usb-device`,
`usb-device-hid`, `usb-device-keyboard`) is included by `manifest_empty.py` and by `manifest.py`,
which adds `src/`. `mpconfigvariant_EMPTY.cmake` selects the empty one; it must exist or cmake
hard-errors on `MICROPY_BOARD_VARIANT`.

**Always run `tools/checkFirmwareSize.sh` after adding frozen code.** The 2 MB of flash is split
640 KiB firmware / 1408 KiB littlefs, but `memmap_mp_rp2040.ld` is handed the *whole* 2 MB — an
oversized image links silently and then overwrites the filesystem, `settings.json` included, on first
boot. The boundary cannot move without wiping every unit in the field, so this check is the only
guard. CI runs it as a hard failure. Current usage: 346,088 B blank / 392,404 B full, ~59 % of ceiling.

| Workflow | Job | Trigger | Release |
|----------|-----|---------|---------|
| `pixel_pump_dev.yml` | `dev-build` | push/PR to `dev` | draft prerelease, tag `latest` |
| `pixel_pump_main.yml` | `dev-build` | `v*` tags | draft release |

Both define a job named `dev-build`. The artifact paths matter: the blank build lands in
`build-PIXEL_PUMP-EMPTY/`, not `build-PIXEL_PUMP/`.

### Remote development on MCU

Flash `firmware-blank.uf2` first, then use mpremote (the old `tools/copy_files.py`,
`list_files.py`, `remove_files.py` scripts were removed in `d512b36` — mpremote replaced them):

```bash
uv tool install mpremote  # if not installed

# Add to ~/.config/mpremote/config.py:
# commands = { "debug": ["mount", "./src", "exec", "import main"] }

mpremote debug  # mounts local src/ on MCU and executes main
```

### Entering the bootloader

- Running pump: long-press **Lift** (→ brightness settings), then long-press **Drop**.
- Over serial: send `bootloader`.
- Dead pump: hold the recessed bootloader switch (rear hole on the left side, see
  `media/bootloader-switch-location.png`) while powering on.

## Architecture

### Blocking main loop (~30 FPS render)

`src/main.py` is a single import. `pixel_pump/pixel_pump.py` is **not a class** — it is module-level
setup code that constructs all hardware objects and then runs a bare `while True:` loop at the
bottom. Importing it *is* starting the firmware.

```
lift/drop/low/high/reverse/trigger .tick() → foot_pedal.tick() → secondary_pedal.tick()
  → no/nc/three_way valve.tick() → motor.tick()
  → pixel_pump.tick(ticks_ms) → communication_manager.tick() → usb_manager.tick()
  → mapping_engine.tick() → renderer.flush_frame_buffer()  (throttled to every 33 ms)
```

`usb_manager.tick()` drains the event queue (max 4 frames), emits the 500 ms device heartbeat, flushes
queued ACK/ERROR/MAPPING responses and releases any pending keyboard tap. Every send is non-blocking
(`timeout_ms=0`), so a host that stops draining its endpoints cannot stall the render loop.

No asyncio anywhere. Every subsystem is cooperative and must return from `tick()` quickly.

### State machine

`PixelPumpStateMachine` (`pixel_pump_state_machine.py`) owns the hardware references and delegates
all behaviour to a current `State` object in `states/`. Button callbacks in `pixel_pump.py` are thin —
they just forward to `pixel_pump.state.<intent>()`.

| State | Entered by | Behaviour |
|-------|-----------|-----------|
| `LiftState` | Lift press | Trigger held = vacuum on; release vents via NC valve after 500 ms |
| `DropState` | Drop tap | Latching — trigger toggles run/pause instead of momentary |
| `ReverseState` | Reverse tap | Forces `PowerMode.MAX`, sequences all three valves; restores previous mode on exit |
| `BrightnessSettingsState` | Long-press Lift | Low/High adjust global LED brightness; long-press Drop → bootloader |
| `LowPowerSettingsState` / `HighPowerSettingsState` | Long-press Low / High | Runs motor live while Low/High step the duty by ±5 % |
| `BootloaderState` | Serial `bootloader`, or Drop long-press in brightness settings | All LEDs white for 500 ms, then `machine.bootloader()` |

Conventions worth preserving:

- `State` base class defines every intent (`to_lift`, `to_drop`, `trigger_on`, …) as a no-op, so
  states only override what they handle. Unhandled intents are silently ignored by design. The
  mapping engine speaks exactly this vocabulary — `mapping.py`'s `_perform` is the full list.
- Four intents have a real base implementation rather than a no-op: `to_power_low` / `to_power_high`
  (set the power mode), `to_low_power_settings` / `to_high_power_settings` (enter the menus).
  `ReverseState` overrides all four to no-ops, because it forces `PowerMode.MAX`.
- `State.on_button_event` is now reached **only** from the settings-menu path — states that set
  `suspends_mapping = True` bypass the mapping engine and handle buttons themselves. Everything else
  goes through the engine, so the base implementation is a no-op.
- **Confirm vs. cancel** in settings states: `trigger_off()` commits and persists, `to_reverse()`
  discards and restores the old value. `on_motor_timeout` also cancels.
- Imports of sibling states are done **inside methods**, not at module top — this breaks the circular
  imports between states. Keep it that way.
- `set_last_state()` re-instantiates the previous state class; it does not restore the old instance.

### Key patterns

- **Callback wiring**: `Button` and `IOEventSource` take `on_*` callbacks. Nothing subclasses them.
- **`Button`** (`controls/button.py`): polled (no debounce), drives two LED indices, lerps toward a
  target colour at 30 FPS, supports `pulsate()` ping-pong animation. Emits TOUCH_DOWN, TOUCH_UP,
  TOUCH, TAPPED (50–300 ms) and LONG_PRESS (>750 ms) — the same gesture set and thresholds as
  `IOEventSource`, and on a quick release TAPPED precedes TOUCH_UP. One button drives exactly one
  pin; the `secondary_switch_pin` OR went away with the trigger/pedal split in Phase 3.
- **`IOEventSource`** (`controls/io_event_source.py`): raw GPIO → events (ACTIVATE, DEACTIVATE, HOLD,
  TAPPED 50–300 ms, LONG_HOLD >750 ms). Used for both pedals — `foot_pedal` (GPIO6) and
  `secondary_pedal` (GPIO7).
- **Pump holders** (`mapping.py`): the trigger button and the foot pedal are independent controls
  that both run the vacuum, so `MappingEngine.pump_press/pump_release()` refcount them through a
  `_pump_holders` set and only call `state.trigger_on()` / `trigger_off()` on the 0↔1 edge. Without
  it, "hold pedal, tap button, release button" would stop the pump mid-pick. The pedal has no LEDs,
  so the trigger button's feedback follows because both funnel into the same state intents.
  Anything that starts the pump outside the table (the settings-menu path) must go through
  `hold_pump()`, so `release_held()` can undo it — a holder that never drops silently kills the pedal.
- **Delayed valve actions**: `valve.activate(delay_ms)` / `deactivate(delay_ms)` schedule against
  `tick()` — this is how reverse mode staggers its three valves (0/100/200 ms).
- **Motor safety timeout**: 60 s, fires `on_timeout` → `state.on_motor_timeout()`.

### Module roles

| Module | Role |
|--------|------|
| `pixel_pump.py` | Module-level bootstrap: pin setup, object graph, boot sequence, main loop |
| `pixel_pump_state_machine.py` | Holds hardware refs + settings, owns current state, maps power mode → PWM duty |
| `states/` | One file per mode; `state.py` is the no-op base |
| `ui_renderer.py` | WS2812 driver — inline `@rp2.asm_pio` program + 12-LED frame buffer |
| `controls/button.py` | Polled switch + two-LED colour animation |
| `controls/io_event_source.py` | Polled GPIO → event enum |
| `motor.py` | PWM pump control (10 kHz), 60 s timeout |
| `valve.py` | Solenoid with optional delayed switching |
| `usb/protocol.py` | Frame encode/decode + the whole v2 wire vocabulary. Keep byte-identical with PP2 |
| `usb/vendor_hid.py` | Vendor HID interface (usage page `0xFF00`), TX seq, host-activity tracking |
| `usb/usb_manager.py` | Owns both HID interfaces, event queue, heartbeat, host command dispatch |
| `usb/keyboard.py` | `KeyboardInterface` wrapper; translates stored HID usages to negative modifiers |
| `mapping.py` | `MappingTable` (persisted table + the `USBManager` contract) and `MappingEngine` (dispatcher, pump refcount, remote-mode LEDs), plus the factory-reset gesture |
| `communication_manager.py` | Serial command protocol over USB stdin (non-blocking `select.poll`) |
| `settings_manager.py` | `settings.json` on device flash, with forward/backward key migration |
| `boot_sequence.py` | Rainbow LED sweep + valve click sequence at startup |
| `version.py` | Auto-generated in CI by `tools/generateVersionFile.py`; `"local"` placeholders in git |

## Hardware Pin Map

`boards/PIXEL_PUMP/pins.csv` gives every GPIO a name, so `Pin.board.PUMP`, `Pin.board.UI_LED_DATA` and
friends resolve in firmware built from this repo. The rp2 port picks the file up from the board dir
automatically. The application code in `pixel_pump.py` still passes raw pin *numbers* — the names are
available, not yet adopted:

| GPIO | `pins.csv` name | Function |
|------|-----------------|----------|
| 2 | `VALVE_NO` | Normally-open valve |
| 3 | `VALVE_NC` | Normally-closed valve (vent) |
| 4 | `VALVE_3W` | Three-way valve |
| 5 | `PUMP` | Pump motor PWM (10 kHz) |
| 6 | `FPEDAL` | Foot pedal (`IOEventSource`) — runs the vacuum alongside the trigger button |
| 7 | `FPEDAL_AUX` | Secondary foot pedal (`IOEventSource`, sends HID keys) |
| 8 / 9 | `BTN_LIFT` / `BTN_DROP` | Lift / Drop buttons |
| 10 / 11 | `BTN_HIGH` / `BTN_LOW` | High / Low buttons (note: High is 10, Low is 11) |
| 12 / 13 | `BTN_REVERSE` / `TRIGGER_BTN` | Reverse / Trigger buttons |
| 14 | `UI_LED_DATA` | WS2812 data — 12 LEDs, two per button |

LED index → button: Lift 0/1, Drop 2/3, Low 4/5, High 6/7, Reverse 8/9, Trigger 10/11.

## Serial Command Protocol

`CommunicationManager` reads newline-terminated, colon-separated commands from USB stdin:

```
bootloader
version:info                                  → tag,branch,commit_hash,timestamp
reset:soft | reset:hard
settings:dump                                 → full JSON
settings:persist:<json>                        (then hard reset)
settings:reset                                 (then hard reset)
settings:set_brightness:<float>
settings:set_mode:lift|drop|reverse
settings:set_power_mode:high|low
settings:set_low_power_setting:<0-100>
settings:set_high_power_setting:<0-100>
settings:set_secondary_pedal_key[_modifier]:<hex>
settings:set_secondary_pedal_long_key[_modifier]:<hex>
```

`settings:persist` re-joins arguments on `:` so JSON payloads survive the split.

This protocol is **not** superseded by vendor HID — it stays the configuration path for the four
`secondary_pedal_*` keys, which the wire protocol deliberately cannot carry (`SEND_KEY` has no
modifier byte). Both paths are live at once.

## Vendor HID (protocol v2)

`docs/usb-communication.md` is the spec and the source of truth for anything on the wire. The firmware
side is `src/pixel_pump/usb/`; USB is initialized **once**, early in `pixel_pump.py`, with
`builtin_driver=True` so the CDC interface survives for `CommunicationManager`.

- Composite device: HID keyboard + `Pixel Pump Vendor HID` (usage page `0xFF00`), 8-byte reports.
- Device heartbeat every 500 ms carrying model id `1` and the firmware semver. The host counts as
  *active* only while it writes to the vendor OUT path (1200 ms timeout) — opening the interface is
  not enough.
- **Publish-all rule:** while the host is active, *every* control publishes EVENT frames, whatever it
  also does locally. The wiring lives in `pixel_pump.py`'s `on_button_event`, `on_foot_pedal_event`
  and `on_aux_pedal_event`; buttons resolve through `_CONTROL_IDS_BY_BUTTON`, keyed on the `Button`
  object itself.
- Every control publishes the full gesture set: PRESS / RELEASE / TAP / HOLD / LONG_HOLD. HOLD is
  throttled to one frame per 120 ms per control in `USBManager`.
- The trigger button (`TRIGGER_BTN`, GPIO13) and the foot pedal (`FPEDAL`, GPIO6) publish separately.
- `USBManager(mapping=...)` is the seam to the mapping table, and its docstring is the contract
  `MappingTable` implements. It is assigned after `PixelPumpStateMachine` exists (the table needs the
  settings manager); until then the four mapping commands answer `ERROR UNKNOWN_COMMAND`.
- `ENTER_BOOTLOADER` requires magic `0xB007`, `RESET_MAPPINGS` requires `0xDEFA`. A wrong magic is
  `ERROR BAD_MAGIC` and must never reboot the pump mid-assembly.
- To watch the wire, two interactive checkers live in `tools/` — `phase3_wire_check.py` (control ids,
  gestures, heartbeat model) and `phase4_wire_check.py` (the mapping commands, slot switching,
  remote-mode LED, factory reset). PP2's `tools/usb-coms` gives a raw frame dump.

  ```bash
  DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid python tools/phase3_wire_check.py
  DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial python tools/phase4_wire_check.py
  ```

- **Any of them must be launched from Terminal** — macOS only opens a vendor HID interface for a
  process holding Input Monitoring. But `exclusive access and device already open` has a second,
  likelier cause that looks identical: **Board Factory's `pixel-pump-daemon` holds the interface**
  whenever its dev app runs. Check `pgrep -fl pixel-pump-daemon` before touching System Settings.
- `phase4_wire_check.py` reads `mode` back over CDC to judge whether a `FORWARD` button still acted
  locally. That question cannot be answered on the vendor interface — publish-all emits the EVENT
  frame either way.
- **A blocking prompt is a disconnection.** The device drops to STANDALONE 1200 ms after the last host
  write, so any `input()` in a checker times the host out mid-question, and the answer then describes
  a standalone pump. This cost a full round of false failures that read convincingly like mapping-engine
  bugs. `phase4_wire_check.py` beats the heartbeat on a background thread and waits for EVENT frames
  instead of Enter; do the same in anything new. A press with no EVENT frame means the host was
  inactive — report that as inconclusive, never as a failed mapping.

## Mapping engine (`mapping.py`)

Layer 1 of the spec's two-layer control model. A table keyed by `(control, gesture, slot)` holding
`(action, param)`; layer 2 (intents, chords) lives in Board Factory and never reaches the firmware.

- **Slots.** `STANDALONE` (0) applies unless `usb_manager.is_vendor_host_active()`, which selects
  `CONNECTED` (1). Resolution happens per event, so a heartbeat timeout falls back instantly.
- **Gestures** are not wire EventKinds: `PRESS`/`TAP`/`HELD`/`LONG_HOLD` (`DELTA_*` is encoder-only,
  so PP1 rejects it). `HELD` is the only momentary one — `dispatch()` starts it on the PRESS edge and
  `release_held()` undoes it on RELEASE, **replaying what the press recorded** rather than looking the
  table up again. That is what makes a host remapping or disconnecting mid-hold safe.
- **Defaults are classic PP1 behaviour in both slots**, so an updated pump acts like legacy firmware
  until a host writes the CONNECTED column. LIFT/DROP/REVERSE on `PRESS`, LOW/HIGH on `TAP` — that
  split is legacy fidelity, not taste.
- **`SEND_KEY` param `0x00` on `FPEDAL_AUX` is a sentinel** meaning "the key + modifier the legacy
  stdin protocol configured". The wire has no modifier byte by design. `GET_MAPPING` reports the
  *resolved* keycode; storage keeps the sentinel, so `settings:set_secondary_pedal_key` keeps working.
- **Persistence:** `SET_MAPPING` is RAM only, `COMMIT_MAPPINGS` writes flash. Only rows differing from
  `DEFAULTS` are stored, under `settings.json`'s `mappings` key — which **must** stay in
  `DEFAULT_SETTINGS` or `migrate_settings()` deletes it every boot. Corrupt rows are skipped, not
  fatal.
- **Settings menus suspend the engine** (`State.suspends_mapping`); `pixel_pump.py`'s
  `_legacy_button_dispatch` handles buttons the legacy way until the menu exits. Pedals are not
  suspended — see the plan doc's Phase 4 deviations.
- **Remote-mode LED:** purple at `Brightness.DIMMER` on buttons whose active-slot gestures are all
  `FORWARD`/`NONE`. Applied on transition, snapshotting and restoring the button's colour and pulsate
  state; it deliberately does not fight the state machine afterwards.
- **Factory reset:** LIFT + DROP held 3 s at power-on, checked before the boot sequence, resets the
  table and flashes the LEDs white. This is the only way back from a table that forwards every button.

## Notes

- This is MicroPython — use `machine`, `rp2`, `utime`, `ujson`, not CPython equivalents. The only
  CPython files in the repo are the three under `tools/`: `generateVersionFile.py`, which runs on the
  CI host, and `phase3_wire_check.py` / `phase4_wire_check.py`, which run on a developer's machine.
- No test framework, no linter, no formatter. Testing is manual, on hardware.
- USB identity: VID `0x2E8A`, PID `0x1061`, "Robins Tools" / "Pixel Pump" (`boards/PIXEL_PUMP/mpconfigboard.h`).
  `0x2E8A` is Raspberry Pi's vendor ID; `0x1061` is the product ID they assigned for the Pixel Pump 1
  and **must not change** — hosts already in the field discover the pump by it. PP2 moves to `0x1062`
  so the two are distinguishable at enumeration; until then both share `0x1061` and a host cannot tell
  them apart before opening the vendor interface. `0x1062` is provisional until Raspberry Pi confirms
  the assignment.
- The CPU is deliberately underclocked to 96 MHz, and QSPI pads are set to 2 mA / slow slew in
  `pixel_pump.py` — both are EMI/noise measures. The large register-address constant block at the top
  of that file is mostly unused; only `SetPadQSPI` reads from it.
- `usb_hid` is **gone**. It only ever existed via the patch in `drivers/rp2_hid/`, which the build
  stopped applying in Phase 0; the directory itself was deleted in Phase 1. USB is now runtime-
  configured through micropython-lib's `usb.device` (frozen into both images), so HID work goes
  through `usb.device.hid` / `usb.device.keyboard`, not `import usb_hid`. The old
  `src/pixel_pump/keyboard.py` went with it in Phase 2 — the replacement is `usb/keyboard.py`.
- **Modifiers are encoded differently on each side of `usb/keyboard.py`.** `settings.json` stores raw
  HID usages (`0xE0`–`0xE7`, `0x00` = none), while micropython-lib's `send_keys` wants modifiers as
  *negative* values (`r[0] |= -k`), i.e. `0xE0 + n → -(1 << n)`. The wrapper translates; anything
  outside that range is passed through as an ordinary keycode, which is what the legacy code did.
- Keyboard taps are **press now, release from `tick()`** (50 ms later) rather than press-and-release
  back to back, and every send is guarded by `is_open()`. Legacy `usb_hid.report()` blocked on a
  closed interface, which is the likely cause of issue #29 (aux pedal freezing the pump with no host
  attached) — worth confirming on hardware.
- Motor duty is stored 0–255 (`percentage * 2.55`) and then **squared** into 16-bit in `Motor.tick()`
  (`duty_u16(d * d)`), giving a quadratic response curve. Changing one side without the other will
  badly misscale power.
- Two different brightness scales exist: per-LED alpha (`Brightness.DIMMER/DEFAULT/BRIGHTER`,
  0.12–0.32) and the global `brightness_modifier` (clamped 0.35–0.8 in `SettingsManager`). They
  multiply together in `UIRenderer.flush_frame_buffer`.
- `Colors` are `(R, G, B)`; `UIRenderer` reorders to GRB for the WS2812s.

## Known rough edges

Pre-existing, and useful to know before touching the surrounding code:

- Timing uses plain `utime.ticks_ms()` subtraction rather than `utime.ticks_diff()`, so it breaks at
  the ~12.4-day wraparound. Consistent across the codebase; match the surrounding style unless you're
  deliberately fixing it. New USB code uses `ticks_diff` as ported from PP2 — the two conventions
  coexist on purpose, and Phase 5 deliberately left the legacy side alone.
- Every `settings:set_*` command reports a missing argument as `Missing argument` and a malformed one
  as `Invalid argument`, and neither disturbs the stored value. Confirmed on hardware 2026-07-28.
  Keep it that way: these are the answers a host parses.
- Comparisons are `==` on values and `is` only on objects, as of Phase 5. The surviving `is` compare
  `Button` *instances* (`btn is self.device.low_button`) and are correct as identity. Don't
  reintroduce `is` against ints, strings or enum constants: it happened to work for small ints and
  interned strings, but nothing guarantees it.

Fixed in Phase 5, listed because the symptoms are worth recognising if they resurface:

- `CommunicationManager.parse()` used to dispatch commands with `is` on strings built by
  `line.split(":")`. It worked only because MicroPython interns short identifiers.
- `settings:set_power_mode` read `power_mode.HIGH` off the *module* rather than the `PowerMode`
  class inside it, raising `AttributeError`. Nothing catches exceptions between
  `CommunicationManager.tick()` and the bare `while True:` in `pixel_pump.py`, so this did not just
  fail the command — it unwound out of the main loop and killed the firmware until power-cycle.
  `tick()` now wraps `parse()` in `try`/`except Exception`, which covers the whole class of
  malformed-input bugs. `SystemExit` derives from `BaseException` in MicroPython
  (`py/objexcept.c:306`), so `reset:soft` still exits through the guard.
- `check_valid_float_argument` and its int/hex siblings tested `len(arguments) < index` where they
  meant `<=`, so a **missing final argument** slipped past the "Missing argument" path into
  `float(arguments[index])` and raised `IndexError`, which their `except ValueError` does not catch.
  Every `settings:set_*` command had that shape — each was one truncated line away from killing the
  firmware. Found by the guard above within minutes of it landing.
