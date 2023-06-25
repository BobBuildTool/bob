# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...builder import LocalBuilder, checkoutsFromState
from ...input import RecipeSet
from ...scm import getScm, ScmTaint, ScmStatus
from ...share import getShare
from ...state import BobState
from ...tty import colorize, ERROR, WARNING, EXECUTED, DEFAULT, Warn
from ...utils import removePath, processDefines
import argparse
import os

from .state import DevelopDirOracle

__all__ = ['doClean']

UNKNOWN = ScmStatus(ScmTaint.unknown)

def collectPaths(rootPackage):
    paths = set()
    done = set()

    def walk(package):
        if package._getId() in done: return
        done.add(package._getId())

        checkoutStep = package.getCheckoutStep()
        if checkoutStep.isValid():
            paths.add(checkoutStep.getWorkspacePath())

        # Remove known directories where the digest does not match. The
        # directory state is stored as list where the first entry is the
        # incremental variant id.
        buildStep = package.getBuildStep()
        if buildStep.isValid():
            p = buildStep.getWorkspacePath()
            state = BobState().getDirectoryState(p, False)
            if (state is None) or (buildStep.getVariantId() == state[0]):
                paths.add(p)

        # Remove known directories where the digest does not match.
        packageStep = package.getPackageStep()
        p = packageStep.getWorkspacePath()
        state = BobState().getDirectoryState(p, False)
        if (state is None) or (packageStep.getVariantId() == state):
            paths.add(p)

        for d in package.getDirectDepSteps():
            walk(d.getPackage())

    walk(rootPackage)
    return paths

def checkSCM(workspace, scmDir, scmSpec, verbose):
    if scmSpec is not None:
        status = getScm(scmSpec).status(workspace)
    else:
        status = UNKNOWN

    if verbose:
        flags = str(status)
        if status.error:
            color = ERROR
        elif not status.expendable:
            color = WARNING
        else:
            color = EXECUTED if flags else DEFAULT
        if scmDir != ".":
            workspace = os.path.join(workspace, scmDir)
        print(colorize("STATUS {0: <4} {1}".format(flags, workspace), color))

    return status.expendable

def checkRegularSource(workspace, verbose):
    ret = True
    state = BobState().getDirectoryState(workspace, True)
    for (scmDir, (scmDigest, scmSpec)) in checkoutsFromState(state):
        if not checkSCM(workspace, scmDir, scmSpec, verbose):
            ret = False

    return ret

def checkAtticSource(workspace, verbose):
    scmSpec = BobState().getAtticDirectoryState(workspace)
    # We must remove the 'dir' propery if present because the attic directory
    # is already the final directory. Old projects might have scmSpec as None!
    if scmSpec and ('dir' in scmSpec): del scmSpec['dir']
    return checkSCM(workspace, ".", scmSpec, verbose)


UNITS = ("Bytes", "KiB", "MiB", "GiB", "TiB")

