import machine
from machine import Pin, PWM, mem32, Timer
from ui_renderer import UIRenderer
from io_event_source import IOEventSource, IOEvent
from button import Button
from valve import Valve
from pixel_pump import PixelPump
from enums.power_mode import PowerMode
from boot_sequence import run_boot_sequence
from motor import Motor
import utime
import keyboard
from communication_manager import CommunicationManager

# Register Base Addresses

SYSCFG_BASE         = 0x40004000
IO_BANK0_BASE       = 0x40014000
PADS_BANK0_BASE     = 0x4001C000
PADS_QSPI_BASE      = 0x40020000
DMA_BASE            = 0x50000000
SIO_BASE            = 0xD0000000

# GPIO control

GPIO_IN             = SIO_BASE + 0x004

GPIO_OUT            = SIO_BASE + 0x010
GPIO_OUT_SET        = SIO_BASE + 0x014
GPIO_OUT_CLR        = SIO_BASE + 0x018
GPIO_OUT_XOR        = SIO_BASE + 0x01C

GPIO_OE             = SIO_BASE + 0x020
GPIO_OE_SET         = SIO_BASE + 0x024
GPIO_OE_CLR         = SIO_BASE + 0x028
GPIO_OE_XOR         = SIO_BASE + 0x02C

# QSPI bus - SD3, SD2, SD1, SD0, SSn, SSCLK (lsb)

QSPI_IN             = SIO_BASE + 0x008

QSPI_OUT            = SIO_BASE + 0x030
QSPI_OUT_SET        = SIO_BASE + 0x034
QSPI_OUT_CLR        = SIO_BASE + 0x038
QSPI_OUT_XOR        = SIO_BASE + 0x03C

QSPI_OE             = SIO_BASE + 0x040
QSPI_OE_SET         = SIO_BASE + 0x044
QSPI_OE_CLR         = SIO_BASE + 0x048
QSPI_OE_XOR         = SIO_BASE + 0x04C

# Processor ID

CPUID               = SIO_BASE + 0x000

# Inter-core FIFO - 8 words deep

FIFO_ST             = SIO_BASE + 0x050 # Status
FIFO_WR             = SIO_BASE + 0x054
FIFO_RD             = SIO_BASE + 0x058

FIFO_ST_ROE_BIT     = 3 # Was read when empty - 1=Yes (sticky)
FIFO_ST_WOF_BIT     = 2 # Was write when full - 1=Yes (sticky)
FIFO_ST_RDY_BIT     = 1 # Can write to fIFO   - 1=Yes
FIFO_ST_VLD_BIT     = 0 # Can read from FIFO  - 1=Yes

# Spin locks - Read to claim (0=fail), Write to release

SPINLOCK            = SIO_BASE + 0x100 # Add (lock * 4)
SPINLOCK_MPY        = 4

SPINLOCK_STATUS     = SIO_BASE + 0x05C # All 32 spin locks as bits

# GPIO configuration

GPIO_CTRL           = IO_BANK0_BASE + 0x04 # Add (pin * 8)
GPIO_CTRL_MPY       = 8

GPIO_IRQOVER_BITS   = 28 # 00-11         Default=0
GPIO_INOVER_BITS    = 16 # 00-11         Default=0
GPIO_OEOVER_BITS    = 12 # 00-11         Default=0
GPIO_OUTOVER_BITS   =  8 # 00-11         Default=0
GPIO_ALT_BITS       =  0 # 00000-11111   Default=31 (11111)

GPIO_ALT_SPI_VAL    = 1
GPIO_ALT_UART_VAL   = 2
GPIO_ALT_I2C_VAL    = 3
GPIO_ALT_PWM_VAL    = 4
GPIO_ALT_SIO_VAL    = 5
GPIO_ALT_PIO0_VAL   = 6
GPIO_ALT_PIO1_VAL   = 7
GPIO_ALT_USB_VAL    = 9
GPIO_ALT_NONE_VAL   = 31

GPIO_SYNC_BYPASS    = SYSCFG_BASE + 0x0C # 1=Bypass input sync for that pin

# Debug configuration

DBGFORCE            = SYSCFG_BASE + 0x14

PROC1_ATTACH_BIT    = 7 # 0=Hardware, 1=Software, Disconnect CPU1 from pads   Default=0
PROC1_SWDCLK_BIT    = 6 # Drive SWCLK input to CPU1                           Default=1
PROC1_SWDI_BIT      = 5 # Drive SWDIO input to CPU1                           Default=1
PROC1_SWDO_BIT      = 4 # Read SWDIO output from CPU1
PROC0_ATTACH_BIT    = 3 # 0=Hardware, 1=Software, Disconnect CPU0 from pads   Default=0
PROC0_SWDCLK_BIT    = 2 # Drive SWCLK input to CPU0                           Default=1
PROC0_SWDI_BIT      = 1 # Drive SWDIO input to CPU0                           Default=1
PROC0_SWDO_BIT      = 0 # Read SWDIO output from CPU0

