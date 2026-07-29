# Pixel Pump Firmware

MicroPython firmware for the Pixel Pump — a vacuum pick-and-place tool for PCB assembly.

It runs on an RP2040 (Raspberry Pi Pico) with MicroPython v1.28. The pump has six illuminated
buttons, three solenoid valves, a PWM-driven vacuum pump and two foot pedal inputs. The firmware is
a small state machine: **Lift**, **Drop** and **Reverse** are the three operating modes, and
long-pressing a button gets you into its settings.

Prebuilt firmware is on the [releases page](https://github.com/robin7331/pixel-pump-firmware/releases).
If you just want a working pump, grab `firmware.uf2` from there and skip to [Flashing](#flashing).

## The two firmware files

Every build produces two UF2 files. The difference matters:

```
firmware.uf2
# Flash this for regular use of the pump.
# It is MicroPython with the Pixel Pump firmware frozen into the image.
# It ignores any main.py you copy onto the device and runs the frozen one instead.

firmware-blank.uf2
# Plain MicroPython, without the Pixel Pump firmware frozen in.
# Flash this to develop: now you can copy your own .py files and have them run on boot.
```

So: `firmware.uf2` to use the pump, `firmware-blank.uf2` to hack on it.

## Flashing

### Entering the bootloader

With a working pump, **long-press Lift** to enter brightness settings, then **long-press Drop**.
All buttons turn white and the pump reboots into the bootloader. Power cycle to get back out.

If the pump isn't running or has no firmware on it, there's a hardware bootloader switch. Looking at
the pump from the front, there are two small holes on the left side — the one towards the back
exposes the switch. Reach in with something thin (one of the nozzles that came with the pump works)
until you feel it click, hold it, and power the pump on.

![Bootloader switch location](media/bootloader-switch-location.png)

You can also send `bootloader` over the [serial interface](#serial-commands).

### Copying the UF2

In bootloader mode the pump shows up as a mass storage device. Copy the UF2 onto it and wait for the
reboot. Once it restarts it should no longer appear as a drive — it has left the bootloader.

## Development

Flash `firmware-blank.uf2` first, then use [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html)
to get your code onto the device.

```bash
uv tool install mpremote   # or: pip3 install mpremote
```

### Mounting your working copy (recommended)

Mounting is the fastest loop — the device runs the files straight off your machine, so there's no
copy step at all. Add this to `~/.config/mpremote/config.py`:

```python
commands = { "debug": ["mount", "./src", "exec", "import main"] }
```

Then from the repo root:

```bash
mpremote debug
```

Edit a file, `Ctrl-C`, run it again. Note that the pump writes its `settings.json` to whatever
filesystem it's running from, so while mounted it lands in `src/` on your machine. That path is
gitignored.

### Copying files instead

If you'd rather have the code live on the device:

```bash
mpremote cp -r src/pixel_pump :        # the whole package
mpremote cp src/main.py :              # and the entry point

mpremote ls                            # see what's on the device
mpremote rm :main.py                   # remove a file
mpremote reset                         # reboot
```

If you have more than one board attached, pick the port explicitly:

```bash
mpremote devs                          # list attached devices
mpremote connect /dev/tty.usbmodem2201 ls
```

### A typical change

Say we want the button LEDs to render at 5 FPS instead of 30, just to see what happens.

The main loop lives at the bottom of `src/pixel_pump/pixel_pump.py` (`src/main.py` is a one-line
import). Find the render throttle and change the delay from **33** ms to **200** ms:

```python
    # Render the UI at 5 FPS.
    if utime.ticks_ms() - rendered_at > 200:
        renderer.flush_frame_buffer()
        rendered_at = utime.ticks_ms()
```

Run `mpremote debug` again and the buttons will animate in visible steps. Congratulations, that's
your first Pixel Pump hack 😈

Flash `firmware.uf2` from the [releases page](https://github.com/robin7331/pixel-pump-firmware/releases)
whenever you want to get back to a known-good pump.

## Serial commands

The firmware polls USB stdin for newline-terminated, colon-separated commands. Open a serial
terminal (`mpremote repl`) and type them — the running firmware consumes stdin, so they reach the
command parser directly.

| Command | Effect |
|---|---|
| `bootloader` | Reboot into the UF2 bootloader |
| `version:info` | Prints `tag,branch,commit_hash,timestamp` |
| `reset:soft` / `reset:hard` | Exit the program / hard reset the MCU |
| `settings:dump` | Print all settings as JSON |
| `settings:persist:<json>` | Overwrite all settings, then hard reset |
| `settings:reset` | Restore defaults, then hard reset |
| `settings:set_brightness:<float>` | Global LED brightness (clamped 0.35–0.8) |
| `settings:set_mode:lift\|drop\|reverse` | Switch operating mode |
| `settings:set_power_mode:high\|low` | Switch power mode |
| `settings:set_low_power_setting:<0-100>` | Low mode motor duty, in percent |
| `settings:set_high_power_setting:<0-100>` | High mode motor duty, in percent |
| `settings:set_keyboard_enabled:0\|1` | Register the HID keyboard interface, or don't (takes effect on the next boot) |
| `settings:set_secondary_pedal_key:<hex>` | HID keycode sent when the second pedal is tapped |
| `settings:set_secondary_pedal_key_modifier:<hex>` | Modifier for the above |
| `settings:set_secondary_pedal_long_key:<hex>` | HID keycode for a long hold |
| `settings:set_secondary_pedal_long_key_modifier:<hex>` | Modifier for the above |

Keycodes are HID scancodes — see the [scancode table](https://deskthority.net/wiki/Scancode).

### Turning the keyboard off

The pump enumerates as a keyboard so the second foot pedal can type. If you don't use that pedal,
macOS' Keyboard Setup Assistant popping up on a fresh machine is pure nuisance, and
`settings:set_keyboard_enabled:0` is the way out — it stops the keyboard interface being registered
at all, which is the only thing that reliably suppresses the dialog.

It behaves a little differently from the other settings commands, in three ways worth knowing:

- It **echoes** `keyboard_enabled:0` or `keyboard_enabled:1` back at you, normalised rather than
  parroted, so any nonzero number reads back as `1`. No other `settings:set_*` answers.
- It does **not** reboot the pump, unlike `settings:persist` and `settings:reset`. Nothing changes
  until the next `reset:hard` or power cycle, so you can write several settings and reboot once.
- Writing the whole settings dict back through `settings:persist` without the key **re-enables the
  keyboard**, because the missing key gets filled in from the defaults. Worth remembering if the
  interface reappears and you can't see why.

Everything else survives with the keyboard off: the vendor interface, the mapping table and the
serial port all work as normal, and the second pedal still reports its presses to a host — it just
doesn't type.

## Talking to a host

Besides the serial port, the pump enumerates as a composite USB device: a plain HID keyboard, plus a
vendor HID interface on usage page `0xFF00` that a host application uses to drive the pump.
[`docs/usb-communication.md`](docs/usb-communication.md) is the full spec; the short version:

- The device sends a **heartbeat** every 500 ms carrying its model id and firmware version. A host
  counts as connected only while it keeps writing back — the pump falls back to standalone behaviour
  1200 ms after the host goes quiet.
- While a host is connected, **every** control publishes its gestures (press, release, tap, hold,
  long hold) as event frames, whatever else that control also does locally.
- A host can read and rewrite the **mapping table**: which action each control and gesture triggers,
  with a separate column for standalone and connected use. Writes land in RAM until committed to
  flash. Out of the box both columns are classic Pixel Pump behaviour, so a pump with no host
  attached acts exactly like it always did.

If a host maps a button to `FORWARD` — meaning "just tell me, don't act" — that button glows purple
while connected. Should you ever end up with a table that forwards everything and no host to undo it,
**hold Lift + Drop while powering on** for three seconds: the LEDs flash white and the table is back
to defaults.

### Checking a pump

`tools/pump_check.py` runs the whole thing against a real pump. It's one entry point over five
groups, and with no arguments it runs them all in an order that leaves the pump usable:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --with hid --with pyserial python tools/pump_check.py
```

| Group | What it checks |
|---|---|
| `static` | The checker's expectations still match the firmware source — needs no pump, and CI runs it |
| `wire` | Control ids, the full gesture set, and the heartbeat's model id |
| `mapping` | The mapping table: reads, writes, slots, flash persistence, factory reset |
| `keyboard` | `keyboard_enabled` — enumeration with and without the keyboard interface |
| `identity` | `GET_VERSION`, `GET_INFO`, and both `ENTER_BOOTLOADER` magics |

Name groups to narrow it down (`pump_check.py mapping keyboard`), and pass `--auto` to skip
everything that needs someone standing at the pump. `--list` prints the groups.

Anything that can't be undone by itself asks first: the `identity` group offers to reboot the pump
into the bootloader, which is the only way to test that command for real, and the `mapping` group
offers the factory-reset gesture, which wants you to power-cycle the pump with two buttons held. Say
no to either and it's skipped. The `keyboard` group reboots the pump a couple of times over the
serial port, but waits it back onto the bus itself and restores the setting it found.

If opening the vendor interface fails with *exclusive access and device already open*, the usual
cause is Board Factory's daemon holding it — quit Board Factory, or check `pgrep -fl
pixel-pump-daemon`. It reads like a permissions problem and generally isn't one.

## Building

There's no Makefile here. A build is the MicroPython rp2 port compiled against the board definition
in `boards/PIXEL_PUMP/`, so you need a MicroPython checkout and an Arm bare-metal toolchain. It all
runs natively — no Docker, no containers.

### Prerequisites

```bash
brew install cmake
brew install --cask gcc-arm-embedded   # Arm's official toolchain — includes newlib
```

> Take the **cask**, not the `arm-none-eabi-gcc` formula. The formula ships a compiler with no
> newlib, and the pico-sdk build dies on a missing `nosys.specs`.

### Getting MicroPython

Clone it wherever you like — the examples below put it next to this repo. Match the version CI
pins, or you're testing a different firmware than the one that ships:

```bash
git clone --depth 1 --branch v1.28.0 https://github.com/micropython/micropython.git
cd micropython
make -C mpy-cross                 # bytecode compiler, needed to freeze src/
make -C ports/rp2 submodules      # pico-sdk, tinyusb, micropython-lib
```

### Building the two images

`BOARD_DIR` has to be an absolute path, so point a variable at your checkout of this repo and stay
in the `micropython/` directory:

```bash
export PP=/absolute/path/to/pixel-pump-firmware

# Optional — stamps real git metadata into version.py. Skip it and you get "local" placeholders.
python3 $PP/tools/generateVersionFile.py --output $PP/src/pixel_pump/version.py --repo $PP

# firmware-blank.uf2 — MicroPython and the USB stack, without src/
make -C ports/rp2 BOARD_DIR=$PP/boards/PIXEL_PUMP BOARD_VARIANT=EMPTY -j8

# firmware.uf2 — the same, with src/ frozen in
make -C ports/rp2 BOARD_DIR=$PP/boards/PIXEL_PUMP -j8
```

Each variant gets its own build directory:

```
ports/rp2/build-PIXEL_PUMP-EMPTY/firmware.uf2   → this is firmware-blank.uf2
ports/rp2/build-PIXEL_PUMP/firmware.uf2         → this is firmware.uf2
```

What ends up frozen is decided by the manifests in `boards/PIXEL_PUMP/`:

| Manifest | Frozen content |
|---|---|
| `manifest_shared.py` | The port manifest, plus micropython-lib's `usb-device`, `usb-device-hid` and `usb-device-keyboard` |
| `manifest_empty.py` | Shared only — this is the blank image, and why `import usb.device` works while you're mounting `src/` |
| `manifest.py` | Shared plus `src/` — this is the shipping image |

### Checking that it still fits

Run this whenever you add frozen code:

```bash
$PP/tools/checkFirmwareSize.sh \
  ports/rp2/build-PIXEL_PUMP-EMPTY/firmware.bin \
  ports/rp2/build-PIXEL_PUMP/firmware.bin
```

The pump's 2 MB of flash is split in two: 1408 KiB of littlefs at the top, where `settings.json`
lives, leaving 640 KiB for firmware. **Nothing in the build enforces that split.** The linker is
handed the whole 2 MB, so an image that outgrows 640 KiB links without a word of complaint and then
overwrites the filesystem the first time it boots. The boundary can't be moved either — that would
wipe the settings of every pump already in the field. This check is the only thing guarding it, and
CI runs it as a hard failure.

### CI

The workflows do exactly the above on an Ubuntu runner:

| Workflow | Trigger | Result |
|---|---|---|
| `pixel_pump_main.yml` | `v*` tag | draft release |
| `pixel_pump_publish.yml` | publishing a release | pushes `firmware.uf2` to the website feed |

There is no CI before a tag — you build natively as you work, so the checks that matter run where
you'd hit them first anyway. `pixel_pump_main.yml` runs `tools/pump_check.py static` before building. It needs no pump and no dependencies — it
reads `src/` and fails if the default mapping table has drifted from what the hardware checks expect,
so that turns up on a push rather than the next time someone plugs a pump in.

Releases are a two-step gesture, and the gate is yours: a `v*` tag builds and leaves a **draft**, so
nothing is public until you review it and hit Publish. That click is what fires `pixel_pump_publish.yml`,
which delivers `firmware.uf2` to `robins-tools.com/downloads/pixel-pump-firmware/` in one POST — the feed
Board Factory reads to offer a firmware update. `firmware-blank.uf2` is a development image and stays on
the GitHub release. Tag strictly as `vMAJOR.MINOR.PATCH`: the pump reports exactly three numbers over USB,
so anything else is rejected rather than published.

## Project layout

```
src/main.py                     Entry point — imports and starts the firmware
src/pixel_pump/
  pixel_pump.py                 Pin setup, object graph, boot sequence, main loop
  pixel_pump_state_machine.py   Holds the hardware and the current state
  states/                       One file per mode (lift, drop, reverse, settings, bootloader)
  controls/                     Button and GPIO event handling
  enums/                        Colors, brightness levels, power modes
  ui_renderer.py                WS2812 driver (PIO) and frame buffer
  motor.py, valve.py            Pump and solenoid control
  usb/                          Vendor HID stack — protocol frames, event publishing, keyboard
  mapping.py                    Which control and gesture does what, and the host-writable table
  communication_manager.py      Serial command parser
  settings_manager.py           settings.json persistence
  boot_sequence.py              Startup LED sweep and valve clicks
  version.py                    Git metadata — regenerated in CI, "local" placeholders in git

boards/PIXEL_PUMP/              MicroPython board definition, pin names and freeze manifests
docs/usb-communication.md       USB protocol spec (canonical copy lives in the Pixel Pump 2 repo)
tools/generateVersionFile.py    Writes version.py from git metadata (runs in CI)
tools/checkFirmwareSize.sh      Fails if an image would overrun the littlefs partition
tools/pump_check.py             Runs the hardware checks (see Checking a pump, above)
tools/pumpcheck/                Those checks, one module per feature area
```

There's no test suite and no linter. Testing is done by hand, on hardware, with
`tools/pump_check.py` — the exception being its `static` group, which reads `src/` rather than a pump
and runs in CI on every build.

## Contributing

Fork the repo, work the way described above, and open a pull request against `main` — the only
long-lived branch. Every push is built and checked; releases are tagged `v*` off `main`, reviewed as
a draft, and published by hand.

Happy hacking!
