import ujson

DEFAULT_SETTINGS = {
    "brightness": 1.0,
    "low_power_setting": 80,
    "high_power_setting": 100,
    "power_mode": 1,
    "mode": 0,
    "secondary_pedal_key": 0x11,
    "secondary_pedal_key_modifier": 0x00,
    "secondary_pedal_long_key": 0x52,
    "secondary_pedal_long_key_modifier": 0x00,
}


class SettingsManager:
    def __init__(self, file_name="settings.json"):
        self.file_name = file_name
        self.settings = None
        self.initialize()
        self.migrate_settings()

    def initialize(self):
        try:
            with open(self.file_name, "r") as file:
                self.settings = ujson.load(file)
        except OSError:  # open failed. Lets create one
            with open(self.file_name, "w") as file:
                self.settings = DEFAULT_SETTINGS
                ujson.dump(self.settings, file)

    def migrate_settings(self):
        # add new settings
        for key in DEFAULT_SETTINGS:
            if key not in self.settings:
                self.settings[key] = DEFAULT_SETTINGS[key]
        
        # remove obsolete settings
        for key in self.settings:
            if key not in DEFAULT_SETTINGS:
                del self.settings[key]
            
        self.persist_settings()


    def read_all_settings(self):
        return ujson.dumps(self.settings)

    def write_all_settings(self, settings):
        self.settings = ujson.loads(settings)
        self.persist_settings()

    def reset_settings(self):
        self.settings = {}
        self.persist_settings()

    def persist_settings(self):
        try:
            with open(self.file_name, "w") as file:
                ujson.dump(self.settings, file)
        except OSError:  # open failed. Lets create one
            print("error writing the settings")

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
        return self.get_property("brightness", default=1.0)

    def set_brightness(self, brightness, persist=True):
        if brightness > 0.8:
            brightness = 0.8
        if brightness < 0.35:
            brightness = 0.35
        self.set_property("brightness", brightness, persist)

    def get_low_power_setting(self):
        return self.get_property("low_power_setting", default=80)

    def set_low_power_setting(self, power, persist=True):
        if power > 100:
            power = 100
        if power < 0:
            power = 0
        self.set_property("low_power_setting", power, persist)

    def get_high_power_setting(self):
        return self.get_property("high_power_setting", default=100)

    def set_high_power_setting(self, duty, persist=True):
        if duty > 100:
            duty = 100
        if duty < 0:
            duty = 0
        self.set_property("high_power_setting", duty, persist)

    def get_power_mode(self):
        return self.get_property("power_mode", default=1)

    def set_power_mode(self, power_mode, persist=True):
        self.set_property("power_mode", power_mode, persist)

    def get_mode(self):
        return self.get_property("mode", default=0)

    def set_mode(self, mode, persist=True):
        self.set_property("mode", mode, persist)

    # add methods to set and get the secondary_pedal_key which is a hex code as 0x11
    def set_secondary_pedal_key(self, key, persist=True):
        self.set_property("secondary_pedal_key", key, persist)

    def get_secondary_pedal_key(self):
        return self.get_property("secondary_pedal_key", default=0x11)

    def set_secondary_pedal_key_modifier(self, key, persist=True):
        self.set_property("secondary_pedal_key_modifier", key, persist)

    def get_secondary_pedal_key_modifier(self):
        return self.get_property("secondary_pedal_key_modifier", default=0x00)

    def set_secondary_pedal_long_key(self, key, persist=True):
        self.set_property("secondary_pedal_long_key", key, persist)

    def get_secondary_pedal_long_key(self):
        return self.get_property("secondary_pedal_long_key", default=0x52)

    def set_secondary_pedal_long_key_modifier(self, key, persist=True):
        self.set_property("secondary_pedal_long_key_modifier", key, persist)

    def get_secondary_pedal_long_key_modifier(self):
        return self.get_property("secondary_pedal_long_key_modifier", default=0x00)