DBGFORCE_HARDWARE   = 0b_0110_0110
DBGFORCE_SOFTWARE   = 0b_1110_1110

# GPIO Pad Control

PAD_GPIO_VOLTAGE    = PADS_BANK0_BASE + 0x00 # 0 or 1 - covers all pins

PAD_GPIO            = PADS_BANK0_BASE + 0x04 # Add (pin * 4)
PAD_GPIO_MPY        = 4

PAD_GPIO_SWC        = PADS_BANK0_BASE + 0x7C
PAD_GPIO_SWD        = PADS_BANK0_BASE + 0x80

PAD_QSPI_VOLTAGE    = PADS_QSPI_BASE  + 0x00 # 0 or 1 - covers all pins

PAD_QSPI            = PADS_QSPI_BASE  + 0x04 # Add (pin * 4)
PAD_QSPI_MPY        = 4

PAD_QSPI_SCLK       = PADS_QSPI_BASE  + 0x04
PAD_QSPI_SD0        = PADS_QSPI_BASE  + 0x08
PAD_QSPI_SD1        = PADS_QSPI_BASE  + 0x0C
PAD_QSPI_SD2        = PADS_QSPI_BASE  + 0x10
PAD_QSPI_SD3        = PADS_QSPI_BASE  + 0x14
PAD_QSPI_SS         = PADS_QSPI_BASE  + 0x18

PAD_VOLTAGE_BIT     = 0 # 0=3V3, 1=1V8                  Default=0, 3V3

PAD_OD_BIT          = 7 # 0=Enable, 1=Disable           Default=0, Output enabled
PAD_IE_BIT          = 6 # 0=Disable, 1=Enable           Default=1, Input Enabled
PAD_DRIVE_BITS      = 4 # 0=2mA, 1=4mA, 2=8mA, 3=12mA   Default=1, 4mA drive
PAD_PUE_BIT         = 3 # 0=Disable, 1=Enable           Default=0, No pull-up
PAD_PDE_BIT         = 2 # 0=Disable, 1=Enable           Default=1, Pull-down enabled
PAD_SCMITT_BIT      = 1 # 0=Disable, 1=Enable           Default=1, Scmitt enabled
PAD_SLEW_BIT        = 0 # 0=Slow, 1=Fast                Default=0, Slew rate slow
# DMA

DMA_RD_ADDRESS      = DMA_BASE + 0x00 # Add (channel * 0x40)
DMA_WR_ADDRESS      = DMA_BASE + 0x04 # Add (channel * 0x40)
DMA_COUNT           = DMA_BASE + 0x08 # Add (channel * 0x40)
DMA_TRIGGER         = DMA_BASE + 0x0C # Add (channel * 0x40)

DMA_CHAN_MPY        = 0x40

DMA_IRQ_QUIET_BIT   = 21
DMA_TREQ_SEL_BITS   = 15
DMA_CHAIN_TO_BITS   = 11
DMA_RING_SEL_BIT    = 10
DMA_RING_SIZE_BIT   =  9
DMA_INCR_WRITE_BIT  =  5
DMA_INCR_READ_BIT   =  4
DMA_DATA_SIZE_BITS  =  2
DMA_PRIORITY_BIT    =  1
DMA_ENABLE_BIT      =  0

DMA_TREQ_AUTO_VAL   = 0x3F # Immediately run
DMA_TREQ_RX_VAL     = 0x04 # Get from PIO
DMA_TREQ_TX_VAL     = 0x00 # Put to PIO

DMA_TREQ_COPY_VAL   = DMA_TREQ_AUTO_VAL # Immediately run
DMA_TREQ_GET_VAL    = DMA_TREQ_RX_VAL   # Get from PIO
DMA_TREQ_PUT_VAL    = DMA_TREQ_TX_VAL   # Put to PIO

machine.freq(96000000)

foot_aux = Pin(7, Pin.IN, Pin.PULL_DOWN)

motor = Motor(motorPin=5)

# The UI Renderer class holds the frame buffer and the PIO state machine
renderer = UIRenderer()
def SetPadQSPI(pin, d, s):
    adr = PAD_QSPI + PAD_QSPI_MPY * pin
    n = mem32[adr]
    n = n | ( 3 << PAD_DRIVE_BITS ) # Set drive bits high
    n = n ^ ( 3 << PAD_DRIVE_BITS ) # Invert drive bits; set drive bits low
    n = n | ( d << PAD_DRIVE_BITS ) # Add drive bits
    n = n | ( 1 << PAD_SLEW_BIT   ) # Set slew high
    n = n ^ ( 1 << PAD_SLEW_BIT   ) # Invert slew bit; set slew low
    n = n | ( s << PAD_SLEW_BIT   ) # Add slew bit
    mem32[adr] = n

for pin in range(6):
    SetPadQSPI(pin, 0, 0) # Drive = 0 (2mA), Slew = 0 (slow)

def liftBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_lift()


def liftBtnLongPress(btn):
    global pixel_pump
    pixel_pump.state.to_brightness_settings()


