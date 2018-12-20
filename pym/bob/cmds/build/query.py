# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...errors import ParseError
from ...input import RecipeSet
from ...utils import processDefines
from string import Formatter
import argparse
import os

from .builder import LocalBuilder
from .state import DevelopDirOracle

def doQueryPath(argv, bobRoot):
    # Configure the parser
    parser = argparse.ArgumentParser(prog="bob query-path",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""Query path information.

This command lists existing workspace directory names for packages given
on the command line. Output is formatted with a format string that can
contain placeholders
   {name}     package name
   {src}      checkout directory
   {build}    build directory
   {dist}     package directory
The default format is '{name}<tab>{dist}'.

If a directory does not exist for a step (because that step has never
been executed or does not exist), the line is omitted.
""")
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to query")
    parser.add_argument('-f', help='Output format string', default='{name}\t{dist}', metavar='FORMAT')
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox', help="Disable sandboxing")
    parser.set_defaults(sandbox=None)

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_true',  dest='dev', help="Use developer mode", default=True)
    group.add_argument('--release', action='store_false', dest='dev', help="Use release mode")

    # Parse args
    args = parser.parse_args(argv)
    if args.sandbox == None:
        args.sandbox = not args.dev

    defines = processDefines(args.defines)

    # Process the recipes
    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', None)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    # State variables in a class
    class State:
        def __init__(self):
            self.packageText = ''
            self.showPackage = True
        def appendText(self, what):
            self.packageText += what
        def appendStep(self, step):
            dir = step.getWorkspacePath()
            if step.isValid() and (dir is not None) and os.path.isdir(dir):
                self.packageText += dir
            else:
                self.showPackage = False
        def print(self):
            if (self.showPackage):
                print(self.packageText)

    if args.dev:
        # Develop names are stable. All we need to do is to replicate build's algorithm,
        # and when we produce a name, check whether it exists.
        nameFormatter = recipes.getHook('developNameFormatter')
        developPersister = DevelopDirOracle(nameFormatter, recipes.getHook('developNamePersister'))
        nameFormatter = developPersister.getFormatter()
    else:
        # Release names are taken from persistence.
        nameFormatter = LocalBuilder.releaseNameInterrogator
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)

    # Find roots
    packages = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if args.dev: developPersister.prime(packages)

    # Loop through packages
    for p in args.packages:
        # Format this package.
        # Only show the package if all of the requested directory names are present
        for package in packages.queryPackagePath(p):
            state = State()
            for (text, var, spec, conversion) in Formatter().parse(args.f):
                state.appendText(text)
                if var is None:
                    pass
                elif var == 'name':
                    state.appendText("/".join(package.getStack()))
                elif var == 'src':
                    state.appendStep(package.getCheckoutStep())
                elif var == 'build':
                    state.appendStep(package.getBuildStep())
                elif var == 'dist':
                    state.appendStep(package.getPackageStep())
                else:
                    raise ParseError("Unknown field '{" + var + "}'")

            # Show
            state.print()
