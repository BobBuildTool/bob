# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BobError
import argparse
import os.path
import subprocess
import sys

def doHelp(availableCommands, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob help",
        description="Display help information about command.")
    # Help without a command parameter gets handled by the main argument parser
    # in pym/bob/scripts.py.
    parser.add_argument('command', help="Command to get help for")

    args = parser.parse_args(argv)

    if args.command in availableCommands:
        manPage = "bob-" + args.command
        manSection = "1"
    elif args.command == "bob":
        manPage = "bob"
        manSection = "1"
    else:
        manPage = "bob" + args.command
        manSection = "7"

    try:
        from ..develop.make import makeManpages
        manPath = makeManpages()
        manArgs = [ os.path.join(manPath, manPage+"."+manSection) ]
    except ImportError:
        manArgs = [manSection, manPage]
    except OSError as e:
        raise BobError("Cannot build manpage: " + str(e))

    try:
        ret = subprocess.call(["man"] + manArgs)
    except OSError:
        ret = 1

    sys.exit(ret)
