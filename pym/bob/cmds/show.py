# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import ParseError
from ..input import RecipeSet
from ..tty import colorize, ADDED, DELETED, DEFAULT, ADDED_HIGHLIGHT, DELETED_HIGHLIGHT
from ..utils import asHexStr, processDefines
import argparse
import difflib
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

def dumpPackage(package):
    recipe = package.getRecipe()

    doc = {
        "meta" : {
            "name" : package.getName(),
            "recipe" : recipe.getName(),
            "package" : "/".join(package.getStack()),
            "deterministic" : package.getPackageStep().isDeterministic(),
        },
        "scriptLanguage" : recipe.scriptLanguage.index.value,
        "shared" : recipe.isShared(),
        "metaEnvironment" : package.getMetaEnv(),
        "relocatable" : package.isRelocatable(),
        "jobServer" : recipe.jobServer(),
        "root" : recipe.isRoot(),
    }

    checkoutStep = package.getCheckoutStep()
    if checkoutStep.isValid():
        doc["meta"]["checkoutVariantId"] = asHexStr(checkoutStep.getVariantId())
        doc["checkoutDeterministic"] = checkoutStep.isDeterministic()
        doc["checkoutSCM"] = [
            { k:v for k,v in s.getProperties(False).items() if not k.startswith("__") }
            for s in checkoutStep.getScmList()
        ]
        doc["checkoutAssert"] = [
            { k:v for k,v in a.getProperties().items() if not k.startswith("__") }
            for a in recipe.checkoutAsserts
        ]
        doc["checkoutTools"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in checkoutStep.getTools().items()
            if name in (recipe.toolDepCheckout - recipe.toolDepCheckoutWeak)
        }
        doc["checkoutToolsWeak"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in checkoutStep.getTools().items()
            if name in recipe.toolDepCheckoutWeak
        }
        doc["checkoutVars"] = {
            k : v for k, v in checkoutStep.getEnv().items()
            if k in recipe.checkoutVars
        }
        doc["checkoutVarsWeak"] = {
            k : v for k, v in checkoutStep.getEnv().items()
            if k in recipe.checkoutVarsWeak
        }

    buildStep = package.getBuildStep()
    if buildStep.isValid():
        doc["meta"]["buildVariantId"] = asHexStr(buildStep.getVariantId())
        doc["buildNetAccess"] = buildStep.hasNetAccess()
        doc["depends"] = [
            "/".join(d.getPackage().getStack())
            for d in buildStep.getArguments()[1:]
        ]
        doc["buildTools"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in buildStep.getTools().items()
            if name in (recipe.toolDepBuild - recipe.toolDepBuildWeak)
        }
        doc["buildToolsWeak"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in buildStep.getTools().items()
            if name in recipe.toolDepBuildWeak
        }
        doc["buildVars"] = {
            k : v for k, v in buildStep.getEnv().items()
            if k in recipe.buildVars
        }
        doc["buildVarsWeak"] = {
            k : v for k, v in buildStep.getEnv().items()
            if k in recipe.buildVarsWeak
        }

    packageStep = package.getPackageStep()
    doc["meta"]["packageVariantId"] = asHexStr(packageStep.getVariantId())
    doc["packageTools"] = {
        name : "/".join(t.getStep().getPackage().getStack())
        for name, t in packageStep.getTools().items()
        if name in (recipe.toolDepPackage - recipe.toolDepPackageWeak)
    }
    doc["packageToolsWeak"] = {
        name : "/".join(t.getStep().getPackage().getStack())
        for name, t in packageStep.getTools().items()
        if name in recipe.toolDepPackageWeak
    }
    doc["packageVars"] = {
        k : v for k, v in packageStep.getEnv().items()
        if k in recipe.packageVars
    }
    doc["packageVarsWeak"] = {
        k : v for k, v in packageStep.getEnv().items()
        if k in recipe.packageVarsWeak
    }
    doc["packageNetAccess"] = packageStep.hasNetAccess()
    doc["fingerprintIf"] = packageStep._isFingerprinted()

    sandbox = packageStep.getSandbox()
    if sandbox and sandbox.isEnabled():
        doc["sandbox"] = "/".join(sandbox.getStep().getPackage().getStack())
    else:
        doc["sandbox"] = None

    return doc

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
    if indent is None:
        style = None
    else:
        style = False

    return "\n".join(
        "--- # {}\n{}".format(package,
                              yaml.dump(doc, default_flow_style=style, indent=indent))
        for package, doc in docs)

def showPackagesJson(docs, indent):
    docs = [ doc for _package, doc in docs ]
    if len(docs) == 1:
        docs = docs[0]
    return json.dumps(docs, indent=indent, sort_keys=True)

def _showPackageFlat(doc, prefix=""):
    ret = []
    if isinstance(doc, dict):
        for k,v in sorted(doc.items()):
            ret.extend(_showPackageFlat(v, prefix+"."+k if prefix else k))
    elif isinstance(doc, list):
        i = 0
        for v in doc:
            ret.extend(_showPackageFlat(v, "{}[{}]".format(prefix, i)))
            i += 1
    else:
        ret = [ "{}={!r}".format(prefix, doc) ]
    return ret

def showPackagesFlat(docs):
    ret = []
    for package, doc in docs:
        ret.append("[" + package + "]")
        ret.extend(_showPackageFlat(doc))
        ret.append("")

    return "\n".join(ret)

def getSegments(indicator):
    mode = False
    start = 0
    end = 1
    ret = []
    for c in indicator[2:]:
        newMode = c != " "
        end += 1
        if mode != newMode:
            ret.append((mode, start, end))
            mode = newMode
            start = end
    ret.append((mode, start, end+1))
    return ret

def diffPackages(left, right, show_common):
    left = [ l+"\n" for l in _showPackageFlat(left) ]
    right = [ l+"\n" for l in _showPackageFlat(right) ]
    diff = [ l for l in difflib.Differ().compare(left, right)
             if not l.startswith("  ") or show_common ]

    # Colorize lines with the intraline information from difflib. A bit
    # complicated because the affected characters are specified with the next
    # line. If the intraline diff is too big then no context is given.
    prevLine = origPrevLine = None
    ret = []
    for l in diff:
        nextLine = origNextLine = l.rstrip()
        if nextLine.startswith("? "):
            if origPrevLine.startswith("+ "):
                codes = { False : ADDED, True : ADDED_HIGHLIGHT }
            elif origPrevLine.startswith("- "):
                codes = { False : DELETED, True : DELETED_HIGHLIGHT }
            else:
                codes = { False : DEFAULT, True : DEFAULT }

            res = ""
            for mode, start, end in getSegments(nextLine):
                res += colorize(origPrevLine[start:end], codes[mode])
            if end < len(origPrevLine):
                res += colorize(origPrevLine[end:], codes[False])
            prevLine = res
            nextLine = origNextLine = None
        elif nextLine.startswith("+ "):
            nextLine = colorize(nextLine, ADDED)
        elif nextLine.startswith("- "):
            nextLine = colorize(nextLine, DELETED)

        if prevLine is not None: ret.append(prevLine)
        prevLine = nextLine
        origPrevLine = origNextLine

    if prevLine is not None:
        ret.append(prevLine)

    return "\n".join(ret)

def doShow(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob show",
        description="Show properties of one or more packages.")
    parser.add_argument('packages', nargs='+', help="(Sub-)packages to query")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=False,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")

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
