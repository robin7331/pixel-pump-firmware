import sys
import glob
import serial
import argparse

def available_serial_ports():
    """ Lists serial port names

        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        ports = glob.glob('/dev/tty[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.*')
    else:
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in ports:
        try:
            s = serial.Serial(port)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result

def create_argument_parser():
  parser = argparse.ArgumentParser(description='Pixel Pump CLI')
  parser.add_argument('-p', '--port', type=str, help='Specify the tty port eg. /dev/tty.usbmodem2101')
  return parser

def choose_port():
    available_ports = available_serial_ports()
    for port in available_ports:
        print(str(available_ports.index(port) + 1) + ': ' + port)
    port_number = int(input('Select a port: '))
    # check if the port_number does not exceed the number of available ports
    if port_number > len(available_ports) or port_number < 1:
        print('Invalid port number. Try again.')
        sys.exit(1)
    return available_ports[port_number - 1]
