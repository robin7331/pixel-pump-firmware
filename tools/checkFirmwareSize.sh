#!/usr/bin/env bash
#
# Verify that a linked firmware image fits below the littlefs partition.
#
# The rp2 linker script hands the firmware the *whole* 2 MB of flash
# (memmap_mp_rp2040.ld: LENGTH = __micropy_flash_size__ = PICO_FLASH_SIZE_BYTES),
# while MICROPY_HW_FLASH_STORAGE_BYTES carves the filesystem out of the top at
# runtime. Nothing in the build connects the two: an oversized image links
# without complaint and then silently overwrites the filesystem — and with it
# every field unit's settings.json — the first time it boots.
#
# Usage: tools/checkFirmwareSize.sh <firmware.bin> [<firmware.bin> ...]
#
# Pass the raw .bin, not the .uf2 — a UF2 wraps 256 bytes of payload in every
# 512-byte block, so its size is roughly double the actual flash footprint.

set -euo pipefail

FLASH_TOTAL_KIB=2048   # Pico / RP2040 as fitted on the Pixel Pump
STORAGE_KIB=1408       # MICROPY_HW_FLASH_STORAGE_BYTES in mpconfigboard.h

CEILING=$(((FLASH_TOTAL_KIB - STORAGE_KIB) * 1024))

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <firmware.bin> [<firmware.bin> ...]" >&2
    exit 2
fi

printf 'Flash ceiling: %s KiB (%s KiB flash - %s KiB littlefs)\n\n' \
    "$((CEILING / 1024))" "$FLASH_TOTAL_KIB" "$STORAGE_KIB"

missing=0
oversized=0

for image in "$@"; do
    if [ ! -f "$image" ]; then
        echo "MISSING  $image" >&2
        missing=1
        continue
    fi

    size=$(wc -c <"$image" | tr -d ' ')
    headroom=$((CEILING - size))
    percent=$((size * 100 / CEILING))

    if [ "$headroom" -lt 0 ]; then
        verdict="OVER BY $((-headroom)) bytes"
        oversized=1
    else
        verdict="$headroom bytes headroom"
    fi

    # Build directory, e.g. build-PIXEL_PUMP-EMPTY — that is what identifies the
    # variant. Fall back to the filename when the image is not in one.
    label=$(basename "$(dirname "$image")")
    if [ "$label" = "." ] || [ "$label" = "/" ]; then
        label=$(basename "$image")
    fi

    printf '%-24s %8s bytes  %3s%% of ceiling  %s\n' \
        "$label" "$size" "$percent" "$verdict"
done

echo

if [ "$oversized" -ne 0 ]; then
    echo "FAIL: firmware does not fit below the littlefs partition." >&2
    echo "Growing MICROPY_HW_FLASH_STORAGE_BYTES is not an option — moving the" >&2
    echo "partition boundary wipes settings.json on every unit in the field." >&2
    exit 1
fi

if [ "$missing" -ne 0 ]; then
    echo "FAIL: an image was not found — check the build directory paths." >&2
    exit 1
fi

echo "OK: all images fit below the littlefs partition."
