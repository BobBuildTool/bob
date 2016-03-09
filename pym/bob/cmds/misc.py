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

from ..input import RecipeSet
import argparse
import sys

def doLS(argv, bobRoot):
    def showTree(packages, recurse, level=0):
        for p in packages:
            print("{}{}".format("    "*level, p.getName()))
            if recurse:
                showTree([d.getPackage() for d in p.getDirectDepSteps()],
                         recurse, level+1)

    def showPrefixed(packages, recurse, stack, level=0):
        for p in packages:
            newStack = stack[:]
            newStack.append(p.getName())
            print("/".join(newStack))
            if recurse:
                showPrefixed([d.getPackage() for d in p.getDirectDepSteps()],
                             recurse, newStack, level+1)

    parser = argparse.ArgumentParser(prog="bob ls", description='List packages.')
    parser.add_argument('package', type=str, nargs='?',
                        help="Sub-package to start listing from")
    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
    parser.add_argument('-p', '--prefixed', default=False, action='store_true',
                        help="Prints the full path prefix for each package")
    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.parse()

    roots = recipes.generatePackages(lambda step, mode: "unused").values()
    stack = []
    if args.package:
        steps = [ s for s in args.package.split("/") if s != "" ]
        for s in steps:
            roots = [ r for r in roots if r.getName() == s ]
            if not roots:
                print("{}: not found in '{}'".format(s, "/".join(stack)),
                      file=sys.stderr)
                sys.exit(1)
            roots = [ d.getPackage() for d in roots[0].getDirectDepSteps() ]
            stack.append(s)

    if args.prefixed:
        showPrefixed(roots, args.recursive, stack)
    else:
        showTree(roots, args.recursive)

