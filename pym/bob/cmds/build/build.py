# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...archive import getArchiver
from ...builder import LocalBuilder
from ...errors import BuildError
from ...input import RecipeSet
from ...intermediate import StepIR, PackageIR, RecipeIR, ToolIR, SandboxIR, \
    RecipeSetIR
from ...share import getShare
from ...tty import setVerbosity, setTui, Warn
from ...utils import copyTree, processDefines, EventLoopWrapper
import argparse
import datetime
import re
import os
import subprocess
import stat
import sys
import time

from .state import DevelopDirOracle

class LazyIR:
    @staticmethod
    def addStep(step, partial):
        return step

    @staticmethod
    def addSandbox(sandbox):
        return sandbox

    @staticmethod
    def addTool(tool):
        return tool

    @staticmethod
    def addPackage(package, partial):
        return package

    @staticmethod
    def addRecipe(recipe):
        return recipe

    @staticmethod
    def addRecipeSet(recipeSet):
        return recipeSet

class LazyIRs:
    JENKINS = False

    def mungeStep(self, step):
        return ExecutableStep.fromStep(step, LazyIR)

    def mungePackage(self, package):
        return ExecutablePackage.fromPackage(package, LazyIR)

    def mungeRecipe(self, recipe):
        return ExecutableRecipe.fromRecipe(recipe, LazyIR)

    def mungeSandbox(self, sandbox):
        return sandbox and ExecutableSandbox.fromSandbox(sandbox, LazyIR)

    def mungeTool(self, tool):
        return ExecutableTool.fromTool(tool, LazyIR)

    def mungeRecipeSet(self, recipeSet):
        return ExecutableRecipeSet.fromRecipeSet(recipeSet)

class ExecutableStep(LazyIRs, StepIR):
    pass

class ExecutablePackage(LazyIRs, PackageIR):
    pass

class ExecutableRecipe(LazyIRs, RecipeIR):
    pass

class ExecutableRecipeSet(LazyIRs, RecipeSetIR):
    @classmethod
    def fromRecipeSet(cls, recipeSet):
        self = super(ExecutableRecipeSet, cls).fromRecipeSet(recipeSet)
        self.__recipeSet = recipeSet
        return self

    async def getScmAudit(self):
        return await self.__recipeSet.getScmAudit()

class ExecutableTool(LazyIRs, ToolIR):
    pass

class ExecutableSandbox(LazyIRs, SandboxIR):
    pass


def runHook(recipes, hook, args):
    hookCmd = recipes.getBuildHook(hook)
    ret = True
    if hookCmd:
        try:
            hookCmd = os.path.expanduser(hookCmd)
            ret = subprocess.call([hookCmd] + args) == 0
        except OSError as e:
            raise BuildError(hook + ": cannot run '" + hookCmd + ": " + str(e))

    return ret

