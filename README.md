# How to Build

## Easy: Using GitHub actions locally
### Install [nektos/act](https://github.com/nektos/act) to run GitHub actions locally:   
   
**on macOS via homebrew**  
```
 brew install act
```

Act is using docker under the hood. Make sure its up and running on your machine.   
[How to install docker](https://www.docker.com/get-started)


### Run the dev or release build with Act
```
act -j dev-build -b ./build
act -j release-build -b ./build
```
Thats it. This may take a while but you should now have a **firmware.uf2** and **firmware-blank.uf2** in your build directory.

# Hacking around

With MicroPython you generally have two main ways of running your code on a device after installing MicroPython.
1. You send your python files to the board via tools like Ampy, Thonny, VSCode with Extensions, ...
2. You have your python files **frozen** into MicroPython

Every Pixel Pump build comes with two generated UF2 firmware files.

```bash
firmware.uf2 
# This is the file you yould flash for a regular usage of the pump. 
# It containes the compiled MicroPython library with the Pixel Pump firmware frozen into it.
# This firmware will not run a main.py that you send via Thonny 
# but instead use the one that was frozen into the build.

firmware-blank.uf2 
# This contains only MicroPython without the actual Pixel Pump firmware frozen into it.
# When you flash this firmware you can freely send any main.py file the regular way and have it executed upon boot.
# This is especially helpful for developing or hacking around. 
```

When you want to develop locally you might want to flash the `firmware-blank.uf2` firmware.   
Now you can use the python scripts found in the `tools/` directory of this repo to manage the python files on the Pixel Pump.

### Install Ampy

The tools currently require [adafruit's ampy]([ampy](https://learn.adafruit.com/micropython-basics-load-files-and-run-code/install-ampy)) to be installed globally on your machine.
```bash
pip3 install adafruit-ampy
```

## List all the files on the Pixel Pump
When connected via USB-C you can run the `list_files.py` script. This should ask you for the device you would like to connect to.
```bash
python3 tools/list_files.py

1: /dev/tty.debug-console
2: /dev/tty.Bluetooth-Incoming-Port
3: /dev/tty.usbmodem2201 # <- Pixel Pump

Select a port: 3
```

All files in the tools directory will prompt for a device to connect. This might be useful for the first time you use a tool. Once you know your port you can simply hand it over to all of the tools as a parameter.

```bash
python3 tools/list_files.py -p /dev/tty.usbmodem2201
```

It will then search for all files on your pump and list them to the console.

```bash
python3 tools/list_files.py -p /dev/tty.usbmodem2201

/settings.json
```

With an empty pump you might only get a settings.json file that is generated automatically.

## Copy all the files to the Pixel Pump

Initially you want a fresh copy of all the firmware python files on your pump.
For this you can use the `copy_files.py` tool like so:

```bash
python3 tools/copy_files.py -p /dev/tty.usbmodem2201
```

This will go through the entire `./src/` folder and copy those files recursively to the pump.   
After the command has finished your files on the pump should look something like this:

```bash
python3 tools/list_files.py -p /dev/tty.usbmodem2201

/boot_sequence.py
/button.py
/io_event_source.py
/main.py
/motor.py
/pixel_pump.py
/settings.json
/settings_manager.py
/ui_renderer.py
/valve.py
```
The pump should now automatically power cycle and function just as if you would have flashed the `firmware.uf2` containing the frozen files.


## Copy a single file to the Pixel Pump

When developing you obviously do not want to copy all the files all the time but only the one that has actually changed. You can pass a single file as a parameter to the `copy_files.py` tool.

```bash
python3 tools/copy_files.py -p /dev/tty.usbmodem2201 -f src/main.py

Copying src/main.py to main.py
```

After that the pump should automatically do a power cycle.

## Cleaning up and removing files

Sometimes you might wanna remove files. Use the `remove_files.py` tool for that.
When executing you should see something like this:

```bash
python3 tools/remove_files.py -p /dev/tty.usbmodem2201

Deleting all files except the settings.json
Removing file /boot_sequence.py
Removing file /button.py
Removing file /io_event_source.py
Removing file /main.py
Removing file /motor.py
Removing file /pixel_pump.py
Removing file /settings_manager.py
Removing file /ui_renderer.py
Removing file /valve.py
Done
```

Note that it said it will not remove the `settings.json` file by default.
If you would also like to remove that add the `--remove-settings` parameter like so:

```bash
python3 tools/remove_files.py -p /dev/tty.usbmodem2201 --remove-settings

Deleting all files including the settings.json
Removing file /settings.json
Done
```

# A typical dev workflow

Lets asume we want to test how it would be to render the buttons at 5 instead of 30 FPS.   
For that we need to increas the delay between rendering frames in the main look in the `main.py` file at the very bottom.

So first of all we clone this repo to our local machine.    
If you want to contribute to the project you might want to fork the project and clone it from your own repo instead.    
And later bring your changes in with a Pull Request.

```bash
git clone git@github.com:robin7331/pixel-pump-firmware.git
```

Then we download the latest development [firmware-blank.uf2](https://github.com/robin7331/pixel-pump-firmware/releases/download/latest/firmware-blank.uf2) and flash it to the pump.

## Enter the bootloader

There are two ways to enter the bootloader. With a working and running pump you can **long press** the **Lift button** to get into lift settings mode. Then simply long press **Drop** to enter the bootloader. Once in here you need to powercycle the pump to get back to normal operation.   

If you have a pump that is not working properly or does not have any firmware files loaded onto it you can hold the bootloader switch while powering on the pump. When viewing the pump from the front you can see two small holes on the left side. The one towards the back of the pump is exposing the bootloader switch. Take a small tool like a nozzle that came with the pump and reach into the hole until you feel the tactile button click. Hold it and power on the pump. You are now in bootloader mode as well. 
   
When in bootloader the pump should show up as a mass storage device on your desktop.   
Now simply copy the UF2 file to that device and wait for a powercycle.   
Once the pump has restarted it should not be enumerated as a mass storage device anymore because it has left the bootloader.   
   
It is now ready for development.

To confirm lets list all the files on the pump:

```bash
python3 tools/list_files.py -p /dev/tty.usbmodem2201

/settings.json
```

If you see more than a settings.json you might want to remove old files before you start fresh.
To do that use the `remove_files.py` tool.

```bash
python3 tools/remove_files.py -p /dev/tty.usbmodem2201
```

Now lets copy a fresh set of firmware files to the pump to have an excellent starting point.

```bash
python3 tools/copy_files.py -p /dev/tty.usbmodem2201

Copying src/boot_sequence.py to boot_sequence.py
Copying src/ui_renderer.py to ui_renderer.py
Copying src/motor.py to motor.py
Copying src/button.py to button.py
Copying src/io_event_source.py to io_event_source.py
Copying src/settings_manager.py to settings_manager.py
Copying src/main.py to main.py
Copying src/valve.py to valve.py
Copying src/pixel_pump.py to pixel_pump.py
```

A power cycle should accure and the pump should be working as expected. We are now ready for development.   
Our goal was to reduce the FPS of the buttons.   
Lets go into the main.py file and change the ms delay from **33** to **200**
```python
    # ...
    # main.py at the very bottom

    # Render the UI at 200 FPS.
    if utime.ticks_ms() - rendered_at > 200:
        renderer.flush_frame_buffer()
        rendered_at = utime.ticks_ms()
```

Now we can copy our modified main.py to the pump

```bash
python3 tools/copy_files.py -p /dev/tty.usbmodem2201 -f src/main.py
```

After a (higher FPS) boot sequence we should see our buttons flash at a slow rate of 5 frames per second :)   
Congratulations, you have made you very first Pixel Pump Hack 😈

## Whats next?
You can flash the latest `firmware.uf2` anytime you like to come back to a working state of the pump.   
You find the firmware files in the [release section of this repo](https://github.com/robin7331/pixel-pump-firmware/releases).
   
If you intend to add a cool feature to the project you might wanna fork this repo and write/flash code the way you have learned in this readme. Once happy feel free to contribute with a pull request.

Happy hacking!



