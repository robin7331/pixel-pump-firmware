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

> Successor project: `../pixel-pump-two-firmware` (RP2354A, MicroPython v1.28.0, async). Different
> architecture — don't copy patterns between them without checking. Two deliberate exceptions, kept
> in sync rather than reinvented: `docs/usb-communication.md` (the USB protocol spec — canonical copy
> lives in PP2, never edit this one independently) and `src/pixel_pump/usb/`, ported from PP2.
>
> **PP2 owns `protocol.py`.** Protocol v2 was authored here and back-ported verbatim under
> `pixel-pump-two-firmware#4`, which closed on 2026-07-29; the two copies are byte-identical today and
> must stay that way, which is why the model id lives in `usb_manager.py` and not in `protocol.py`.
> The `vendor_hid.tick()` fix below went across as `pixel-pump-two-firmware#5`, also closed. PP1 is
> downstream of both now — change `protocol.py` in PP2 and re-sync, not the other way round.

## Docs & where facts live

The README is the reference: building, flashing, mpremote, the serial command table, the project
layout. It is written for a contributor and it is authoritative — **do not restate its content here.**
This file holds what a contributor does not need and an agent does: conventions, invariants and the
traps that have already cost someone a day.

Three of those are worth stating up front because they are cheap to trip and expensive to debug:

- **Always run `tools/checkFirmwareSize.sh` after adding frozen code.** The 2 MB of flash is split
  640 KiB firmware / 1408 KiB littlefs, but `memmap_mp_rp2040.ld` is handed the *whole* 2 MB — an
  oversized image links silently and then overwrites the filesystem, `settings.json` included, on
  first boot. The boundary cannot move without wiping every unit in the field, so this check is the
  only guard. CI runs it as a hard failure. Current usage: 346,088 B blank / 393,772 B full, ~60 % of
  ceiling.
- **The MicroPython checkout already exists at `./micropython`** — v1.28.0, submodules fetched, both
  variants built, ~450 MB, ignored via `.gitignore`. Do not re-clone it; the disk runs close to full.
- **`mpconfigvariant_EMPTY.cmake` must exist** or cmake hard-errors on `MICROPY_BOARD_VARIANT`. The
  manifests decide what is frozen: `manifest_shared.py` is included by `manifest_empty.py` and by
  `manifest.py`, which adds `src/`.

Plans live in `docs/plans/` only while they are in flight. Once the work lands, the plan is deleted
and anything still load-bearing moves here — a finished plan is archaeology, and a stale one is
worse than none. Issue #30's plan was deleted on 2026-07-29 for exactly that reason.

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
  pin; the `secondary_switch_pin` OR went away when the trigger and the pedal became separate
  controls.
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
| `usb/keyboard.py` | `KeyboardInterface` wrapper; translates stored HID usages to negative modifiers. `enabled=False` builds no interface and every send answers `False` |
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

`CommunicationManager` reads newline-terminated, colon-separated commands from USB stdin. **The full
command table is in the README** (§ Serial commands); what follows is only what is not obvious from
reading it.

`settings:persist` re-joins arguments on `:` so JSON payloads survive the split.

This protocol is **not** superseded by vendor HID — it stays the configuration path for the four
`secondary_pedal_*` keys, which the wire protocol deliberately cannot carry (`SEND_KEY` has no
modifier byte), and for `keyboard_enabled`. Both paths are live at once.

`set_keyboard_enabled` is the only settings key that changes what the device *is* rather than how it
behaves, so it is worth knowing four things about it. It is the one `settings:set_*` that **echoes**
— `keyboard_enabled:0|1`, normalised, not a parrot of what was sent — because
`docs/usb-communication.md` specifies it: PP2 shares this command over a CDC line protocol carrying
nothing else, so the reply is the host's only confirmation. It does **not** reset the pump, unlike
`settings:persist` and `settings:reset`, because a host usually writes several keys before
rebooting; nothing happens until `reset:hard` or a power cycle. Any nonzero int is on. And an old
host that writes the whole dict through `settings:persist` without the key silently re-enables the
keyboard, since `migrate_settings()` fills the gap from `DEFAULT_SETTINGS`; that fails safe, but it
will surprise anyone debugging why the interface came back.

