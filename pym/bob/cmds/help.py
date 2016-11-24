# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import os.path
import subprocess
import sys

def doHelp(availableCommands, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob help",
        description="Display help information about command.")
    parser.add_argument('command', help="Command to get help for")

    args = parser.parse_args(argv)

    if args.command in availableCommands:
        manPage = "bob-" + args.command
    else:
        manPage = "bob" + args.command

    inSourceLoc = os.path.join(bobRoot, "doc", "_build", "man", manPage+".1")
    if os.path.isfile(inSourceLoc):
        ret = subprocess.call(["man", inSourceLoc])
    else:
        ret = subprocess.call(["man", manPage])

    sys.exit(ret)
