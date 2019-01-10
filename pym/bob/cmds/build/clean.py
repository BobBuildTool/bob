# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...input import RecipeSet
from ...state import BobState
from ...utils import removePath, processDefines
import argparse
import os

from .builder import LocalBuilder

def collectPaths(package):
    paths = set()
    checkoutStep = package.getCheckoutStep()
    if checkoutStep.isValid(): paths.add(checkoutStep.getWorkspacePath())
    buildStep = package.getBuildStep()
    if buildStep.isValid(): paths.add(buildStep.getWorkspacePath())
    paths.add(package.getPackageStep().getWorkspacePath())
    for d in package.getDirectDepSteps():
        paths |= collectPaths(d.getPackage())
    return paths

def doClean(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob clean",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Clean unused directories.

This command removes currently unused directories from previous "bob build"
invocations.  By default only 'build' and 'package' steps are evicted. Adding
'-s' will clean 'checkout' steps too. Make sure that you have checked in (and
pushed) all your changes, tough. When in doubt add '--dry-run' to see what
would get removed without actually deleting that already.
""")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-s', '--src', default=False, action='store_true',
        help="Clean source steps too")
    parser.add_argument('-v', '--verbose', default=False, action='store_true',
        help="Print what is done")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    nameFormatter = LocalBuilder.makeRunnable(LocalBuilder.releaseNameInterrogator)

    # collect all used paths (with and without sandboxing)
    usedPaths = set()
    packages = recipes.generatePackages(nameFormatter, defines, sandboxEnabled=True)
    usedPaths |= collectPaths(packages.getRootPackage())
    packages = recipes.generatePackages(nameFormatter, defines, sandboxEnabled=False)
    usedPaths |= collectPaths(packages.getRootPackage())

    # get all known existing paths
    cleanSources = args.src
    allPaths = ( os.path.join(dir, "workspace")
        for (dir, isSourceDir) in BobState().getAllNameDirectores()
        if (not isSourceDir or (isSourceDir and cleanSources)) )
    allPaths = set(d for d in allPaths if os.path.exists(d))

    # delete unused directories
    for d in allPaths - usedPaths:
        if args.verbose or args.dry_run:
            print("rm", d)
        if not args.dry_run:
            removePath(d)

