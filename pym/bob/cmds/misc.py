# -*- coding: utf-8 -*-

# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..input import RecipeSet
from ..errors import ParseError, BuildError
from .helpers import processDefines
import argparse
import codecs
import sys
import os, os.path

try:
    # test if stdout can handle box drawing characters
    codecs.encode("└├│─", sys.stdout.encoding)
    LS_SEP_1 = u"└── "
    LS_SEP_2 = u"├── "
    LS_SEP_3 = u"    "
    LS_SEP_4 = u"│   "
except UnicodeEncodeError:
    # fall back to ASCII
    LS_SEP_1 = "\\-- "
    LS_SEP_2 = "|-- "
    LS_SEP_3 = "    "
    LS_SEP_4 = "|   "


class PackagePrinter:
    def __init__(self, showAll, showOrigin, recurse, unsorted):
        self.showAll = showAll
        self.showOrigin = showOrigin
        self.recurse = recurse
        if unsorted:
            self.sort = lambda x: x
        else:
            self.sort = sorted

    def __getChilds(self, package):
        return [
            (name, child.node, " ({})".format(child.origin) if (self.showOrigin and child.origin) else "")
            for (name, child) in self.sort(package.items())
            if (self.showAll or child.direct)
        ]

    def showTree(self, package, prefix=""):
        i = 0
        packages = self.__getChilds(package)
        for (n, p, o) in packages:
            last = (i >= len(packages)-1)
            print("{}{}{}{}".format(prefix, LS_SEP_1 if last else LS_SEP_2, n, o))
            self.showTree(p, prefix + (LS_SEP_3 if last else LS_SEP_4))
            i += 1

    def showPrefixed(self, package, showAliases, stack=[], level=0):
        for p in showAliases: print(p)
        for (n, p, o) in self.__getChilds(package):
            newStack = stack[:]
            newStack.append(n)
            print("{}{}".format("/".join(newStack), o))
            if self.recurse:
                self.showPrefixed(p, [], newStack, level+1)


def doLS(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob ls", description='List packages.')
    parser.add_argument('package', type=str, nargs='?', default="",
                        help="Sub-package to start listing from")
    parser.add_argument('-a', '--all', default=False, action='store_true',
                        help="Show indirect dependencies too")
    parser.add_argument('-A', '--alternates', default=False, action='store_true',
                        help="Show all alternate paths to identical packages too")
    parser.add_argument('-o', '--origin', default=False, action='store_true',
                        help="Show origin of indirect dependencies")
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
    parser.add_argument('-u', '--unsorted', default=False, action='store_true',
                        help="Show packages in recipe order (unsorted)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-p', '--prefixed', default=False, action='store_true',
                       help="Prints the full path prefix for each package")
    group.add_argument('-d', '--direct', default=False, action='store_true',
                       help="List packages themselves, not their contents")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
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
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)

    packages = recipes.generatePackages(lambda s,m: "unused", args.sandbox)
    showAliases = packages.getAliases() if args.package == "" else []

    printer = PackagePrinter(args.all, args.origin, args.recursive, args.unsorted)
    showAlternates = args.alternates and (args.prefixed or args.direct)
    for (stack, root) in packages.queryTreePath(args.package, showAlternates):
        if args.prefixed:
            printer.showPrefixed(root, showAliases, stack)
        elif args.direct:
            print("/".join(stack) if stack else "/")
        elif args.recursive:
            print("/".join(stack) if stack else "/")
            printer.showTree(root)
        else:
            printer.showPrefixed(root, showAliases)

class Default(dict):
    def __init__(self, default, *args, **kwargs):
        self.__default = default
        super().__init__(*args, **kwargs)

    def __missing__(self, key):
        return self.__default