def dropBtnTouchUp(btn):
    global pixel_pump
    pixel_pump.state.to_drop()


def reverse_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.to_reverse()


def trigger_buttonTouchDown(btn):
    global pixel_pump
    pixel_pump.state.trigger_on()


def trigger_buttonTouchUp(btn):
    global pixel_pump
    pixel_pump.state.trigger_off()


def on_button_event(btn, event):
    global pixel_pump
    pixel_pump.state.on_button_event(btn, event)

def on_event(source, event):
    global pixel_pump
    # https://deskthority.net/wiki/Scancode for keyboard codes
    if event is IOEvent.TAPPED:
        k.press(pixel_pump.settings_manager.get_secondary_pedal_key_modifier(), pixel_pump.settings_manager.get_secondary_pedal_key())
        k.release(pixel_pump.settings_manager.get_secondary_pedal_key_modifier(), pixel_pump.settings_manager.get_secondary_pedal_key())
    if event is IOEvent.LONG_HOLD:
        k.press(pixel_pump.settings_manager.get_secondary_pedal_long_key_modifier(), pixel_pump.settings_manager.get_secondary_pedal_long_key())
        k.release(pixel_pump.settings_manager.get_secondary_pedal_long_key_modifier(), pixel_pump.settings_manager.get_secondary_pedal_long_key())

def renderBtn(btn):
    global renderer
    renderer.set_led_color(
        btn.left_led_index, (btn.left_color[0], btn.left_color[1], btn.left_color[2]), btn.left_color[3])
    renderer.set_led_color(
        btn.right_led_index, (btn.right_color[0], btn.right_color[1], btn.right_color[2]), btn.right_color[3])


lift_button = Button(title='Lift',
                     left_led_index=0,
                     right_led_index=1,
                     switch_pin=8,
                     on_button_event=on_button_event,
                     on_touch_down=liftBtnTouchUp,
                     on_long_press=liftBtnLongPress,
                     on_should_render=renderBtn)

drop_button = Button(title='Drop',
                     left_led_index=2,
                     right_led_index=3,
                     switch_pin=9,
                     on_button_event=on_button_event,
                     on_touch_down=dropBtnTouchUp,
                     on_should_render=renderBtn)

low_button = Button(title='Low',
                    left_led_index=4,
                    right_led_index=5,
                    switch_pin=11,
                    on_button_event=on_button_event,
                    on_should_render=renderBtn)

high_button = Button(title='High',
                     left_led_index=6,
                     right_led_index=7,
                     switch_pin=10,
                     on_button_event=on_button_event,
                     on_should_render=renderBtn)

reverse_button = Button(title='Reverse',
                        left_led_index=8,
                        right_led_index=9,
                        switch_pin=12,
                        on_button_event=on_button_event,
                        on_touch_down=reverse_buttonTouchDown,
                        on_should_render=renderBtn)

trigger_button = Button(title='Trigger',
                        left_led_index=10,
                        right_led_index=11,
                        switch_pin=13,
                        secondary_switch_pin=6,
                        on_button_event=on_button_event,
                        on_touch_up=trigger_buttonTouchUp,
                        on_touch_down=trigger_buttonTouchDown,
                        on_should_render=renderBtn)

secondary_pedal = IOEventSource(title='Secondary Trigger', pin_number=7, pin_mode=Pin.IN, pin_pull=Pin.PULL_DOWN, on_event=on_event)

no_valve = Valve(2)
nc_valve = Valve(3)
three_way_valve = Valve(4)

pixel_pump = PixelPump(motor=motor,
                       ui_renderer=renderer,
                       lift_button=lift_button,
                       drop_button=drop_button,
                       low_button=low_button,
                       high_button=high_button,
                       reverse_button=reverse_button,
                       trigger_button=trigger_button,
                       nc_valve=nc_valve,
                       no_valve=no_valve,
                       three_way_valve=three_way_valve)

communication_manager = CommunicationManager(pixel_pump)

# Lets render the buttons at 30 fps (just for the boot sequence)
uiTimer = Timer()
uiTimer.init(freq=60, mode=Timer.PERIODIC,
             callback=lambda t: renderer.flush_frame_buffer())

# Lets run a fancy rainbow boot sequence followed by a few relay clicks because we can
run_boot_sequence(renderer, [no_valve, nc_valve, three_way_valve])

uiTimer.deinit()

rendered_at = 0

k = keyboard.Keyboard()

while True:
    lift_button.tick()
    drop_button.tick()
    low_button.tick()
    high_button.tick()
    reverse_button.tick()
    trigger_button.tick()

    secondary_pedal.tick()

    no_valve.tick()
    nc_valve.tick()
    three_way_valve.tick()

    motor.tick()

    pixel_pump.tick()

    communication_manager.tick()

    # Render the UI at 30 FPS.
    if utime.ticks_ms() - rendered_at > 33:
        renderer.flush_frame_buffer()
        rendered_at = utime.ticks_ms()
