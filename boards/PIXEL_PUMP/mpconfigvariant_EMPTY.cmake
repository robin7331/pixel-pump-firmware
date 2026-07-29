# The mpconfigboard.cmake file is executed first.
# Therefore we do not need to set PICO_BOARD and other things, again.
#
# The EMPTY variant ships MicroPython plus the micropython-lib USB stack, but
# without src/ frozen in — this is what `mpremote mount ./src` develops against.

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest_empty.py)
