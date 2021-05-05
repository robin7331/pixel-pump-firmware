import utime


def wheel(pos):
    if pos < 0 or pos > 255:
        return (0, 0, 0)
    if pos < 85:
        return (255 - pos * 3, pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (0, 255 - pos * 3, pos * 3)
    pos -= 170
    return (pos * 3, 0, 255 - pos * 3)


def run_boot_sequence(renderer, valves):
    for i in range(255):
        brightness = (i / 255) * 0.3
        for j in range(12):
            rc_index = (j * 256 // 12) + i
            renderer.set_led_color(j, wheel(rc_index & 255), brightness)
            utime.sleep_us(100)
    for i in range(255):
        brightness = 0.3 - ((i / 255) * 0.3)
        for j in range(12):
            rc_index = (j * 256 // 12) + i
            renderer.set_led_color(j, wheel(rc_index & 255), brightness)
            utime.sleep_us(100)

    for i in range(len(valves)):
      valves[i].activate()
      utime.sleep_ms(120)


    for i in range(len(valves)):
      utime.sleep_ms(80)
      valves[i].deactivate()
