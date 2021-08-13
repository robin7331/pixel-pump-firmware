#!/usr/bin/python

import glob
from ampy import files
from ampy import pyboard
from ampy import files
from helpers import create_argument_parser
from helpers import choose_port

if __name__ == '__main__':

    parser = create_argument_parser()
    parser.add_argument('-f', '--file', type=str, help='Specify a single file you would like to copy eg. src/main.py')

    args = parser.parse_args()
    port = args.port or choose_port()


    # open the pyboard
    pyb = pyboard.Pyboard(port)

    ampy_files = files.Files(pyb)

    # iterate through all the py files in the src folder
    for src_file in glob.iglob('src/**/*.py', recursive=True):

      if args.file:
        if not args.file == src_file:
          continue      

      print("Copying " + src_file + " to " + src_file[4:])

      # get all the folders in the file path 
      source_folders = src_file[4:].split('/')

      # if folders exist loop through the folders and create them recursively
      if len(source_folders) > 1:
        for folder in source_folders[:-1]:
          print("Making folder " + folder)
          ampy_files.mkdir(folder, exists_okay=True)
      
      #put the file into the newyl created folder
      # read contents of file at the current path
      with open(src_file, 'rb') as f: 
        ampy_files.put(src_file[4:], f.read())

      # soft reboot
      pyb.serial.write(b'\x04')


