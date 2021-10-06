import ujson


class SettingsManager:
    def __init__(self, file_name='settings.json'):
        self.file_name = file_name
        self.settings = None
        self.initialize()

    def initialize(self):
        try:
            with open(self.file_name, "r") as file:
                self.settings = ujson.load(file)
        except OSError:  # open failed. Lets create one
            with open(self.file_name, 'w') as file:
                self.settings = {}
                ujson.dump(self.settings, file)

    def persist_settings(self):
        try:
            with open(self.file_name, 'w') as file:
                ujson.dump(self.settings, file)
        except OSError:  # open failed. Lets create one
            print('error writing the settings')

    def set_property(self, property, value, persist):
        if property not in self.settings:
            self.settings[property] = value
            if persist:
                self.persist_settings()
        elif self.settings[property] != value:
            self.settings[property] = value
            if persist:
                self.persist_settings()

    def get_property(self, property, default=None):
        if property not in self.settings:
            self.settings[property] = default
            self.persist_settings()

        return self.settings[property]

    def get_brightness(self):
        return self.get_property('brightness', default=1.0)

    def set_brightness(self, brightness, persist=True):
        if brightness > 0.8:
            brightness = 0.8
        if brightness < 0.35:
            brightness = 0.35
        self.set_property('brightness', brightness, persist)

    def get_low_pwm_duty(self):
        return self.get_property('low_pwm_duty', default=200)

    def set_low_pwm_duty(self, duty, persist=True):
        if duty > 255:
            duty = 255
        if duty < 0:
            duty = 0
        self.set_property('low_pwm_duty', duty, persist)

    def get_high_pwm_duty(self):
        return self.get_property('high_pwm_duty', default=255)

    def set_high_pwm_duty(self, duty, persist=True):
        if duty > 255:
            duty = 255
        if duty < 0:
            duty = 0
        self.set_property('high_pwm_duty', duty, persist)

    def get_power_mode(self):
        return self.get_property('power_mode', default=1)

    def set_power_mode(self, power_mode, persist=True):
        self.set_property('power_mode', power_mode, persist)

    def get_mode(self):
        return self.get_property('mode', default=0)

    def set_mode(self, mode, persist=True):
        self.set_property('mode', mode, persist)
