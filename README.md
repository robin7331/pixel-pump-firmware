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
| `settings:set_secondary_pedal_key:<hex>` | HID keycode sent when the second pedal is tapped |
| `settings:set_secondary_pedal_key_modifier:<hex>` | Modifier for the above |
| `settings:set_secondary_pedal_long_key:<hex>` | HID keycode for a long hold |
| `settings:set_secondary_pedal_long_key_modifier:<hex>` | Modifier for the above |

Keycodes are HID scancodes — see the [scancode table](https://deskthority.net/wiki/Scancode).

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
| `pixel_pump_dev.yml` | push / PR to `dev` | draft prerelease tagged `latest` |
| `pixel_pump_main.yml` | `v*` tag | draft release |

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
  keyboard.py                   USB HID keyboard output
  communication_manager.py      Serial command parser
  settings_manager.py           settings.json persistence

boards/PIXEL_PUMP/              MicroPython board definition and freeze manifests
drivers/rp2_hid/                Legacy USB HID patch — no longer applied by the build
tools/generateVersionFile.py    Writes version.py from git metadata (runs in CI)
tools/checkFirmwareSize.sh      Fails if an image would overrun the littlefs partition
```

There's no test suite and no linter — testing is done by hand, on hardware.

## Contributing

Fork the repo, work the way described above, and open a pull request against `dev`. Development
builds are cut from `dev`; releases are tagged `v*` off `main`.

Happy hacking!
