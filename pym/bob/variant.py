# SPDX-License-Identifier: GPL-3.0-or-later

import difflib
from .tty import colorize, ADDED, DELETED, DEFAULT, ADDED_HIGHLIGHT, DELETED_HIGHLIGHT
from .utils import asHexStr, dumpFlat

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
        "shared" : package.isShared(),
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
            { k:v for k,v in s.getProperties(False, True).items() if not k.startswith("__") }
            for s in checkoutStep.getScmList()
        ]
        doc["checkoutAssert"] = [
            { k:v for k,v in a.items() if not k.startswith("__") }
            for a in recipe.checkoutAsserts
        ]
        doc["checkoutTools"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in checkoutStep.getTools().items()
            if name in (checkoutStep.toolDep - checkoutStep.toolDepWeak)
        }
        doc["checkoutToolsWeak"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in checkoutStep.getTools().items()
            if name in checkoutStep.toolDepWeak
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
            if name in (buildStep.toolDep - buildStep.toolDepWeak)
        }
        doc["buildToolsWeak"] = {
            name : "/".join(t.getStep().getPackage().getStack())
            for name, t in buildStep.getTools().items()
            if name in buildStep.toolDepWeak
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
        if name in (packageStep.toolDep - packageStep.toolDepWeak)
    }
    doc["packageToolsWeak"] = {
        name : "/".join(t.getStep().getPackage().getStack())
        for name, t in packageStep.getTools().items()
        if name in packageStep.toolDepWeak
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
    left = [ l+"\n" for l in dumpFlat(left) ]
    right = [ l+"\n" for l in dumpFlat(right) ]
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