def commonBuildDevelop(parser, argv, bobRoot, develop):
    def _downloadArgument(arg):
        if arg.startswith('packages=') or arg in ['yes', 'no', 'deps', 'forced', 'forced-deps', 'forced-fallback']:
            return arg
        raise argparse.ArgumentTypeError("{} invalid.".format(arg))
    def _downloadLayerArgument(arg):
        if re.match(r'^(yes|no|forced)=\S+$', arg):
            return arg
        raise argparse.ArgumentTypeError("{} invalid.".format(arg))

    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to build")
    parser.add_argument('--destination', metavar="DEST", default=None,
        help="Destination of build result (will be overwritten!)")
    parser.add_argument('-j', '--jobs', default=None, type=int, nargs='?', const=...,
        help="Specifies  the  number of jobs to run simultaneously.")
    parser.add_argument('-k', '--keep-going', default=None, action='store_true',
        help="Continue  as much as possible after an error.")
    parser.add_argument('-f', '--force', default=None, action='store_true',
        help="Force execution of all build steps")
    parser.add_argument('-n', '--no-deps', default=None, action='store_true',
        help="Don't build dependencies")
    parser.add_argument('-p', '--with-provided', dest='build_provided', default=None, action='store_true',
        help="Build provided dependencies")
    parser.add_argument('--without-provided', dest='build_provided', default=None, action='store_false',
        help="Build without provided dependencies")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('-A', '--no-audit', dest='audit', default=None,
        action='store_false',
        help="Don't generate audit trail for build results")
    group.add_argument('--audit', dest='audit', action='store_true',
        help="Generate audit trail (default)")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('-b', '--build-only', dest='build_mode', default=None,
        action='store_const', const='build-only',
        help="Don't checkout, just build and package")
    group.add_argument('-B', '--checkout-only', dest='build_mode',
        action='store_const', const='checkout-only',
        help="Don't build, just check out sources")
    group.add_argument('--normal', dest='build_mode',
        action='store_const', const='normal',
        help="Checkout, build and package")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--clean', action='store_true', default=None,
        help="Do clean builds (clear build directory)")
    group.add_argument('--incremental', action='store_false', dest='clean',
        help="Reuse build directory for incremental builds")
    parser.add_argument('--always-checkout', default=[], action='append', metavar="RE",
        help="Regex pattern of packages that should always be checked out")
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('--no-logfiles', default=None, action='store_true',
        help="Disable logFile generation.")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('-M', default=[], action='append', dest="meta",
        help="Add meta key to audit trail")
    parser.add_argument('--upload', default=None, action='store_true',
        help="Upload to binary archive")
    parser.add_argument('--link-deps', default=None, help="Add linked dependencies to workspace paths",
        dest='link_deps', action='store_true')
    parser.add_argument('--no-link-deps', default=None, help="Do not add linked dependencies to workspace paths",
        dest='link_deps', action='store_false')
    parser.add_argument('--download', metavar="MODE", default=None,
        help="Download from binary archive (yes, no, deps, forced, forced-deps, forced-fallback, packages=<packages>)",
        type=_downloadArgument)
    parser.add_argument('--download-layer', metavar="MODE", action='append', default=[],
        help="Download from binary archive for layer recipes (yes=<layer>, no=<layer>, forced=<layer>)",
        type=_downloadLayerArgument)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--shared', action='store_true', default=None,
        help="Use shared packages")
    group.add_argument('--no-shared', action='store_false', dest='shared',
        help="Do not use shared packages")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--install', action='store_true', default=None,
        help="Install shared packages")
    group.add_argument('--no-install', action='store_false', dest='install',
        help="Do not install shared packages")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=None,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    parser.add_argument('--clean-checkout', action='store_true', default=None, dest='clean_checkout',
        help="Do a clean checkout if SCM state is dirty.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--attic', action='store_true', default=None,
        help="Move scm to attic if inline switch is not possible (default).")
    group.add_argument('--no-attic', action='store_false', default=None, dest='attic',
        help="Do not move to attic, instead fail the build.")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)
    meta = processDefines(args.meta)

    startTime = time.time()

    with EventLoopWrapper() as (loop, executor):
        recipes = RecipeSet()
        recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
        recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
        recipes.defineHook('developNamePersister', None)
        recipes.setConfigFiles(args.configFile)
        recipes.parse(defines)

        # if arguments are not passed on cmdline use them from default.yaml or set to default yalue
        if develop:
            cfg = recipes.getCommandConfig().get('dev', {})
        else:
            cfg = recipes.getCommandConfig().get('build', {})

        noJobs = args.jobs == None
        defaults = {
                'destination' : '',
                'force' : False,
                'no_deps' : False,
                'build_mode' : 'normal',
                'clean' : not develop,
                'upload' : False,
                'download' : "deps" if develop else "yes",
                'sandbox' : not develop,
                'clean_checkout' : False,
                'no_logfiles' : False,
                'link_deps' : True,
                'jobs' : 1,
                'keep_going' : False,
                'audit' : True,
                'shared' : True,
                'install' : True,
                'attic' : True,
            }

        for a in vars(args):
            if getattr(args, a) is None:
                setattr(args, a, cfg.get(a, defaults.get(a)))
            elif isinstance(getattr(args, a), list):
                setattr(args, a, cfg.get(a, []) + getattr(args, a))

        if args.jobs is ...:
            args.jobs = os.cpu_count()
        elif args.jobs <= 0:
            parser.error("--jobs argument must be greater than zero!")

        # parse MAKEFLAGS environment variable to setup number of jobs
        # when called from make
        makeFlags = os.environ.get('MAKEFLAGS')
        makeFds = None
        if makeFlags is not None:
            jobs = re.search(r'-j([0-9]*)', makeFlags)
            fds = re.search(r'--jobserver-auth=([0-9]*),([0-9]*)', makeFlags)
            if jobs and fds and jobs.group(1) and fds.group(1) and fds.group(2):
                if noJobs:
                    args.jobs = int(jobs.group(1))
                    makeFds = [int(fds.group(1)), int(fds.group(2))]
                    try:
                        if not all(stat.S_ISFIFO(os.stat(f).st_mode) for f in makeFds): makeFds = None
                    except OSError:
                        makeFds = None
                else:
                    print("warning: -j" + str(args.jobs) + " forced: resetting jobserver mode.");

        envWhiteList = recipes.envWhiteList()
        envWhiteList |= set(args.white_list)

        if develop:
            nameFormatter = recipes.getHook('developNameFormatter')
            developPersister = DevelopDirOracle(nameFormatter, recipes.getHook('developNamePersister'))
            nameFormatter = developPersister.getFormatter()
        else:
            nameFormatter = recipes.getHook('releaseNameFormatter')
            nameFormatter = LocalBuilder.releaseNamePersister(nameFormatter)
        nameFormatter = LocalBuilder.makeRunnable(nameFormatter)
        packages = recipes.generatePackages(nameFormatter, args.sandbox)
        if develop: developPersister.prime(packages)

        verbosity = cfg.get('verbosity', 0) + args.verbose - args.quiet
        setVerbosity(verbosity)
        builder = LocalBuilder(verbosity, args.force,
                               args.no_deps, True if args.build_mode == 'build-only' else False,
                               args.preserve_env, envWhiteList, bobRoot, args.clean,
                               args.no_logfiles)

        builder.setExecutor(executor)
        builder.setArchiveHandler(getArchiver(recipes))
        builder.setLocalUploadMode(args.upload)
        builder.setLocalDownloadMode(args.download)
        builder.setLocalDownloadLayerMode(args.download_layer)
        builder.setCleanCheckout(args.clean_checkout)
        builder.setAlwaysCheckout(args.always_checkout + cfg.get('always_checkout', []))
        builder.setLinkDependencies(args.link_deps)
        builder.setJobs(args.jobs)
        builder.setMakeFds(makeFds)
        builder.setKeepGoing(args.keep_going)
        builder.setAudit(args.audit)
        builder.setAuditMeta(meta)
        builder.setShareHandler(getShare(recipes.getShareConfig()))
        builder.setShareMode(args.shared, args.install)
        builder.setAtticEnable(args.attic)
        if args.resume: builder.loadBuildState()

        backlog = []
        providedBacklog = []
        results = []
        for p in args.packages:
            for package in packages.queryPackagePath(p):
                packageStep = package.getPackageStep()
                backlog.append(packageStep)
                # automatically include provided deps when exporting
                build_provided = (args.destination and args.build_provided == None) or args.build_provided
                if build_provided: providedBacklog.extend(packageStep._getProvidedDeps())

        success = runHook(recipes, 'preBuildHook',
            ["/".join(p.getPackage().getStack()) for p in backlog])
        if not success:
            raise BuildError("preBuildHook failed!",
                help="A preBuildHook is set but it returned with a non-zero status.")
        success = False
        if args.jobs > 1:
            setTui(args.jobs)
            builder.enableBufferedIO()
        try:
            builder.cook([ExecutableStep.fromStep(b, LazyIR) for b in backlog],
                         True if args.build_mode == 'checkout-only' else False,
                         loop)
            for p in backlog:
                resultPath = p.getWorkspacePath()
                if resultPath not in results:
                    results.append(resultPath)
            builder.cook([ExecutableStep.fromStep(b, LazyIR) for b in providedBacklog],
                         True if args.build_mode == 'checkout-only' else False,
                         loop, 1)
            for p in providedBacklog:
                resultPath = p.getWorkspacePath()
                if resultPath not in results:
                    results.append(resultPath)
            success = True
        finally:
            if args.jobs > 1: setTui(1)
            builder.saveBuildState()
            runHook(recipes, 'postBuildHook', ["success" if success else "fail"] + results)

    # tell the user
    if results:
        if len(results) == 1:
            print("Build result is in", results[0])
        else:
            print("Build results are in:\n  ", "\n   ".join(results))

        endTime = time.time()
        stats = builder.getStatistic()
        activeOverrides = len(stats.getActiveOverrides())
        print("Duration: " + str(datetime.timedelta(seconds=(endTime - startTime))) + ", "
                + str(stats.checkouts)
                    + " checkout" + ("s" if (stats.checkouts != 1) else "")
                    + " (" + str(activeOverrides) + (" overrides" if (activeOverrides != 1) else " override") + " active), "
                + str(stats.packagesBuilt)
                    + " package" + ("s" if (stats.packagesBuilt != 1) else "") + " built, "
                + str(stats.packagesDownloaded) + " downloaded.")

        # Copy build result if requested. It's ok to overwrite files that are
        # already at the destination. Warn if built packages overwrite
        # themselves, though.
        ok = True
        if args.destination:
            allFiles = set()
            collisions = set()
            for result in results:
                nextFiles = set()
                ok = copyTree(result, args.destination, nextFiles) and ok
                collisions |= allFiles & nextFiles
                allFiles |= nextFiles
            if collisions:
                shownCollisions = ", ".join(sorted(collisions)[:3])
                if len(collisions) > 3: shownCollisions = shownCollisions + ", ..."
                Warn("duplicate files at distination overwritten: " + shownCollisions).warn()
        if not ok:
            raise BuildError("Could not copy everything to destination. Your aggregated result is probably incomplete.")
    else:
        print("Your query matched no packages. Naptime!")

def doBuild(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob build", description='Build packages in release mode.')
    commonBuildDevelop(parser, argv, bobRoot, False)

def doDevelop(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob dev", description='Build packages in development mode.')
    commonBuildDevelop(parser, argv, bobRoot, True)

