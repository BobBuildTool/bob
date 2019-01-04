# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...input import RecipeSet
from ...state import BobState
from ...tty import colorize
from ...utils import processDefines
import argparse
import os

from .builder import LocalBuilder
from .state import DevelopDirOracle

def doStatus(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob status", description='Show SCM status')
    parser.add_argument('packages', nargs='+', help="(Sub-)packages")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_true',  dest='develop', help="Use developer mode", default=True)
    group.add_argument('--release', action='store_false', dest='develop', help="Use release mode")

    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-v', '--verbose', default=1, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('--show-overrides', default=False, action='store_true', dest='show_overrides',
        help="Show scm override status")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', None)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    if args.develop:
        # Develop names are stable. All we need to do is to replicate build's algorithm,
        # and when we produce a name, check whether it exists.
        nameFormatter = recipes.getHook('developNameFormatter')
        developPersister = DevelopDirOracle(nameFormatter, recipes.getHook('developNamePersister'))
        nameFormatter = developPersister.getFormatter()
    else:
        # Release names are taken from persistence.
        nameFormatter = LocalBuilder.releaseNameInterrogator
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)

    packages = recipes.generatePackages(nameFormatter, defines, not args.develop)
    if args.develop: developPersister.prime(packages)

    def showStatus(package, recurse, verbose, done, donePackage):
        if package._getId() in donePackages:
            return
        donePackages.add(package._getId())
        checkoutStep = package.getCheckoutStep()
        if checkoutStep.isValid() and (not checkoutStep.getVariantId() in done):
            done.add(checkoutStep.getVariantId())
            print(">>", colorize("/".join(package.getStack()), "32;1"))
            if checkoutStep.getWorkspacePath() is not None:
                oldCheckoutState = BobState().getDirectoryState(checkoutStep.getWorkspacePath(), True)
                if not os.path.isdir(checkoutStep.getWorkspacePath()):
                    oldCheckoutState = {}
                else:
                    oldCheckoutState = { k : v[0] for k,v in oldCheckoutState.items() }
                checkoutState = { k : v[0] for k,v in checkoutStep.getScmDirectories().items() }
                stats = {}
                for scm in checkoutStep.getScmList():
                    stats[scm.getDirectory()] = scm
                for (scmDir, scmDigest) in sorted(oldCheckoutState.copy().items(), key=lambda a:'' if a[0] is None else a[0]):
                    if scmDir is None: continue
                    if scmDigest != checkoutState.get(scmDir):
                        print(colorize("   STATUS {0: <4} {1}".format("A", os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "33"))
                        continue
                    status, shortStatus, longStatus = stats[scmDir].status(checkoutStep.getWorkspacePath())
                    if (status == 'clean') or (status == 'empty'):
                        if (verbose >= 3):
                            print(colorize("   STATUS      {0}".format(os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "32"))
                    elif (status == 'dirty'):
                        print(colorize("   STATUS {0: <4} {1}".format(shortStatus, os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "33"))
                        if (verbose >= 2) and (longStatus != ""):
                            for line in longStatus.splitlines():
                                print('   ' + line)
                    if args.show_overrides:
                        overridden, shortStatus, longStatus = stats[scmDir].statusOverrides(checkoutStep.getWorkspacePath())
                        if overridden:
                            print(colorize("   STATUS {0: <4} {1}".format(shortStatus, os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "32"))
                            if (verbose >= 2) and (longStatus != ""):
                                for line in longStatus.splitlines():
                                    print('   ' + line)

        if recurse:
            for d in package.getDirectDepSteps():
                showStatus(d.getPackage(), recurse, verbose, done, donePackages)

    done = set()
    donePackages = set()
    for p in args.packages:
        for package in packages.queryPackagePath(p):
            showStatus(package, args.recursive, args.verbose, done, donePackages)

