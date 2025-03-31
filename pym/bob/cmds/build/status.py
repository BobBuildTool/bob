# Bob build tool
# Copyright (C) 2016-2019  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...builder import LocalBuilder, checkoutsFromState
from ...input import RecipeSet
from ...scm import getScm, ScmTaint, ScmStatus
from ...state import BobState
from ...tty import colorize, ERROR, WARNING, EXECUTED, DEFAULT, SKIPPED, \
    IMPORTANT, NORMAL, INFO, DEBUG, TRACE, HEADLINE
from ...utils import joinLines
from ..helpers import processDefines
from textwrap import indent
import argparse
import os

from .state import DevelopDirOracle

__all__ = ['doStatus']

# Flag to headline verbosity. The description is shown on the next level.
FLAG_TO_VERBOSITY = {
    ScmTaint.attic          : NORMAL,
    ScmTaint.collides       : NORMAL,       # not modified, but will break the build
    ScmTaint.error          : IMPORTANT,    # error, modified
    ScmTaint.modified       : NORMAL,       # modified
    ScmTaint.new            : NORMAL,
    ScmTaint.overridden     : DEBUG,
    ScmTaint.switched       : NORMAL,       # modified
    ScmTaint.unknown        : NORMAL,       # cannot tell, could be modified
    ScmTaint.unpushed_main  : NORMAL,       # modified
    ScmTaint.unpushed_local : INFO,         # not modified but user may loose data
}
assert set(FLAG_TO_VERBOSITY.keys()) == set(ScmTaint)

class PackagePrinter:
    def __init__(self, verbose, showClean, showOverrides, checkoutStep = None):
        self.verbose = verbose
        self.flagVerbosity = FLAG_TO_VERBOSITY.copy()
        self.showClean = showClean
        if showOverrides: self.flagVerbosity[ScmTaint.overridden] = NORMAL
        self.headerShown = checkoutStep is None
        self.checkoutStep = checkoutStep

    def __printHeader(self):
        if not self.headerShown:
            print(">>", colorize("/".join(self.checkoutStep.getPackage().getStack()),
                                 HEADLINE))
            self.headerShown = True

    def __printStatus(self, flags, message, color):
        print(colorize("   STATUS {0: <4} {1}".format(flags, message), color))

    def show(self, status, dir):
        detailedFlags = { flag for flag,severity in self.flagVerbosity.items()
            if severity < self.verbose }

        # Determine severity of headline. If showClean start directly at NORMAL
        # level.
        severity = NORMAL if self.showClean else DEBUG
        for flag in status.flags:
            severity = min(self.flagVerbosity[flag], severity)

        flags = str(status)
        if status.error:
            color = ERROR
        elif status.dirty or (status.flags & {ScmTaint.unknown, ScmTaint.collides}):
            color = WARNING
        elif flags:
            color = EXECUTED
        else:
            color = DEFAULT

        if severity <= self.verbose:
            self.__printHeader()
            self.__printStatus(flags, dir, color)
            description = status.description(detailedFlags)
            if description:
                for line in description.splitlines():
                    print('   ' + line)

    def skipped(self):
        # skipped workspaces are shown only on '-vvv' at least
        if TRACE <= self.verbose:
            self.__printHeader()
            self.__printStatus("",
                "skipped ({} does not exist)".format(self.checkoutStep.getWorkspacePath()),
                SKIPPED)

ATTIC = ScmStatus(ScmTaint.attic,
    description="> Recipe changed. Will be moved to attic on next checkout.")
UNKNOWN = ScmStatus(ScmTaint.unknown,
    description="> Workspace too old. Cannot determine status.")

