# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare package variant inputs and render their differences."""

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

def dumpVariantPair(left, right):
    """Dump packages, treating equal dependency variants as common values."""
    docs = dumpPackage(left), dumpPackage(right)
    for method, field in (("getCheckoutStep", "checkoutTools"),
                          ("getBuildStep", "buildTools"),
                          ("getPackageStep", "packageTools")):
        a, b = getattr(left, method)(), getattr(right, method)()
        ta, tb = a.getTools(), b.getTools()
        for name in ta.keys() & tb.keys():
            if ta[name].getStep().getVariantId()[:20] != tb[name].getStep().getVariantId()[:20]:
                continue
            for key in (field, field + "Weak"):
                if name in docs[0].get(key, {}) and name in docs[1].get(key, {}):
                    docs[1][key][name] = docs[0][key][name]
    a, b = left.getBuildStep(), right.getBuildStep()
    if a.isValid() and b.isValid():
        for i, (x, y) in enumerate(zip(a.getArguments()[1:], b.getArguments()[1:])):
            if x.getVariantId() == y.getVariantId():
                docs[1]["depends"][i] = docs[0]["depends"][i]
    a, b = left.getPackageStep().getSandbox(), right.getPackageStep().getSandbox()
    if a and b and a.getStep().getVariantId() == b.getStep().getVariantId():
        docs[1]["sandbox"] = docs[0]["sandbox"]
    return docs


def renderVariantDifferences(chain, filt=lambda x: x, showCommon=False):
    output = []
    for index, (left, right, field) in enumerate(chain):
        left, right = left.getPackage(), right.getPackage()
        leftDoc, rightDoc = dumpVariantPair(left, right)
        leftPath, rightPath = "/".join(left.getStack()), "/".join(right.getStack())
        output.append("diff {} {}\n--- {}\n+++ {}\n{}".format(
            leftPath, rightPath, leftPath, rightPath,
            diffPackages(filt(leftDoc), filt(rightDoc), showCommon)))
        if field:
            nextLeft, nextRight, _ = chain[index + 1]
            leftName = nextLeft.getPackage().getName()
            rightName = nextRight.getPackage().getName()
            names = "'{}'".format(leftName)
            if leftName != rightName:
                names += " and '{}'".format(rightName)
            output.append("The above package diff was caused by '{}' from package {}:".format(
                field, names))
    return "\n\n".join(output)


def findVariantDifference(left, right):
    """Find the first package with direct differences in variant inputs."""
    visited = set()

    def compare(a, b, recipeOnly=False):
        aid, bid = a.getVariantId(), b.getVariantId()
        if recipeOnly:
            aid, bid = aid[:20], bid[:20]
        key = (aid, bid, recipeOnly)
        if aid == bid or key in visited:
            return None
        visited.add(key)
        pa, pb = a.getPackage(), b.getPackage()
        pending = []
        for sa, sb in zip((pa.getCheckoutStep(), pa.getBuildStep(), pa.getPackageStep()),
                          (pb.getCheckoutStep(), pb.getBuildStep(), pb.getPackageStep())):
            if sa.isValid() != sb.isValid():
                return [(a, b, None)]
            if not sa.isValid():
                continue
            if sa._coreStep.digestEnv != sb._coreStep.digestEnv:
                return [(a, b, None)]
            if sa.getLabel() == 'src':
                scmA = [s.getProperties(False, True) for s in sa.getScmList()]
                scmB = [s.getProperties(False, True) for s in sb.getScmList()]
                if scmA != scmB:
                    return [(a, b, None)]
            ta, tb = sa.getTools(), sb.getTools()
            if set(ta) != set(tb):
                return [(a, b, None)]
            for name in sorted(ta):
                x, y = ta[name].getStep(), tb[name].getStep()
                if x.getVariantId()[:20] == y.getVariantId()[:20]:
                    continue
                if x._coreStep.corePackage.recipe is not y._coreStep.corePackage.recipe:
                    return [(a, b, None)]
                pending.append((x, y, True, {"src": "checkoutTools", "build": "buildTools", "dist": "packageTools"}[sa.getLabel()] + "." + name))
            aa = [x for x in sa.getArguments() if x.isValid() and x.getPackage().getStack() != pa.getStack()]
            ab = [x for x in sb.getArguments() if x.isValid() and x.getPackage().getStack() != pb.getStack()]
            if len(aa) != len(ab):
                return [(a, b, None)]
            for index, (x, y) in enumerate(zip(aa, ab)):
                xid, yid = x.getVariantId(), y.getVariantId()
                if (xid[:20] == yid[:20]) if recipeOnly else (xid == yid):
                    continue
                if (x.getPackage().getName() != y.getPackage().getName() or
                    x._coreStep.corePackage.recipe is not y._coreStep.corePackage.recipe):
                    return [(a, b, None)]
                pending.append((x, y, recipeOnly, "depends[{}]".format(index)))
            if not recipeOnly:
                x = sa.getSandbox() if sa._isFingerprinted() else None
                y = sb.getSandbox() if sb._isFingerprinted() else None
                if bool(x) != bool(y):
                    return [(a, b, None)]
                if x and y:
                    x, y = x.getStep(), y.getStep()
                    if x.getVariantId() == y.getVariantId():
                        continue
                    if x._coreStep.corePackage.recipe is not y._coreStep.corePackage.recipe:
                        return [(a, b, None)]
                    pending.append((x, y, False, "sandbox"))
        for x, y, only, field in pending:
            result = compare(x, y, only)
            if result:
                return [(a, b, field)] + result
        return None

    return compare(left, right)
