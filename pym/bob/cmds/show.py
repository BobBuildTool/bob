# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import ParseError
from ..input import RecipeSet
from ..variant import dumpPackage, diffPackages
from .helpers import processDefines, addStandardArgs, dumpYaml, dumpJson, dumpFlat
import argparse
import json
import sys
import yaml

ALL_FIELDS = (
    "buildNetAccess", "buildTools", "buildToolsWeak", "buildVars",
    "buildVarsWeak", "checkoutAssert", "checkoutDeterministic", "checkoutSCM",
    "checkoutTools", "checkoutToolsWeak", "checkoutVars", "checkoutVarsWeak",
    "depends", "fingerprintIf", "jobServer", "meta", "metaEnvironment",
    "packageNetAccess", "packageTools", "packageToolsWeak", "packageVars",
    "packageVarsWeak", "relocatable", "root", "sandbox", "scriptLanguage",
    "shared",
)

def filterEmpty(doc):
    if isinstance(doc, dict):
        return { k : filterEmpty(v) for k, v in doc.items() if v }
    elif isinstance(doc, list):
        return [ filterEmpty(v) for v in doc ]
    else:
        return doc

def filterFields(doc, fields):
    if not fields:
        return doc
    else:
        return { k:v for k,v in doc.items() if k in fields }

def showPackagesYaml(docs, indent):
    return "\n".join("--- # {}\n{}".format(package, dumpYaml(doc, indent))
                     for package, doc in docs)

def showPackagesJson(docs, indent):
    docs = [ doc for _package, doc in docs ]
    if len(docs) == 1:
        docs = docs[0]
    return dumpJson(docs, indent)

def showPackagesFlat(docs):
    ret = []
    for package, doc in docs:
        ret.append("[" + package + "]")
        ret.extend(dumpFlat(doc))
        ret.append("")

    return "\n".join(ret)

def doShow(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob show",
        description="Show properties of one or more packages.")
    parser.add_argument('packages', nargs='+', help="(Sub-)packages to query")
    addStandardArgs(parser)

    group = parser.add_argument_group('output', "Appearance and content of output")
    group.add_argument('--show-empty', action='store_true', default=False,
        help="Also show empty properties")
    group.add_argument('--show-common', action='store_true', default=False,
        help="Also show unchanged attributes in diff format")
    ex = group.add_mutually_exclusive_group()
    ex.add_argument('--indent', type=int, default=4,
        help="Number of spaces to indent (default: 4)")
    ex.add_argument('--no-indent', action='store_const', const=None,
        dest='indent', help="No indent. Compact format.")
    group.add_argument('--format', choices=['yaml', 'json', 'flat', 'diff'],
        default="yaml", help="Output format")
    group.add_argument('-f', dest='field', action='append',
        choices=sorted(ALL_FIELDS),
        help="Show particular field(s) instead of all")

    args = parser.parse_args(argv)
    defines = processDefines(args.defines)

    recipes = RecipeSet()
    recipes.setConfigFiles(args.configFile)
    recipes.parse(defines)
    packages = recipes.generatePackages(lambda s,m: "unused", args.sandbox)

    if not args.show_empty:
        filt = filterEmpty
    else:
        filt = lambda x: x

    if args.field:
        filt = lambda x, filt=filt: filterFields(filt(x), args.field)

    docs = []
    with packages:
        for p in args.packages:
            for package in packages.queryPackagePath(p):
                docs.append(("/".join(package.getStack()), filt(dumpPackage(package))))

    if not docs:
        print("Your query matched no packets!", file=sys.stderr)
        ret = None
    elif args.format == 'yaml':
        ret = showPackagesYaml(docs, args.indent)
    elif args.format == 'json':
        ret = showPackagesJson(docs, args.indent)
    elif args.format == 'flat':
        ret = showPackagesFlat(docs)
    elif len(docs) != 2:
        raise ParseError("Diff format requires exactly two packages")
    else:
        ret = diffPackages(docs[0][1], docs[1][1], args.show_common)

    if ret: print(ret)