class Printer:
    def __init__(self, recurse, verbose, showClean, showOverrides, showAttic):
        self.recurse = recurse
        self.verbose = verbose
        self.showClean = showClean
        self.showOverrides = showOverrides
        self.doneSteps = set()
        self.donePackages = set()
        self.showAttic = showAttic

    def __showCheckoutStep(self, pp, checkoutStep):
        workspace = checkoutStep.getWorkspacePath()
        oldCheckoutState = BobState().getDirectoryState(workspace, True)
        checkoutState = checkoutStep.getScmDirectories()
        scms = { scm.getDirectory() : scm for scm in checkoutStep.getScmList() }
        result = {}

        # First scan old checkout state. This is what the user is most
        # interested in. The recipe might have changed compared to the
        # persisted state!
        for (scmDir, (scmDigest, scmSpec)) in checkoutsFromState(oldCheckoutState):
            if not os.path.exists(os.path.join(workspace, scmDir)): continue

            if scmDigest == checkoutState.get(scmDir, (None, None))[0]:
                # The digest still matches -> use recipe values
                status = scms[scmDir].status(workspace)
            elif scmSpec is not None:
                # New project that kept scm spec -> compare with that and mark
                # as attic
                status = getScm(scmSpec).status(workspace)
                status.merge(ATTIC)
            else:
                # Don't know anything about it except that this will be moved
                # to the attic
                status = ScmStatus()
                status.merge(ATTIC)
                status.merge(UNKNOWN)

            result[scmDir] = status

        # Additionally scan current checkout state to find new checkouts and
        # determinte override status.
        for scmDir in checkoutState.keys():
            status = result.setdefault(scmDir, ScmStatus(ScmTaint.new))
            if (ScmTaint.new in status.flags) and os.path.exists(os.path.join(workspace, scmDir)):
                status.add(ScmTaint.collides,
                    "> Collides with existing file in workspace.")
            elif ScmTaint.attic in status.flags:
                status.add(ScmTaint.new)

            # The override status is taken from the recipe scm. This is
            # independent of any actual checkout.
            overrides = scms[scmDir].getActiveOverrides()
            for o in overrides:
                status.add(ScmTaint.overridden, joinLines("> Overridden by:",
                    indent(str(o), '   ')))

        for (scmDir, status) in sorted(result.items()):
            pp.show(status, os.path.join(workspace, scmDir))

    def __showAtticDirs(self, pp, prefix=""):
        for d in sorted(BobState().getAtticDirectories()):
            if not os.path.isdir(d):
                BobState().delAtticDirectoryState(d)
                continue
            if not d.startswith(prefix): continue

            scmSpec = BobState().getAtticDirectoryState(d)
            if scmSpec is not None:
                # We must remove the 'dir' propery if present because the attic
                # directory is already the final directory.
                if 'dir' in scmSpec: del scmSpec['dir']
                status = getScm(scmSpec).status(d)
            else:
                status = UNKNOWN

            pp.show(status, d)

    def showPackage(self, package):
        if package._getId() in self.donePackages: return
        self.donePackages.add(package._getId())

        checkoutStep = package.getCheckoutStep()
        if checkoutStep.isValid() and (checkoutStep.getVariantId() not in self.doneSteps):
            pp = PackagePrinter(self.verbose, self.showClean, self.showOverrides,
                checkoutStep)
            workspace = checkoutStep.getWorkspacePath()
            if workspace is not None:
                if os.path.isdir(workspace):
                    self.__showCheckoutStep(pp, checkoutStep)
                else:
                    pp.skipped()
                if self.showAttic:
                    # The last path element (/workspace) must be removed because
                    # attics are located next to the workspace, not inside it.
                    self.__showAtticDirs(pp, os.path.dirname(workspace))
        self.doneSteps.add(checkoutStep.getVariantId())

        if self.recurse:
            for d in package.getDirectDepSteps():
                self.showPackage(d.getPackage())

    def showAllDirs(self, showAttic):
        pp = PackagePrinter(self.verbose, self.showClean, self.showOverrides)
        for workspace in sorted(BobState().getDirectories()):
            dirState = BobState().getDirectoryState(workspace, False)
            # Only the checkout state is stored as dict. Use that to find out
            # which are the right directories.
            if not isinstance(dirState, dict):
                continue

            if not os.path.isdir(workspace):
                BobState().delDirectoryState(workspace)
                continue

            # Upgrade from old format without scmSpec.
            dirState = sorted(
                (dir, state) if isinstance(state, tuple) else (dir, (state, None))
                for dir,state in checkoutsFromState(dirState))
            for (scmDir, (scmDigest, scmSpec)) in dirState:
                scmDir = os.path.join(workspace, scmDir)
                if scmSpec is not None:
                    status = getScm(scmSpec).status(workspace)
                else:
                    status = UNKNOWN
                pp.show(status, scmDir)

        if showAttic:
            self.__showAtticDirs(pp)


def doStatus(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob status", description='Show SCM status')
    parser.add_argument('packages', nargs='*', help="(Sub-)packages")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_true',  dest='develop', help="Use developer mode", default=True)
    group.add_argument('--release', action='store_false', dest='develop', help="Use release mode")

    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")

    parser.add_argument('--attic', action='store_true',
        help="Additionally look in/for attic directories")
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox', help="Disable sandboxing")
    parser.set_defaults(sandbox=None)

    parser.add_argument('--show-clean', action='store_true',
        help="Show SCM status even if checkout is unmodified")
    parser.add_argument('--show-overrides', action='store_true',
        help="Show SCM status if affected by an scmOverrides")
    parser.add_argument('-v', '--verbose', default=NORMAL, action='count',
        help="Increase verbosity (may be specified multiple times)")
    args = parser.parse_args(argv)

    if args.sandbox == None:
        args.sandbox = not args.develop

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', None)
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)

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

    packages = recipes.generatePackages(nameFormatter, args.sandbox)
    if args.develop: developPersister.prime(packages)

    # Dummy query of attic directories. Will warn if project directory was
    # created before Bob 0.15 where they were not tracked!
    if args.attic:
        BobState().getAtticDirectories()

    # Set BobState into asynchronous mode because we might remove many entries
    # if their directories do not exist anymore.
    BobState().setAsynchronous()
    try:
        printer = Printer(args.recursive, args.verbose, args.show_clean,
            args.show_overrides, args.attic)
        if args.packages:
            for p in args.packages:
                for package in packages.queryPackagePath(p):
                    printer.showPackage(package)
        else:
            printer.showAllDirs(args.attic)
    finally:
        BobState().setSynchronous()

