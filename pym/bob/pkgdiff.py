# SPDX-License-Identifier: GPL-3.0-or-later

"""Compare package variant inputs and render their differences."""

import difflib
from typing import List, Optional, Tuple
from .input.common import DigestHasher
from .tty import colorize, ADDED, DELETED, DEFAULT, ADDED_HIGHLIGHT, DELETED_HIGHLIGHT
from .utils import asHexStr, dumpFlat


def dumpTools(step, weak):
    toolDep = step.toolDepWeak if weak else (step.toolDep - step.toolDepWeak)
    return {
        name : {
            "package" : "/".join(t.getStep().getPackage().getStack()),
            "path" : t.getPath(),
            "libs" : t.getLibs(),
        }
        for name, t in step.getTools().items()
        if name in toolDep
    }

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
        doc["checkoutTools"] = dumpTools(checkoutStep, False)
        doc["checkoutToolsWeak"] = dumpTools(checkoutStep, True)
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
        doc["buildTools"] = dumpTools(buildStep, False)
        doc["buildToolsWeak"] = dumpTools(buildStep, True)
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
    doc["packageTools"] = dumpTools(packageStep, False)
    doc["packageToolsWeak"] = dumpTools(packageStep, True)
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

def dumpPackagePair(left, right):
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
                    docs[1][key][name]["package"] = docs[0][key][name]["package"]
    a, b = left.getBuildStep(), right.getBuildStep()
    if a.isValid() and b.isValid():
        for i, (x, y) in enumerate(zip(a.getArguments()[1:], b.getArguments()[1:])):
            if x.getVariantId() == y.getVariantId():
                docs[1]["depends"][i] = docs[0]["depends"][i]
    a, b = left.getPackageStep().getSandbox(), right.getPackageStep().getSandbox()
    if a and b and a.getStep().getVariantId() == b.getStep().getVariantId():
        docs[1]["sandbox"] = docs[0]["sandbox"]
    return docs


def renderPackageDiffChain(chain, filt=lambda x: x, showCommon=False):
    output = []
    for index, (left, right, field) in enumerate(chain):
        left, right = left.getPackage(), right.getPackage()
        leftDoc, rightDoc = dumpPackagePair(left, right)
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


def calcPackageDiffRecursive(left, right) -> Optional[List[Tuple["Step", "Step", Optional[str]]]]:
    """Find a chain of packages that explains a recursive variant difference.

    Each entry contains the left and right steps at that point in the chain and
    the field on the preceding package that led to them. The first entry's
    field is ``None``. Return ``None`` if no concrete difference can be found.
    """
    if left.getVariantId() == right.getVariantId():
        return None

    visited = set()

    # The fingerprint part of the Variant-Id of tools is ignored.
    # Consequently, changes to the fingerprint part of tools that are compared
    # must be ignored.
    def compare(a, b, withFingerprint):
        key = a.getVariantId(), b.getVariantId(), withFingerprint
        if key in visited:
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

            if (sa._coreStep.digestEnv != sb._coreStep.digestEnv or
                sa.getDigestScript()   != sb.getDigestScript()):
                return [(a, b, None)]

            ta, tb = sa.getTools(), sb.getTools()
            # Strictly speaking, the tool name is ignored for the Variant-Id
            # calculation.  But different tools will have different Variant-Ids
            # in practice. This way it's easier to compare.
            if set(ta) != set(tb):
                return [(a, b, None)]
            for name in sorted(ta):
                if (ta[name].getPath() != tb[name].getPath() or
                    ta[name].getLibs() != tb[name].getLibs()):
                    return [(a, b, None)]
                x, y = ta[name].getStep(), tb[name].getStep()
                # The fingerprint part of tools is ignored! See CoreStep.getDigest().
                xv = DigestHasher.sliceRecipes(x.getVariantId())
                yv = DigestHasher.sliceRecipes(y.getVariantId())
                if xv == yv:
                    continue
                pending.append((x, y, False,
                    {"src": "checkoutTools",
                     "build": "buildTools",
                     "dist": "packageTools"}[sa.getLabel()] + "." + name))

            # Skip the intra-package arguments in build- and package-steps
            firstArg = 0 if sa.isCheckoutStep() else 1
            aa = [x for x in sa.getArguments()[firstArg:] if x.isValid()]
            ab = [x for x in sb.getArguments()[firstArg:] if x.isValid()]
            if len(aa) != len(ab):
                return [(a, b, None)]
            for index, (x, y) in enumerate(zip(aa, ab)):
                xid, yid = x.getVariantId(), y.getVariantId()
                if not withFingerprint:
                    xid, yid = DigestHasher.sliceRecipes(xid), DigestHasher.sliceRecipes(yid)
                if xid == yid:
                    continue
                pending.append((x, y, withFingerprint, "depends[{}]".format(index)))

            # The sandbox contributes only to the fingerprint part.
            if withFingerprint:
                x = sa.getSandbox() if sa._isFingerprinted() else None
                y = sb.getSandbox() if sb._isFingerprinted() else None
                if bool(x) != bool(y):
                    return [(a, b, None)]
                if x and y:
                    x, y = x.getStep(), y.getStep()
                    if x.getVariantId() != y.getVariantId():
                        pending.append((x, y, True, "sandbox"))

        for x, y, withFingerprint, field in pending:
            result = compare(x, y, withFingerprint)
            if result:
                return [(a, b, field)] + result
        return None

    return compare(left, right, True)
