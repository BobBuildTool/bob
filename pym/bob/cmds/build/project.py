# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ...builder import LocalBuilder
from ...errors import BobError
from ...generators import generators as defaultGenerators
from ...input import RecipeSet
from ...tty import colorize
from ...utils import SandboxMode
from ..helpers import processDefines
import argparse
import os

from .build import doDevelop
from .state import DevelopDirOracle

def doProject(argv, bobRoot):
    def _downloadArg(arg):
        if (arg.startswith("packages=") or arg in ['yes', 'no', 'deps']):
            return arg
        raise argparse.ArgumentTypeError("{} invalid.".format(arg))

    parser = argparse.ArgumentParser(prog="bob project", description='Generate Project Files')
    parser.add_argument('projectGenerator', nargs='?', help="Generator to use.")
    parser.add_argument('package', nargs='?', help="Sub-package that is the root of the project")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for project generator")

    parser.add_argument('--list', default=False, action='store_true', help="List available Generators")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('--download', metavar="MODE", default="no",
        help="Download from binary archive (yes, no, deps, packages=<regular expression>)",
        type=_downloadArg)
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-n', dest="execute_prebuild", default=True, action='store_false',
        help="Do not build (bob dev) before generate project Files. RunTargets may not work")
    parser.add_argument('-b', dest="execute_buildonly", default=False, action='store_true',
        help="Do build only (bob dev -b) before generate project Files. No checkout")
    parser.add_argument('-j', '--jobs', default=None, type=int, nargs='?', const=...,
        help="Specifies  the  number of jobs to run simultaneously.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_const', const="yes", default="no",
        help="Enable partial sandboxing")
    group.add_argument('--slim-sandbox', action='store_const', const="slim", dest='sandbox',
        help="Enable slim sandboxing")
    group.add_argument('--dev-sandbox', action='store_const', const="dev", dest='sandbox',
        help="Enable development sandboxing")
    group.add_argument('--strict-sandbox', action='store_const', const="strict", dest='sandbox',
        help="Enable strict sandboxing")
    group.add_argument('--no-sandbox', action='store_const', const="no", dest='sandbox',
        help="Disable sandboxing")
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', None)
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    nameFormatter = recipes.getHook('developNameFormatter')
    developPersister = DevelopDirOracle(nameFormatter, recipes.getHook('developNamePersister'))
    nameFormatter = LocalBuilder.makeRunnable(developPersister.getFormatter())
    sandboxMode = SandboxMode(args.sandbox)
    packages = recipes.generatePackages(nameFormatter, sandboxMode.sandboxEnabled,
                                        sandboxMode.stablePaths)
    developPersister.prime(packages)

    generators = defaultGenerators.copy()
    generators.update(recipes.getProjectGenerators())

    if args.list:
        for g in generators:
            print(g)
        return 0
    else:
        if not args.package or not args.projectGenerator:
            raise BobError("The following arguments are required: projectGenerator, package")

    try:
        generator = generators[args.projectGenerator]
    except KeyError:
        raise BobError("Generator '{}' not found!".format(args.projectGenerator))

    extra = [ "--download=" + args.download ]
    for d in args.defines:
        extra.append('-D')
        extra.append(d)
    for c in args.configFile:
        extra.append('-c')
        extra.append(c)
    for e in args.white_list:
        extra.append('-e')
        extra.append(e)
    if args.preserve_env: extra.append('-E')
    if args.sandbox == "yes":
        extra.append('--sandbox')
    elif args.sandbox == "slim":
        extra.append('--slim-sandbox')
    elif args.sandbox == "dev":
        extra.append('--dev-sandbox')
    elif args.sandbox == "strict":
        extra.append('--strict-sandbox')
    if args.jobs is ...:
        # expand because we cannot control the argument order in the generator
        args.jobs = os.cpu_count()
    if args.jobs is not None:
        if args.jobs <= 0:
            parser.error("--jobs argument must be greater than zero!")
        extra.extend(['-j', str(args.jobs)])

    if generator.get('query'):
        package = packages.queryPackagePath(args.package)
    else:
        package = packages.walkPackagePath(args.package)
        print(">>", colorize("/".join(package.getStack()), "32;1"))

    # execute a bob dev with the extra arguments to build all executables.
    # This makes it possible for the plugin to collect them and generate some runTargets.
    if args.execute_prebuild:
        devArgs = extra.copy()
        if args.resume: devArgs.append('--resume')
        if args.execute_buildonly: devArgs.append('-b')
        devArgs.append(args.package)
        doDevelop(devArgs, bobRoot)

    print(colorize("   PROJECT   {} ({})".format(args.package, args.projectGenerator), "32"))
    generator.get('func')(package, args.args, extra, bobRoot)

