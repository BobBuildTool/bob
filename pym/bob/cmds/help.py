# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
    else:
        manPage = "bob" + args.command
        manSection = "7"

    inSourceLoc = os.path.join(bobRoot, "doc", "_build", "man", manPage+"."+manSection)
    try:
        if os.path.isfile(inSourceLoc):
            ret = subprocess.call(["man", inSourceLoc])
        else:
            ret = subprocess.call(["man", manSection, manPage])
    except OSError:
        ret = 1

    sys.exit(ret)
