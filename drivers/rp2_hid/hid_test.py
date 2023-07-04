# RP2 USB HID library test code

import keyboard
import mouse
import consumer_control
import gamepad
import time

def keyboard_test():
    k = keyboard.Keyboard()

    # press and release
    for i in range(10):
        k.press(k.MOD_LEFT_SHIFT, 0x04) # A
        k.release(k.MOD_LEFT_SHIFT, 0x04)
    for i in range(10):
        k.press(k.MOD_LEFT_SHIFT, 0x05, 0x06) # AB
        k.release(k.MOD_LEFT_SHIFT, 0x05, 0x06)

    # press and hold 0.75 sec
    for i in range(1):
        k.press(k.MOD_LEFT_SHIFT, 0x04) # A
        time.sleep(0.75)
        k.release(k.MOD_LEFT_SHIFT)
        k.press(0x05) # b
        time.sleep(0.75)
        k.release_all()
    
    input("press [enter]...")

def mouse_test():
    m = mouse.Mouse()
    # relative position
    for i in range(3):
        for rel in [(200,0), (0,200), (-200,0), (0,-200)]:
            m.move(rel[0], rel[1])
            m.click(m.BUTTON_RIGHT)
            time.sleep(1)
    # absolute position
    steps = 4
    for i in range(0, 32768, int(32768/steps)):
        for j in range(0, 32768, int(32768/steps)):
            m.moveto(i + 2000, j + 2000)
            time.sleep(1)

def consumer_control_test():
    cc = consumer_control.Consumer_Control()
    for i in range(20):
        cc.send(cc.VOLUME_INCREMENT)
        cc.send(cc.VOLUME_DECREMENT)
        cc.send(cc.MUTE) # mute
        cc.send(cc.MUTE) # unmute
        
    for i in range(4):
        cc.send(cc.PLAY_PAUSE)
        time.sleep(3)
        
def gamepad_test():
    gp = gamepad.GamePad()
    for i in range(20):
        gp.set_linear(-50, -50, -50)
        gp.set_rotation(-50, -50, -50)
        gp.set_hat(i % 9)
        gp.set_buttons(0xaaaa)
        gp.send()
        time.sleep(0.2)
        gp.set_linear(100, 100, 100)
        gp.set_rotation(100, 100, 100)
        gp.set_hat(i % 9)
        gp.set_buttons(0x5555)
        gp.send()
        time.sleep(0.2)        

if __name__ == "__main__":

    while True:
        s = input("enter test [k|m|c|g]: ")

        if s in "kmcg":
            for t in range(5, 0, -1):                
                print("starting in {} sec".format(t))
                time.sleep(1)

            if s == "k":
                keyboard_test()
            if s == "m":
                mouse_test()
            if s == "c":
                consumer_control_test()
            if s == "g":
                gamepad_test()
