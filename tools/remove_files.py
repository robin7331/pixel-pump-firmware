#!/usr/bin/python


from ampy import files
from ampy import pyboard
from ampy import files
from helpers import create_argument_parser
from helpers import choose_port

def remove_path_component(files, path):
  # split path into a list of components
  path_components = file.split('/')

  # delete the path if it has more than one component
  if len(path_components) > 2:
    path = '/'.join(path_components[:-1])
    print("Removing folder " + path)
    files.rmdir(path)
  else:
    print("Removing file " + path)
    files.rm(path)

if __name__ == '__main__':

    parser = create_argument_parser()

    parser.add_argument(
        '--remove-settings',
        action='store_true',
        help='Also delete the settings.json if this argument is present.'
    )

    args = parser.parse_args()

    port = args.port or choose_port()

    # open the pyboard
    pyb = pyboard.Pyboard(port)

    # list the files
    ampy_files = files.Files(pyb)
    files = ampy_files.ls(long_format=False, recursive=True)

    if args.remove_settings:
      print("Deleting all files including the settings.json")
    else:
      print("Deleting all files except the settings.json")

    for file in files:
        if args.remove_settings:
          remove_path_component(ampy_files, file)
        elif 'settings.json' not in file:
          remove_path_component(ampy_files, file)

    print("Done")



