from machine import Pin, PWM


class Motor:
    def __init__(self, motorPin, freq=10000):
        self.pwm = PWM(Pin(motorPin))
        self.pwm.freq(freq)
        self.pwm_duty = 0
        self.running = False

    def start(self, duty=None):
        if duty:
            self.pwm_duty = duty
        self.running = True

    def stop(self):
        self.running = False

    def set_pwm(self, duty):
        self.pwm_duty = duty

    def tick(self):
        if self.running:
            self.pwm.duty_u16(self.pwm_duty * self.pwm_duty)
        else:
            self.pwm.duty_u16(0)
