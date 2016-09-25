# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from ..input import RecipeSet, walkPackagePath
import argparse
import sys

def doLS(argv, bobRoot):
    def showTree(packages, showAll, prefix=""):
        i = 0
        for p in packages:
            last = (i >= len(packages)-1)
            print("{}{}{}".format(prefix, "└── " if last else "├── ", p.getName()))
            deps = p.getAllDepSteps() if showAll else p.getDirectDepSteps()
            showTree([d.getPackage() for d in deps], showAll,
                     prefix + ("    " if last else "│   "))
            i += 1

    def showPrefixed(packages, recurse, showAll, stack, level=0):
        for p in packages:
            newStack = stack[:]
            newStack.append(p.getName())
            print("/".join(newStack))
            if recurse:
                deps = p.getAllDepSteps() if showAll else p.getDirectDepSteps()
                showPrefixed([d.getPackage() for d in deps], recurse, showAll,
                             newStack, level+1)

    parser = argparse.ArgumentParser(prog="bob ls", description='List packages.')
    parser.add_argument('package', type=str, nargs='?',
                        help="Sub-package to start listing from")
    parser.add_argument('-a', '--all', default=False, action='store_true',
                        help="Show indirect dependencies too")
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
    parser.add_argument('-p', '--prefixed', default=False, action='store_true',
                        help="Prints the full path prefix for each package")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=True,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.parse()

    showAll = args.all
    roots = recipes.generatePackages(lambda s,m: "unused", sandboxEnabled=args.sandbox).values()
    stack = []
    if args.package:
        steps = [ s for s in args.package.split("/") if s != "" ]
        for s in steps:
            roots = [ r for r in roots if r.getName() == s ]
            if not roots:
                print("{}: not found in '{}'".format(s, "/".join(stack)),
                      file=sys.stderr)
                sys.exit(1)
            if showAll:
                roots = [ d.getPackage() for d in roots[0].getAllDepSteps() ]
            else:
                roots = [ d.getPackage() for d in roots[0].getDirectDepSteps() ]
            stack.append(s)
    else:
        steps = ["/"]

    if args.prefixed:
        showPrefixed(roots, args.recursive, showAll, stack)
    elif args.recursive:
        print("/".join(steps))
        showTree(roots, showAll)
    else:
        for p in roots: print(p.getName())

class Default(dict):
    def __init__(self, default, *args, **kwargs):
        self.__default = default
        super().__init__(*args, **kwargs)

    def __missing__(self, key):
        return self.__default

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
    parser.add_argument('package', help="(Sub-)package to query")

    parser.add_argument('-f', default=[], action='append', dest="formats",
        help="Output format for scm (syntax: scm=format). Can be specified multiple times.")
    parser.add_argument('--default', default="", help='Default for missing attributes (default: "")')
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")

    formats = {
        'git' : "git {package} {dir} {url} {branch}",
        'svn' : "svn {package} {dir} {url} {revision}",
        'cvs' : "cvs {package} {dir} {cvsroot} {module}",
        'url' : "url {package} {dir}/{fileName} {url}",
    }

    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.parse()
    rootPackages = recipes.generatePackages(lambda s,m: "unused")
    package = walkPackagePath(rootPackages, args.package)

    # update formats
    for fmt in args.formats:
        f = fmt.split("=")
        if len(f) != 2: parser.error("Malformed format: "+fmt)
        formats[f[0]] = f[1]

    def showPackage(package, recurse, done=set()):
        key = (package.getRecipe().getName(), package.getCheckoutStep().getVariantId())
        if key in done: return
        done.add(key)

        for scm in package.getCheckoutStep().getScmList():
            for p in scm.getProperties():
                p = { k:v for (k,v) in p.items() if v is not None }
                p['package'] = "/".join(package.getStack())
                fmt = formats.get(p['scm'], "{scm} {dir}")
                print(fmt.format_map(Default(args.default, p)))

        if recurse:
            for ps in package.getDirectDepSteps():
                showPackage(ps.getPackage(), recurse, done)

    showPackage(package, args.recursive)

def doQueryRecipe(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob query-recipe",
        description="Query recipe and class files of package.")
    parser.add_argument('package', help="(Sub-)package to query")

    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.parse()
    rootPackages = recipes.generatePackages(lambda s,m: "unused")
    package = walkPackagePath(rootPackages, args.package)

    for fn in package.getRecipe().getSources():
        print(fn)

