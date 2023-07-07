# cmake file for Pixel Pump.
set(PICO_BOARD "pico")

if(NOT "${BOARD_VARIANT}" STREQUAL "EMPTY")
  set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)
endif()