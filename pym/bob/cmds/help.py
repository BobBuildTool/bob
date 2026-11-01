# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BobError
from ..input import RecipeSet
import argparse
import os.path
import shutil
import subprocess
import sys

def showManPage(manPage, manSection):
    try:
        from ..develop import make
    except ImportError:
        make = None

    if sys.platform != "win32" and shutil.which("man"):
        try:
            if make is not None:
                manPath = make.makeManpages()
                manArgs = [ os.path.join(manPath, manPage+"."+manSection) ]
            else:
                manArgs = [manSection, manPage]
        except OSError as e:
            raise BobError("Cannot build manpage: " + str(e))

        try:
            return subprocess.call(["man"] + manArgs)
        except OSError:
            return 1

    # No "man" available. Fall back to the plain text version of the manpage.
    try:
        if make is not None:
            textPath = make.makeHelpText()
        else:
            textPath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_man")
    except OSError as e:
        raise BobError("Cannot build help page: " + str(e))

    try:
        with open(os.path.join(textPath, manPage+".txt"), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise BobError("No help page available for '{}'".format(manPage))
    except OSError as e:
        raise BobError("Cannot read help page: " + str(e))

    import pydoc
    pydoc.pager(text)
    return 0

def doHelp(availableCommands, argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob help",
        description="Display help information about command.")
    # Help without a command parameter gets handled by the main argument parser
    # in pym/bob/scripts.py.
    parser.add_argument('command', nargs='?', help="Command to get help for")
    parser.add_argument('-a', '--all', action='store_true',
                        help="print all available commands")

    args = parser.parse_args(argv)

    if args.command is None:
        lines = ["The following high level commands are available:", ""]
        lines.extend(sorted([ "  {:16s}{}".format(k, v[2])
                            for (k,v) in availableCommands.items() if v[0] == 'hl' ]))
        lines.extend(["", "The following scripting commands are available:", ""])
        lines.extend(sorted([ "  {:16s}{}".format(k, v[2])
                            for (k,v) in availableCommands.items() if v[0] == 'll' ]))
        if args.all:
            lines.extend(["", "The following plugin defined commands are available:", ""])

            recipes = RecipeSet()
            recipes.parseConfigs(requireRecipes=False)
            for name, spec in sorted(recipes.getCommands().items()):
                lines.append("  {:16s}{}".format(name, spec.get("help", "")))

        print("\n".join(lines))
        return 0

    if args.command in availableCommands.keys():
        manPage = "bob-" + args.command
        manSection = "1"
    elif args.command == "bob":
        manPage = "bob"
        manSection = "1"
    else:
        recipes = RecipeSet()
        recipes.parseConfigs(requireRecipes=False)
        if args.command in recipes.getCommands().keys():
            h = recipes.getCommands()[args.command].get("help", "")
            print(f"'{args.command}' is provided by a plugin: {h}")
            return 0

        manPage = "bob" + args.command
        manSection = "7"

    sys.exit(showManPage(manPage, manSection))
