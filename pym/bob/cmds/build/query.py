# Bob build tool
# Copyright (C) 2016-2020, The BobBuildTool Contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...builder import LocalBuilder
from ...errors import ParseError
from ...input import RecipeSet
from ..helpers import processDefines
from string import Formatter
import argparse
import os
import sys

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
    parser.add_argument('-q', dest="quiet", action="store_true",
        help="Be quiet in case of errors")
    parser.add_argument('--fail', action="store_true",
        help="Return a non-zero error code in case of errors")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=False,
        help="Enable sandboxing")
    group.add_argument('--slim-sandbox', action='store_false', dest='sandbox',
        help="Enable slim sandboxing")
    group.add_argument('--dev-sandbox', action='store_true', dest='sandbox',
        help="Enable development sandboxing")
    group.add_argument('--strict-sandbox', action='store_true', dest='sandbox',
        help="Enable strict sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
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
    recipes.parse(defines)

    # State variables in a class
    class State:
        def __init__(self):
            self.packageText = ''
            self.showPackage = True
            self.failedSteps = []
        def appendText(self, what):
            self.packageText += what
        def appendStep(self, step):
            dir = step.getWorkspacePath()
            if step.isValid() and (dir is not None) and os.path.isdir(dir):
                self.packageText += dir
            else:
                self.showPackage = False
                self.failedSteps.append(step)
        def print(self):
            if (self.showPackage):
                print(self.packageText)
            else:
                if not args.quiet:
                    packageName = self.failedSteps[0].getPackage().getName()
                    if len(self.failedSteps) == 1:
                        print("Directory for {{{}}} step of package {} not present.".format(
                            self.failedSteps[0].getLabel(), packageName), file=sys.stderr)
                    else:
                        labelList = ', '.join([step.getLabel() for step in self.failedSteps])
                        print("Directories for {{{}}} steps of package {} not present.".format(
                            labelList, packageName), file=sys.stderr)
                if args.fail:
                    sys.exit(1)

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
    packages = recipes.generatePackages(nameFormatter, args.sandbox)
    if args.dev: developPersister.prime(packages)

    matched = False
    # Loop through packages
    for p in args.packages:
        # Format this package.
        # Only show the package if all of the requested directory names are present
        for package in packages.queryPackagePath(p):
            matched = True
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

    if not matched:
        if not args.quiet:
            print("Your query matched no packages. Naptime!", file=sys.stderr)
        if args.fail:
            sys.exit(1)