## Vendor HID (protocol v2)

`docs/usb-communication.md` is the spec and the source of truth for anything on the wire. The firmware
side is `src/pixel_pump/usb/`; USB is initialized **once**, early in `pixel_pump.py`, with
`builtin_driver=True` so the CDC interface survives for `CommunicationManager`.

- Composite device: HID keyboard + `Pixel Pump Vendor HID` (usage page `0xFF00`), 8-byte reports.
  The keyboard half is conditional: `settings.json`'s `keyboard_enabled` (default `true`) decides
  whether that interface is registered at all, which is the only device-side way to stop macOS'
  Keyboard Setup Assistant (issue #33; `bCountryCode` does not drive it, see #31). This is why
  `SettingsManager` is constructed in `pixel_pump.py` *above* the USB init and then handed to
  `PixelPumpStateMachine` — the key has to be readable before the interfaces are registered, and one
  shared instance means USB can never enumerate on a different view of `settings.json` than the rest
  of the firmware runs on. Keep that ordering.
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
- **`vendor_hid.tick()`'s open edge used to discard a host frame that had already arrived.** The
  branch cleared `_last_host_rx_ms` unconditionally, so a frame landing between the interface opening
  and the next `tick()` was thrown away and the device stayed inactive until the host's *next*
  heartbeat (~400 ms with the daemon) — which a daemon that opens and immediately writes hits on every
  connect. The close edge already clears that state, so the reset was redundant as well as lossy.
  Fixed here, and taken by PP2 as `pixel-pump-two-firmware#5`.
- To watch the wire, `tools/pump_check.py` is the single entry point; see the README (§ Talking to a
  host) for how to run it and what the groups cover. PP2's `tools/usb-coms` gives a raw frame dump.
  Two things about it matter when changing firmware rather than running it:
  - **It shares the firmware's protocol vocabulary rather than copying it.** `pumpcheck/firmware.py`
    imports `src/pixel_pump/usb/protocol.py` directly — that file carries a CPython shim for
    `micropython.const` and imports nothing else, which is what makes it host-importable. Keep it that
    way: adding an import to `protocol.py` breaks every check at once.
  - **Two firmware facts it cannot import are guarded automatically.** `pumpcheck/checks_static.py`
    holds an independent `EXPECTED_DEFAULTS` and diffs it against `mapping.py`'s `DEFAULTS`, parsed
    with `ast` (`mapping.py` itself cannot be imported — it pulls in `utime`), and cross-checks its
    `VID`/`PID` against `boards/PIXEL_PUMP/mpconfigboard.h`. Change either and CI fails with both
    sides named. The `DEFAULTS` copy is deliberate: importing it would make the hardware checks
    tautological, proving only that the device agrees with its own source.

  Everything else is manual. A new control, gesture, command or settings key wants a matching check in
  the group that owns it — `wire`, `mapping`, `identity` or `keyboard`.
- If opening the vendor interface fails with `exclusive access and device already open`, the likely
  cause is that **Board Factory's `pixel-pump-daemon` holds it** whenever its dev app runs. Check
  `pgrep -fl pixel-pump-daemon` before touching System Settings — the message reads as a permissions
  problem and usually is not one. Input Monitoring was thought to be a second cause, and this note
  used to say the checkers had to be launched from Terminal; that is **no longer true** on this Mac.
  On 2026-07-29 `hid.Device(path=...)` opened the `0xFF00` interface from an agent shell on the first
  try and a full acceptance run went end to end from there, reboots included. Try the open before
  assuming a permission wall.
- The `mapping` group reads `mode` back over CDC to judge whether a `FORWARD` button still acted
  locally. That question cannot be answered on the vendor interface — publish-all emits the EVENT
  frame either way.
- **A blocking prompt is a disconnection.** The device drops to STANDALONE 1200 ms after the last host
  write, so any `input()` in a checker times the host out mid-question, and the answer then describes
  a standalone pump. This cost a full round of false failures that read convincingly like mapping-engine
  bugs. `Session.start_keepalive()` beats on a background thread, and the checks wait for EVENT frames
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
  `_legacy_button_dispatch` handles buttons the legacy way until the menu exits. **Suspension covers
  buttons only, not the pedals** — deliberately. The spec says "the legacy in-menu **button** behavior
  applies", and under the default table the two readings are indistinguishable anyway
  (`FPEDAL HELD → PUMP_TRIGGER` calls the same `trigger_on/off` intents the menu commits on). Letting
  a *remapped* pedal keep its host mapping inside a menu is the more defensible of the two.
  Both paths share the HELD bookkeeping via `MappingEngine.hold_pump()`, and that is not optional: if
  the trigger button is held inside a menu and the menu exits some *other* way — Reverse cancels it,
  or the 60 s motor timeout drops it — the release arrives on the mapping path, which without the
  recorded press never drops the pump holder. The refcount then sits at 1 forever, silently killing
  the foot pedal until power-cycle.
- **Remote-mode LED:** purple at `Brightness.DIMMER` on buttons whose active-slot gestures are all
  `FORWARD`/`NONE`. Applied on transition, snapshotting and restoring the button's colour and pulsate
  state; it deliberately does not fight the state machine afterwards.
- **Control appearance** (issue #35, spec §Control appearance): `FORWARD`'s param is
  `(animation << 4) | colour`, so a host can badge each forwarded button rather than getting six
  identical purple ones. `decode_appearance()` degrades at *render* time and `is_valid_action()`
  deliberately does not check the param — a reserved byte must ACK and round-trip verbatim, or the
  catalog could never outgrow this firmware. PP1 draws `SOLID` and `PULSE`; `SPIN`/`RAINBOW` are
  ring-only and land as `SOLID`. Three things are easy to get wrong:
  - **`APPEARANCE_SCAN` is gesture-*id* order, which `GESTURES` is not** (`GESTURES` leads with
    `PRESS` because dispatch cares about the press edge). The appearance is the first non-zero param
    among the `FORWARD` cells in id order, so an implicit zero-param `FORWARD` never masks one a host
    wrote. Don't reuse `GESTURES` here.
  - **A repaint keeps the *original* snapshot.** `_apply_remote_leds` re-renders when the appearance
    changes while a button stays remote; restoring the purple badge on the way out instead of the
    state machine's colour would strand the LED.
  - `Colors.AMBER` / `Colors.CYAN` exist only for this palette and carry no device-side meaning,
    unlike BLUE/RED/GREEN/PURPLE. Brightness stays device-owned — the global user setting must keep
    winning, which is why the host picks colour and animation but not brightness.
- **Factory reset:** LIFT + DROP held 3 s at power-on, checked before the boot sequence, resets the
  table and flashes the LEDs white. This is the only way back from a table that forwards every button.

## Release distribution (issue #34)

Publishing a draft release fires `pixel_pump_publish.yml`, which POSTs `firmware.uf2` to
`robins-tools.com/downloads/pixel-pump-firmware/` — the feed Board Factory reads. The **README covers
the mechanics** (§ CI); the wire contract lives in the website repo, and the CI-facing summary is
board-factory's `docs/website-pixel-pump-firmware-endpoint.md`. What is not obvious from either:

- **Both workflows are ports of PP2's files of the same name, and the two repos should stay in step.**
  In `pixel_pump_publish.yml` only the URL prefix, the token and the artifact filename differ. Fix a
  bug in one, carry it across — the same standing arrangement as `protocol.py`, except this one PP1
  does not receive from PP2 automatically. `pixel_pump_main.yml` diverges more (PP1 runs
  `pump_check.py static`, PP2 does not), but as of 2026-07-29 its **release step is the same action in
  both**, so that half travels too.
- **The draft-creating action was `marvinpinto/action-automatic-releases@latest` until 2026-07-29.**
  It is archived upstream and its `action.yml` still declares `node12`, while runners now force even
  node20 actions onto node24. It had not run here since v1.0.1 in 2023, so the first tag of the
  rewritten firmware would have been its first exercise on a modern runner — and a failure there costs
  the whole five-minute build and leaves no draft for `pixel_pump_publish.yml` to key off. Replaced by
  PP2's `softprops/action-gh-release@v2` (`title:` becomes `name:`, the token moves from a `repo_token`
  input to the `GITHUB_TOKEN` env var). The job carries an explicit `permissions: contents: write`
  rather than leaning on the repo-wide default — that default is `write` in PP1 and `read` in PP2, and
  the explicit block is what lets the same file work in either.
- **The token has a `ONE` the URL slug deliberately lacks.** Secret and Forge env var are
  `PIXEL_PUMP_ONE_FIRMWARE_RELEASE_TOKEN` (website services key `pixel_pump_one_firmware`), while the
  line is `/downloads/pixel-pump-firmware/`. Not a typo: the slug matches this repo's name and froze the
  moment anything shipped against it, whereas the ONE/TWO pairing is what stops the two lines' tokens
  being confused. Anyone "fixing" either side breaks ingest with a 401 or a 404.
- **Tags must be strict `vMAJOR.MINOR.PATCH`.** The device reports three bytes, so a prerelease suffix
  cannot exist on the wire and must not reach the feed. This repo's history carries `latest`, `false`
  and a malformed `v.0.0.4`; the job's `startsWith(tag, 'v')` condition skips the first two and the
  semver check fails loudly on the third. Those tags are dead now (see below) but the guards stay —
  they are what makes publishing anything unexpected a no-op rather than a bad ingest.
- **There is one long-lived branch, `main`, and no prerelease channel.** The `dev` branch and
  `pixel_pump_dev.yml`'s rolling `latest` draft prerelease were both retired on 2026-07-29 to match
  PP2, which never had either. Beta/dev *distribution* stays deferred rather than designed away: the
  wire can already say "dev build" (`Flags.DEV_BUILD`) but not *which* dev build, since the three
  version bytes are the last tag. That is the real blocker, and it is firmware-side, not
  website-side.
- **`pixel_pump_build.yml` is the pre-tag build, and it is deliberately not a release.** Added
  2026-07-29, after a few hours in which nothing built before a tag at all. That gap was the problem:
  a tag is the only heavy gesture in this repo — it creates a draft and arms `pixel_pump_publish.yml`
  — and it was the only way to get a UF2 off a runner, so "does this commit flash and run?" cost a
  release candidate. The workflow runs on pushes to `main`, PRs into `main` and `workflow_dispatch`,
  and uploads both UF2s as a run artifact (30 days). It creates no tag, no release and no draft, so
  it can never reach the website feed. Two things follow that are worth keeping:
  - **It carries the same two guards as the tag build** — `pump_check.py static` and
    `checkFirmwareSize.sh` — which is the second reason it exists. Between the `dev` branch going
    away and this landing, both first ran at release time. Builds are native in both repos (README:
    "no Docker, no containers"; PP2 dropped its Act/Docker path on 2026-07-29), so running the size
    check locally as you go is still the first line — CI is the backstop, not a substitute.
  - **Its images are self-identifying, and that is load-bearing.** `generateVersionFile.py` describes
    an untagged commit as `v2.0.0-3-gabc1234`, which `parse_version()` reads as `(2, 0, 0)` with
    `dev = True`. So an artifact UF2 announces itself as a dev build over USB and cannot be confused
    with the release of the tag it descends from — which is exactly the ambiguity that makes a
    prerelease *channel* still unshippable. Do not "fix" the version file to drop the suffix.
  - PP2 carries the same workflow under the same name, minus the `pump_check.py static` step (no
    counterpart there). Same standing arrangement as the other two: fix one, carry it across.
- **A `release: published` event runs the workflow file from the tag's commit, not from `main`.** So a
  fix landed after a failed publish cannot re-run that publish; `workflow_dispatch` with the tag is the
  recovery path, and it is why that trigger exists. By the same rule a tag cut before the workflow
  existed can never publish at all.
- **Re-running is safe.** Ingest is atomic and republish-is-replace, so a failed POST published nothing
  and the previous release keeps serving.
- **The path is proven end to end, on 2026-07-29 with `v2.0.0`** — tag → build → draft → publish →
  POST → feed, with the UF2 the website serves verified byte-identical to the GitHub release asset.
  That run is also the first thing to exercise `PIXEL_PUMP_ONE_FIRMWARE_RELEASE_TOKEN` against Forge,
  and it is worth knowing why nothing earlier could: an unauthenticated POST to the ingest URL answers
  401, but so does an authenticated one when the *server* side is unset, because
  `AuthenticateReleaseIngest` rejects a missing configured token identically. A 401 proves the route is
  deployed and nothing about whether the two token values agree.

## Notes

- This is MicroPython — use `machine`, `rp2`, `utime`, `ujson`, not CPython equivalents. The CPython
  in the repo is `tools/`: `generateVersionFile.py`, which runs on the CI host, and `pump_check.py`
  plus the `pumpcheck/` package, which run on a developer's machine. `src/pixel_pump/usb/protocol.py`
  is the one file that must import cleanly under **both**, which is what its `const` shim is for.
- No test framework, no linter, no formatter. The only automated check is
  `tools/pump_check.py static`, which CI runs before the build; everything else is manual, on hardware.
- USB identity: VID `0x2E8A`, PID `0x1061`, "Robins Tools" / "Pixel Pump" (`boards/PIXEL_PUMP/mpconfigboard.h`).
  `0x2E8A` is Raspberry Pi's vendor ID; `0x1061` is the product ID they assigned for the Pixel Pump 1
  and **must not change** — hosts already in the field discover the pump by it. PP2 has moved to
  `0x1062` (registered as raspberrypi/usb-pid#44), so the two models are now distinguishable at
  enumeration, before any interface is opened. Distinct PIDs do not make `MODEL_ID` redundant: the PID
  identifies the product to the OS, while `MODEL_ID` in the heartbeat tells the daemon which protocol
  dialect and mapping table apply. A daemon should treat a `MODEL_ID` that disagrees with the PID as
  an error.
- The CPU is deliberately underclocked to 96 MHz, and QSPI pads are set to 2 mA / slow slew in
  `pixel_pump.py` — both are EMI/noise measures. The large register-address constant block at the top
  of that file is mostly unused; only `SetPadQSPI` reads from it.
- `usb_hid` is **gone**. It only ever existed via the patch in `drivers/rp2_hid/`, which the build
  stopped applying, and the directory itself is gone. USB is now runtime-
  configured through micropython-lib's `usb.device` (frozen into both images), so HID work goes
  through `usb.device.hid` / `usb.device.keyboard`, not `import usb_hid`. The old
  `src/pixel_pump/keyboard.py` went with it — the replacement is `usb/keyboard.py`.
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
  coexist on purpose: the legacy side was deliberately left alone.
- Every `settings:set_*` command reports a missing argument as `Missing argument` and a malformed one
  as `Invalid argument`, and neither disturbs the stored value. Confirmed on hardware 2026-07-28.
  Keep it that way: these are the answers a host parses.
- Comparisons are `==` on values and `is` only on objects. The surviving `is` compare
  `Button` *instances* (`btn is self.device.low_button`) and are correct as identity. Don't
  reintroduce `is` against ints, strings or enum constants: it happened to work for small ints and
  interned strings, but nothing guarantees it.

Already fixed, listed because the symptoms are worth recognising if they resurface:

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
