# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BobError
from ..input import RecipeSet
import argparse
import os.path
import subprocess
import sys

def doHelp(availableCommands, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob help",
        description="Display help information about command.")
    # Help without a command parameter gets handled by the main argument parser
    # in pym/bob/scripts.py.
    parser.add_argument('command', nargs='?', help="Command to get help for")
    parser.add_argument('-a', '--all', action='store_true',
                        help="print all available commands")

    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.parseConfigs()

    if args.command is None:
        lines = ["The following high level commands are available:", ""]
        lines.extend(sorted([ "  {:16s}{}".format(k, v[2])
                            for (k,v) in availableCommands.items() if v[0] == 'hl' ]))
        lines.extend(["", "The following scripting commands are available:", ""])
        lines.extend(sorted([ "  {:16s}{}".format(k, v[2])
                            for (k,v) in availableCommands.items() if v[0] == 'll' ]))
        if args.all:
            lines.extend(["", "The following plugin defined commands are available:", ""])

            for name, spec in sorted(recipes.getCommands().items()):
                lines.append("  {:16s}{}".format(name, spec.get("help", "")))

        print("\n".join(lines))
        return 0

    if args.command in recipes.getCommands().keys():
        h = recipes.getCommands()[args.command].get("help", "")
        print(f"'{args.command}' is provided by a plugin: {h}")
        return 0

    if args.command in availableCommands.keys():
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
