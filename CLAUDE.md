# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MicroPython firmware for the original Pixel Pump — a vacuum pick-and-place tool for PCB assembly.
Runs on RP2040 (Raspberry Pi Pico) with MicroPython v1.20, pinned in CI to commit `294baf52`.

The build patches MicroPython itself to add a `usb_hid` module (`drivers/rp2_hid/0001-...patch`, from
jimmo upstream), then freezes the application code into a custom firmware image. Two UF2s come out of
every build:

- `firmware.uf2` — MicroPython **with** `src/` frozen in. What ships on a pump.
- `firmware-blank.uf2` — MicroPython only. Flash this for development, then push `src/` over USB.

> Successor project: `../pixel-pump-two-firmware` (RP2354A, MicroPython v1.25, async). Different
> architecture — don't copy patterns between them without checking.

## Build & Development Commands

### Building firmware

There is no local Makefile. Builds run through GitHub Actions, which you can execute locally with
[nektos/act](https://github.com/nektos/act) (needs Docker running):

```bash
brew install act

act -j local-dev-build -b ./build   # .github/workflows/pixel_pump_dev_local.yml
```

Expect this to take a while — it compiles mpy-cross and the whole rp2 port. All three workflows
perform the same build: apply the HID patch → generate `version.py` → build `BOARD_VARIANT=EMPTY`
(→ `firmware-blank.uf2`) → clean → build again with the manifest (→ `firmware.uf2`).

| Workflow | Job | Trigger | Release |
|----------|-----|---------|---------|
| `pixel_pump_dev_local.yml` | `local-dev-build` | push to `dev` | none — for local `act` runs |
| `pixel_pump_dev.yml` | `dev-build` | push/PR to `dev` | draft prerelease, tag `latest` |
| `pixel_pump_main.yml` | `dev-build` | `v*` tags | draft release |

Two caveats if you touch CI: `README.md` documents `act -j dev-build` / `act -j release-build`, but
no `release-build` job exists and `dev-build` is defined in *two* workflows, so `-j dev-build` is
ambiguous. The local workflow also checks out into a `pixel-pump-firmware/` subdirectory while every
later step still references `$GITHUB_WORKSPACE/...` — verify it actually runs before relying on it.

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
lift/drop/low/high/reverse/trigger .tick() → secondary_pedal.tick()
  → no/nc/three_way valve.tick() → motor.tick()
  → pixel_pump.tick(ticks_ms) → communication_manager.tick()
  → renderer.flush_frame_buffer()  (throttled to every 33 ms)
```

No asyncio anywhere. Every subsystem is cooperative and must return from `tick()` quickly.

### State machine

`PixelPumpStateMachine` (`pixel_pump_state_machine.py`) owns the hardware references and delegates
all behaviour to a current `State` object in `states/`. Button callbacks in `pixel_pump.py` are thin —
they just forward to `pixel_pump.state.<intent>()`.

| State | Entered by | Behaviour |
|-------|-----------|-----------|
| `LiftState` | Lift tap | Trigger held = vacuum on; release vents via NC valve after 500 ms |
| `DropState` | Drop tap | Latching — trigger toggles run/pause instead of momentary |
| `ReverseState` | Reverse tap | Forces `PowerMode.MAX`, sequences all three valves; restores previous mode on exit |
| `BrightnessSettingsState` | Long-press Lift | Low/High adjust global LED brightness; long-press Drop → bootloader |
| `LowPowerSettingsState` / `HighPowerSettingsState` | Long-press Low / High | Runs motor live while Low/High step the duty by ±5 % |
| `BootloaderState` | Serial `bootloader`, or Drop long-press in brightness settings | All LEDs white for 500 ms, then `machine.bootloader()` |

Conventions worth preserving:

- `State` base class defines every intent (`to_lift`, `to_drop`, `trigger_on`, …) as a no-op, so
  states only override what they handle. Unhandled intents are silently ignored by design.
- `State.on_button_event` handles Low/High power-mode selection for all states; settings states
  override it entirely.
- **Confirm vs. cancel** in settings states: `trigger_off()` commits and persists, `to_reverse()`
  discards and restores the old value. `on_motor_timeout` also cancels.
- Imports of sibling states are done **inside methods**, not at module top — this breaks the circular
  imports between states. Keep it that way.
- `set_last_state()` re-instantiates the previous state class; it does not restore the old instance.

### Key patterns

- **Callback wiring**: `Button` and `IOEventSource` take `on_*` callbacks. Nothing subclasses them.
- **`Button`** (`controls/button.py`): polled (no debounce), 750 ms long-press threshold, drives two
  LED indices, lerps toward a target colour at 30 FPS, supports `pulsate()` ping-pong animation. A
  button may have a `secondary_switch_pin` OR'd in — the trigger button also answers to the foot pedal.
- **`IOEventSource`** (`controls/io_event_source.py`): raw GPIO → events (ACTIVATE, DEACTIVATE, HOLD,
  TAPPED 50–300 ms, LONG_HOLD >750 ms). Only used for the secondary pedal.
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
| `keyboard.py` | USB HID keyboard reports via the patched-in `usb_hid` module |
| `communication_manager.py` | Serial command protocol over USB stdin (non-blocking `select.poll`) |
| `settings_manager.py` | `settings.json` on device flash, with forward/backward key migration |
| `boot_sequence.py` | Rainbow LED sweep + valve click sequence at startup |
| `version.py` | Auto-generated in CI by `tools/generateVersionFile.py`; `"local"` placeholders in git |

## Hardware Pin Map

Pins are hard-coded in `pixel_pump.py` (there is no `pins.csv` in this generation):

| GPIO | Function |
|------|----------|
| 2 | Normally-open valve |
| 3 | Normally-closed valve (vent) |
| 4 | Three-way valve |
| 5 | Pump motor PWM (10 kHz) |
| 6 | Foot pedal — wired as the trigger button's secondary switch |
| 7 | Secondary foot pedal (`IOEventSource`, sends HID keys) |
| 8 / 9 | Lift / Drop buttons |
| 10 / 11 | High / Low buttons (note: High is 10, Low is 11) |
| 12 / 13 | Reverse / Trigger buttons |
| 14 | WS2812 data — 12 LEDs, two per button |

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

## Notes

- This is MicroPython — use `machine`, `rp2`, `utime`, `ujson`, not CPython equivalents. The only
  CPython file in the repo is `tools/generateVersionFile.py`, which runs on the CI host.
- No test framework, no linter, no formatter. Testing is manual, on hardware.
- USB identity: VID `0x2E8A`, PID `0x1061`, "Robins Tools" / "Pixel Pump" (`boards/PIXEL_PUMP/mpconfigboard.h`).
- The CPU is deliberately underclocked to 96 MHz, and QSPI pads are set to 2 mA / slow slew in
  `pixel_pump.py` — both are EMI/noise measures. The large register-address constant block at the top
  of that file is mostly unused; only `SetPadQSPI` reads from it.
- `usb_hid` only exists because of the patch in `drivers/rp2_hid/`. It is not stock MicroPython, so a
  plain upstream firmware will fail at `import usb_hid`. The `.py` files alongside the patch are
  upstream reference drivers, not used by `src/`.
- Motor duty is stored 0–255 (`percentage * 2.55`) and then **squared** into 16-bit in `Motor.tick()`
  (`duty_u16(d * d)`), giving a quadratic response curve. Changing one side without the other will
  badly misscale power.
- Two different brightness scales exist: per-LED alpha (`Brightness.DIMMER/DEFAULT/BRIGHTER`,
  0.12–0.32) and the global `brightness_modifier` (clamped 0.35–0.8 in `SettingsManager`). They
  multiply together in `UIRenderer.flush_frame_buffer`.
- `Colors` are `(R, G, B)`; `UIRenderer` reorders to GRB for the WS2812s.

## Known rough edges

Pre-existing, and useful to know before touching the surrounding code:

- `CommunicationManager.parse()` dispatches with `is` on strings built by `line.split(":")`
  (`if command is "bootloader"`), while its sub-parsers correctly use `==`. Identity comparison on
  runtime-built strings is not guaranteed — verify on hardware before trusting any of those branches.
  `PixelPumpStateMachine.__init__` and `Button.tick` have the same `is`-on-int pattern.
- `settings:set_power_mode` calls `power_mode.HIGH` / `power_mode.LOW`, but `power_mode` is the
  *module* — the constants live on the `PowerMode` class inside it. This path raises `AttributeError`.
- Timing uses plain `utime.ticks_ms()` subtraction rather than `utime.ticks_diff()`, so it breaks at
  the ~12.4-day wraparound. Consistent across the codebase; match the surrounding style unless you're
  deliberately fixing it.
- `README.md` is stale in two places: the "Hacking around" section documents the deleted `tools/*.py`
  scripts (point people at mpremote), and its `act` commands name a job that doesn't exist.
- `pixel_pump.py` creates a stray `foot_aux = Pin(7, ...)` that nothing reads; `secondary_pedal` owns
  that pin.
