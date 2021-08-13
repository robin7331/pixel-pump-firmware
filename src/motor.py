from machine import Pin, PWM
import utime

class Motor:
    def __init__(self, motorPin, freq=10000, timeout=30000, on_timeout=None):
        self.pwm = PWM(Pin(motorPin))
        self.pwm.freq(freq)
        self.pwm_duty = 0
        self.running = False
        self.on_timeout = on_timeout
        self.turned_on_at = 0
        self.timeout = timeout

    def start(self, duty=None):
        if duty:
            self.pwm_duty = duty
        self.running = True
        self.turned_on_at = utime.ticks_ms()

    def stop(self):
        self.running = False

    def set_pwm(self, duty):
        self.pwm_duty = duty

    def tick(self):
        if self.running:
            if utime.ticks_ms() - self.turned_on_at > self.timeout:
                self.stop()
                if self.on_timeout:
                    self.on_timeout(self)
            self.pwm.duty_u16(self.pwm_duty * self.pwm_duty)
        else:
            self.pwm.duty_u16(0)