def doQueryMeta(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob query-meta",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="""Query meta information of packages.""")
    parser.add_argument('packages', nargs='+', help="(Sub-)packages to query")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
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
    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)
    packages = recipes.generatePackages(lambda s,m: "unused", args.sandbox)

    def showPackage(package, recurse, done):
        # Show each package only once. Meta variables are fixed and not variant
        # dependent.
        key = package.getName()
        if key not in done:
            for (var, val) in package.getMetaEnv().items():
                print(package.getName() + " " + var + "=" + val)
            done.add(key)

        # recurse package tree if requested
        if recurse:
            for ps in package.getDirectDepSteps():
                showPackage(ps.getPackage(), recurse, done)

    done = set()
    for p in args.packages:
        for package in packages.queryPackagePath(p):
            showPackage(package, args.recursive, done)

def doQuerySCM(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob query-scm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Query SCM configuration of packages.

By default this command will print one line for each SCM in the given package.
The output format may be overridded by '-f'. By default the following formats
are used:

 * git="git {package} {dir} {url} {branch}"
 * svn="svn {package} {dir} {url} {revision}"
 * cvs="cvs {package} {dir} {cvsroot} {module}"
 * url="url {package} {dir}/{fileName} {url}"
""")
    parser.add_argument('packages', nargs='+', help="(Sub-)packages to query")

    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-f', default=[], action='append', dest="formats",
        help="Output format for scm (syntax: scm=format). Can be specified multiple times.")
    parser.add_argument('--default', default="", help='Default for missing attributes (default: "")')
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
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

    formats = {
        'git' : "git {package} {dir} {url} {branch}",
        'svn' : "svn {package} {dir} {url} {revision}",
        'cvs' : "cvs {package} {dir} {cvsroot} {module}",
        'url' : "url {package} {dir}/{fileName} {url}",
    }

    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)
    packages = recipes.generatePackages(lambda s,m: "unused", args.sandbox)

    # update formats
    for fmt in args.formats:
        f = fmt.split("=")
        if len(f) != 2: parser.error("Malformed format: "+fmt)
        formats[f[0]] = f[1]

    def showPackage(package, recurse, done, donePackages):
        if package._getId() in donePackages:
            return
        donePackages.add(package._getId())

        # show recipes only once for each checkout variant
        key = (package.getRecipe().getName(), package.getCheckoutStep().getVariantId())
        if key not in done:
            for scm in package.getCheckoutStep().getScmList():
                p = { k:v for (k,v) in scm.getProperties(False).items() if v is not None }
                p['package'] = "/".join(package.getStack())
                fmt = formats.get(p['scm'], "{scm} {dir}")
                print(fmt.format_map(Default(args.default, p)))
            done.add(key)

        # recurse package tree if requested
        if recurse:
            for ps in package.getDirectDepSteps():
                showPackage(ps.getPackage(), recurse, done, donePackages)

    done = set()
    donePackages = set()
    for p in args.packages:
        for package in packages.queryPackagePath(p):
            showPackage(package, args.recursive, done, donePackages)

def doQueryRecipe(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob query-recipe",
        description="Query recipe and class files of package.")
    parser.add_argument('package', help="(Sub-)package to query")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
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

    args = parser.parse_args(argv)

    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)
    package = recipes.generatePackages(lambda s,m: "unused", args.sandbox).walkPackagePath(args.package)

    for fn in package.getRecipe().getSources():
        print(fn)

def doInit(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob init",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Initialize out-of-source build tree.

Create a Bob build tree in the current directory or at the given BUILD
directory. The recipes, classes, plugins and all other files are taken from the
project root directory at PROJECT.
""")
    parser.add_argument('project', metavar="PROJECT",
        help="Project root directory")
    parser.add_argument('build', nargs='?', metavar="BUILD", default=".",
        help="Build directory (default: .)")

    args = parser.parse_args(argv)

    recipesDir = os.path.join(args.project, "recipes")
    if not os.path.isdir(recipesDir):
        raise ParseError("No recipes directory found in " + recipesDir)

    try:
        os.makedirs(args.build, exist_ok=True)
        if os.path.samefile(args.project, args.build):
            print("The project directory does not need to be initialized.",
                  file=sys.stderr)
            return
    except OSError as e:
        raise ParseError("Error creating build directory: " + str(e))

    projectLink = os.path.join(args.build, ".bob-project")
    if os.path.exists(projectLink):
        raise ParseError("Build tree already initialized!")

    try:
        with open(projectLink, "w") as f:
            f.write(os.path.abspath(args.project))
    except OSError as e:
        raise ParseError("Cannot create project link: " + str(e))