def doClean(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob clean",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Clean unused directories.

This command removes currently unused directories from previous bob dev/build
invocations.  By default only 'build' and 'package' steps are evicted. Adding
'-s' will clean 'checkout' steps too. Make sure that you have checked in (and
pushed) all your changes, tough. When in doubt add '--dry-run' to see what
would get removed without actually deleting that already.
""")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_const', const='develop', dest='mode',
        help="Clean developer mode directories (dev/..., default)", default='develop')
    group.add_argument('--release', action='store_const', const='release', dest='mode',
        help="Clean release mode directories (work/...)")
    group.add_argument('--attic', action='store_const', const='attic', dest='mode',
        help="Clean attic directories (dev/.../attic_*)")
    group.add_argument('--shared', action='store_const', const='shared', dest='mode',
        help="Clean shared package repository")

    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-f', '--force', default=False, action='store_true',
        help="Force deletion of unknown/unclean SCMs")
    parser.add_argument('-s', '--src', default=False, action='store_true',
        help="Clean source workspaces too")
    parser.add_argument('--all-unused', default=False, action='store_true',
        help="Remove all unused shared packages, even if quota is not exceeded")
    parser.add_argument('--used', default=False, action='store_true',
        help="Also remove used shared packages if quota is exceeded")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=None,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")

    parser.add_argument('-v', '--verbose', default=False, action='store_true',
        help="Print what is done")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    develop = args.mode != 'release'
    if args.sandbox is None:
        args.sandbox = not develop

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', None)
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)

    if args.mode == 'attic':
        delPaths = sorted(d for d in BobState().getAtticDirectories()
            if os.path.exists(d) and (args.force or checkAtticSource(d, args.verbose)))
    elif args.mode == 'shared':
        delPaths = []
        share = getShare(recipes.getShareConfig())
        repoSize = share.gc(args.used, args.all_unused, args.dry_run,
                            lambda x: delPaths.append(x))
        if (repoSize is not None) and (share.quota is not None) and (repoSize > share.quota):
            excess = repoSize - share.quota
            for unit in UNITS:
                if excess < 1024: break
                excess = excess // 1024
            Warn("Could not free enough space to meet quota. ({}{} over quota)".format(excess, unit),
                 help="You can add --used to remove packages that are still used.").show()
    else:
        # Get directory name formatter into shape
        if develop:
            nameFormatter = recipes.getHook('developNameFormatter')
            developPersister = DevelopDirOracle(nameFormatter, recipes.getHook('developNamePersister'))
            nameFormatter = developPersister.getFormatter()
        else:
            # Special read-only "persister" that does create new entries. The
            # actual formatter is irrelevant.
            nameFormatter = LocalBuilder.releaseNameInterrogator
        nameFormatter = LocalBuilder.makeRunnable(nameFormatter)
        packages = recipes.generatePackages(nameFormatter, args.sandbox)
        if develop: developPersister.prime(packages)

        if args.mode == 'release':
            # collect all used paths
            usedPaths = collectPaths(packages.getRootPackage())

            # get all known release paths
            allPaths = [ (os.path.join(dir, "workspace"), isSourceDir)
                for (dir, isSourceDir) in BobState().getAllNameDirectores() ]
        elif args.mode == 'develop':
            # collect all used paths
            usedPaths = collectPaths(packages.getRootPackage())

            # Determinte all known develop paths. Bob does not directly store
            # this information.  We start with all known directories and
            # subtract the release paths. Source workspaces are detected by
            # their state being a dict (instead of a bytes object).
            releasePaths = set(os.path.join(dir, "workspace")
                for (dir, isSourceDir) in BobState().getAllNameDirectores())
            allPaths = [
                (dir, isinstance(BobState().getDirectoryState(dir, False), dict))
                for dir in BobState().getDirectories()
                if dir not in releasePaths ]

        # Source workspace policy
        if args.src:
            if args.force:
                mayClean = lambda d: True
            else:
                mayClean = lambda d: checkRegularSource(d, args.verbose)
        else:
            mayClean = lambda d: False

        # Remove non-existent directories and source workspaces that are not
        # allowed to be touched.
        delPaths = sorted(d for (d, isSourceDir) in allPaths
            if (d not in usedPaths) and os.path.exists(d) and
               (not isSourceDir or mayClean(d)))

    # Finally delete unused directories.
    BobState().setAsynchronous()
    try:
        for d in delPaths:
            if args.verbose or args.dry_run:
                print("rm", d)
            if not args.dry_run and args.mode != 'shared':
                removePath(d)
                if args.mode == 'attic':
                    BobState().delAtticDirectoryState(d)
                else:
                    BobState().delDirectoryState(d)

        # cleanup BobState() of non-existent directories
        if not args.dry_run:
            for d in BobState().getDirectories():
                if not os.path.exists(d): BobState().delDirectoryState(d)
            for d in BobState().getAtticDirectories():
                if not os.path.exists(d): BobState().delAtticDirectoryState(d)
    finally:
        BobState().setSynchronous()
