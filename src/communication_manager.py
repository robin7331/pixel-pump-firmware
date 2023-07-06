import version
import select
import sys
from enums.power_mode import PowerMode
import machine
import sys

class CommunicationManager:
    def __init__(self, pixel_pump):
        self.pixel_pump = pixel_pump
        self.settings_manager = self.pixel_pump.settings_manager

        # setup poll to read USB port
        self.poll_object = select.poll()
        self.poll_object.register(sys.stdin, 1)

    def parse(self, line):
        command = line.split(":")[0]
        arguments = line.split(":")[1:]

        if command is "version":
            self.parse_version_cmd(arguments)
            return
        
        if command is "reset":
            self.parse_reset_cmd(arguments)
            return
        
        if command is "settings":
            self.parse_settings_cmd(arguments)
            return
        
        print("Unknown command '" + command + "'")
    
    def parse_version_cmd(self, arguments):
        if not self.check_has_argument(arguments, 0):
                return
        
        arguments = arguments[0]
        if arguments == "info":
          print(version.tag + "," + version.branch + "," + version.commit_hash + "," + version.timestamp)

    def parse_reset_cmd(self, arguments):
        if not self.check_has_argument(arguments, 0):
                return
        arguments = arguments[0]
        if arguments == "soft":
          sys.exit()

        if arguments == "hard":
          machine.reset()


    def parse_settings_cmd(self, arguments):
        if not self.check_has_argument(arguments, 0):
                return
        cmd = arguments[0]
        if cmd == "dump":
            print(self.settings_manager.read_all_settings())
            return
        
        if cmd == "persist":
            if not self.check_has_argument(arguments, 1):
                return
            
            # join all arguments using ':' as separator
            jsonString = ':'.join(arguments[1:])
            
            try:
                self.settings_manager.write_all_settings(jsonString)
                machine.reset()
            except ValueError:
                print("Invalid JSON")

            return
        
        if cmd == "reset":
            self.settings_manager.reset_settings()
            machine.reset()
        
        if cmd == "set_brightness":
            if not self.check_valid_float_argument(arguments, 1):
                return
            
            self.settings_manager.set_brightness(float(arguments[1]))
            self.pixel_pump.ui_renderer.brightness_modifier = self.settings_manager.get_brightness()
            return
        
        if cmd == "set_mode":
            if not self.check_has_argument(arguments, 1):
                return
            
            target_mode = arguments[1]
            if target_mode == "lift":
                self.pixel_pump.state.to_lift()
                return
            if target_mode == "drop":
                self.pixel_pump.state.to_drop(autorun=False)
                return
            if target_mode == "reverse":
                self.pixel_pump.state.to_reverse()
                return
            
            return
        
        if cmd == "set_power_mode":
            if not self.check_has_argument(arguments, 1):
                return
            
            target_mode = arguments[1]
            if target_mode == "high":
                self.pixel_pump.set_power_mode(PowerMode.HIGH)
                return
            if target_mode == "low":
                self.pixel_pump.set_power_mode(PowerMode.LOW)
                return
                        
            return
        
        if cmd == "set_low_power_setting":
            if not self.check_valid_int_argument(arguments, 1):
                return
            
            percentage = int(arguments[1])
            if percentage < 0:
                percentage = 0
            if percentage > 100:
                percentage = 100

            self.pixel_pump.settings_manager.set_low_pwm_duty(int(percentage * 2.55))
            self.pixel_pump.low_duty = self.settings_manager.get_low_pwm_duty()                
            return
    
        if cmd == "set_high_power_setting":
            if not self.check_valid_int_argument(arguments, 1):
                return
            
            percentage = int(arguments[1])
            if percentage < 0:
                percentage = 0
            if percentage > 100:
                percentage = 100

            self.pixel_pump.settings_manager.set_high_pwm_duty(int(percentage * 2.55))
            self.pixel_pump.high_duty = self.settings_manager.get_high_pwm_duty()              
            return
        
        if cmd == "set_secondary_pedal_key":
            if not self.check_valid_hex_argument(arguments, 1):
                return
            # the core is sent as hex
            code = int(arguments[1], 16)
            self.pixel_pump.settings_manager.set_secondary_pedal_key(code)      
            return
        
        if cmd == "set_secondary_pedal_key_modifier":
            if not self.check_valid_hex_argument(arguments, 1):
                return
            # the core is sent as hex
            code = int(arguments[1], 16)
            self.pixel_pump.settings_manager.set_secondary_pedal_key_modifier(code)      
            return
        
        if cmd == "set_secondary_pedal_long_key":
            if not self.check_valid_hex_argument(arguments, 1):
                return
            # the core is sent as hex
            code = int(arguments[1], 16)
            self.pixel_pump.settings_manager.set_secondary_pedal_long_key(code)      
            return
        
        if cmd == "set_secondary_pedal_long_key_modifier":
            if not self.check_valid_hex_argument(arguments, 1):
                return
            # the core is sent as hex
            code = int(arguments[1], 16)
            self.pixel_pump.settings_manager.set_secondary_pedal_long_key_modifier(code)      
            return
    

    def check_has_argument(self, arguments, index): 
        try:
            arguments[index]
            return True
        except IndexError:
            print("Missing argument")
            return False

    def check_valid_float_argument(self, arguments, index):
        if len(arguments) < index:
            print("Missing argument")
            return False
        try:
            float(arguments[index])
            return True
        except ValueError:
            print("Invalid argument")
            return False
        
    def check_valid_int_argument(self, arguments, index):
        if len(arguments) < index:
            print("Missing argument")
            return False
        try:
            int(arguments[index])
            return True
        except ValueError:
            print("Invalid argument")
            return False
        
    def check_valid_hex_argument(self, arguments, index):
        if len(arguments) < index:
            print("Missing argument")
            return False
        try:
            int(arguments[index], 16)
            return True
        except ValueError:
            print("Invalid argument")
            return False

    def tick(self):
        # check if there is data on the USB port
        if self.poll_object.poll(0):
            line = sys.stdin.readline()
            self.parse(line.strip())
