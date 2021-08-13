#!/usr/bin/python

from ampy import files
from ampy import pyboard
from ampy import files
from helpers import create_argument_parser
from helpers import choose_port

if __name__ == '__main__':

    parser = create_argument_parser()
    args = parser.parse_args()
    port = args.port or choose_port()

    # open the pyboard
    pyb = pyboard.Pyboard(port)

    # list the files
    ampy_files = files.Files(pyb)
    files = ampy_files.ls(long_format=False, recursive=True)

    # print the files
    for file in files:
          print(file)



