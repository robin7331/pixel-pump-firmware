#!/bin/bash

if which nproc > /dev/null; then
    MAKEOPTS="-j$(nproc)"
else
    MAKEOPTS="-j$(sysctl -n hw.ncpu)"
fi

function ci_pixel_pump_setup {
    sudo apt-get update
    sudo apt-get install gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential cmake git -y
    arm-none-eabi-gcc --version
}

function ci_pixel_pump_build {
    ls .
    make ${MAKEOPTS} -C mpy-cross
    git submodule update --init lib/pico-sdk lib/tinyusb
    make ${MAKEOPTS} -C ports/rp2
}