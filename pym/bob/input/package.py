# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Packages and their checkout/build/package steps."""

from ..errors import ParseError
from ..languages import getLanguage, ScriptLanguage
from ..stringparser import Env
from ..utils import asHexStr, joinScripts, hashString, isAbsPath
from .checkout import CheckoutAssert, Scm
from .common import DigestHasher, isPrefixPath
from .core import CoreItem, CoreRef
from itertools import chain
import hashlib
import struct


class CoreStep(CoreItem):
    __slots__ = ( "corePackage", "digestEnv", "env", "args",
        "providedEnv", "providedTools", "providedDeps", "providedSandbox", "providedInterpreters",
        "variantId", "deterministic", "isValid", "toolDep", "toolDepWeak",
        "auditFileNames" )

    def __init__(self, corePackage, isValid, deterministic, digestEnv, env, args,
                 toolDep, toolDepWeak, auditFileNames):
        self.corePackage = corePackage
        self.isValid = isValid
        self.digestEnv = digestEnv.detach()
        self.env = env.detach()
        self.args = args
        self.toolDep = toolDep
        self.toolDepWeak = toolDepWeak
        self.deterministic = deterministic and all(
            arg.isDeterministic() for arg in self.getAllDepCoreSteps())
        self.variantId = self.getDigest(lambda coreStep: coreStep.variantId)
        self.providedEnv = {}
        self.providedTools = {}
        self.providedDeps = []
        self.providedSandbox = None
        self.providedInterpreters = {}
        self.auditFileNames = auditFileNames

    def getPreRunCmds(self):
        return []

    def getJenkinsPreRunCmds(self):
        return []

    def getSetupScript(self):
        raise NotImplementedError

    def getMainScript(self):
        raise NotImplementedError

    def getPostRunCmds(self):
        return []

    def getDigestScript(self):
        raise NotImplementedError

    def getUpdateScript(self):
        return ""

    def getIncludedFiles(self):
        raise NotImplementedError

    def getLabel(self):
        raise NotImplementedError

    def isDeterministic(self):
        return self.deterministic

    def isUpdateDeterministic(self):
        return True

    def isCheckoutStep(self):
        return False

    def isBuildStep(self):
        return False

    def isPackageStep(self):
        return False

    def getTools(self):
        if self.isValid:
            toolKeys = self.toolDep
            return { name : tool for name,tool in self.corePackage.tools.items()
                                 if name in toolKeys }
        else:
            return {}

    def getSandbox(self):
        sandbox = self.corePackage.sandbox
        if sandbox and sandbox.enabled and self.isValid:
            return sandbox
        else:
            return None

    def getInterpreter(self):
        if self.isValid:
            return self.corePackage.interpreters.get(self.corePackage.recipe.scriptLanguage.index)
        else:
            return None

    def getAllDepCoreSteps(self):
        sandbox = self.getSandbox()
        interp = self.getInterpreter()
        return [ a.refGetDestination() for a in self.args ] + \
            [ d.coreStep for n,d in sorted(self.getTools().items()) ] + (
            [ sandbox.coreStep] if sandbox else []) + (
            [ interp.coreStep ] if interp else [])

    def getDigest(self, calculate):
        h = DigestHasher()
        if self.isFingerprinted() and self.getSandbox():
            # Add the full variant-id (including fingerprint part!) to the
            # fingerprint of this package. This is important to make separate
            # builds for separate sandboxes, including sandboxes that are
            # itself fingerprinted.
            h.fingerprint(calculate(self.getSandbox().coreStep))
        h.update(b'\x00' * 20) # historically the sandbox digest, see sandboxInvariant policy pre-0.25
        script = self.getDigestScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        tools = self.getTools()
        h.update(struct.pack("<I", len(tools)))
        for (name, tool) in sorted(tools.items(), key=lambda t: t[0]):
            h.update(DigestHasher.sliceRecipes(calculate(tool.coreStep)))
            h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
            h.update(tool.path.encode("utf8"))
            for l in tool.libs:
                h.update(struct.pack("<I", len(l)))
                h.update(l.encode('utf8'))
        h.update(struct.pack("<I", len(self.digestEnv)))
        for (key, val) in sorted(self.digestEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        args = [ arg for arg in (a.refGetDestination() for a in self.args) if arg.isValid ]
        h.update(struct.pack("<I", len(args)))
        for arg in args:
            arg = calculate(arg)
            h.update(DigestHasher.sliceRecipes(arg))
            h.fingerprint(DigestHasher.sliceHost(arg))
        return h.digest()

    def getResultId(self):
        h = hashlib.sha1()
        h.update(self.variantId)
        # Include invalid dependencies. They are needed for traversing dummy
        # packages without a buildScript in path queries. Valid dependencies
        # are already included in the variantId.
        args = [ arg for arg in (a.refGetDestination() for a in self.args) if not arg.isValid ]
        h.update(struct.pack("<I", len(args)))
        for arg in args:
            h.update(arg.getResultId())
        # Include used sandbox. Prevents merging of identical packages that
        # are defined under different sandboxes.
        sandbox = self.getSandbox()
        if sandbox:
            h.update(sandbox.coreStep.variantId)
            h.update(struct.pack("<I", len(sandbox.paths)))
            for p in sandbox.paths:
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        # Include weak tools for the same reason as above.
        weakTools = self.toolDepWeak
        for (name, tool) in sorted(self.getTools().items(), key=lambda t: t[0]):
            if name in weakTools:
                h.update(tool.coreStep.variantId)
                h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
                h.update(tool.path.encode("utf8"))
                for l in tool.libs:
                    h.update(struct.pack("<I", len(l)))
                    h.update(l.encode('utf8'))
        # providedEnv
        h.update(struct.pack("<I", len(self.providedEnv)))
        for (key, val) in sorted(self.providedEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        # providedTools
        providedTools = self.providedTools
        h.update(struct.pack("<I", len(providedTools)))
        for (name, tool) in sorted(providedTools.items()):
            h.update(struct.pack("<I", len(name)))
            h.update(name.encode("utf8"))
            h.update(tool.resultId)
        # provideDeps
        providedDeps = self.providedDeps
        h.update(struct.pack("<I", len(providedDeps)))
        for dep in providedDeps:
            h.update(dep.refGetDestination().variantId)
        # sandbox
        providedSandbox = self.providedSandbox
        if providedSandbox:
            h.update(providedSandbox.resultId)
        else:
            h.update(b'\x00' * 20)
        # providedInterpreters
        providedInterpreters = self.providedInterpreters
        for interp in (providedInterpreters.get(lang) for lang in ScriptLanguage):
            h.update(interp.resultId if interp is not None else b'\x00' * 20)
        # Add package name if aliased
        pkgName = self.corePackage.packageName
        if pkgName is not None:
            h.update(pkgName.encode('utf8'))
        # Add shared status. Actually only relevant for package step but it
        # doesn't make sense to differentiate here.
        h.update(b'\x01' if self.corePackage.isShared else b'\x00')

        return h.digest()

    @property
    def fingerprintMask(self):
        raise NotImplementedError

    def isFingerprinted(self):
        return self.fingerprintMask != 0

    @property
    def jobServer(self):
        return self.corePackage.recipe.jobServer

class Step:
    """Represents the smallest unit of execution of a package.

    A step is what gets actually executed when building packages.

    Steps can be compared and sorted. This is done based on the Variant-Id of
    the step. See :meth:`bob.input.Step.getVariantId` for details.
    """

    def __init__(self, coreStep, package, pathsConfig):
        self._coreStep = coreStep
        self.__package = package
        self.__pathsConfig = pathsConfig

    def __repr__(self):
        return "Step({}, {}, {})".format(self.getLabel(), "/".join(self.getPackage().getStack()), asHexStr(self.getVariantId()))

    def __hash__(self):
        return hash(self._coreStep.variantId)

    def __lt__(self, other):
        return self._coreStep.variantId < other._coreStep.variantId

    def __le__(self, other):
        return self._coreStep.variantId <= other._coreStep.variantId

    def __eq__(self, other):
        return self._coreStep.variantId == other._coreStep.variantId

    def __ne__(self, other):
        return self._coreStep.variantId != other._coreStep.variantId

    def __gt__(self, other):
        return self._coreStep.variantId > other._coreStep.variantId

    def __ge__(self, other):
        return self._coreStep.variantId >= other._coreStep.variantId

    def getPreRunCmds(self):
        return self._coreStep.getPreRunCmds()

    def getJenkinsPreRunCmds(self):
        return self._coreStep.getJenkinsPreRunCmds()

    def getScript(self):
        """Return a single big script of the whole step.

        Besides considerations of special backends (such as Jenkins) this
        script is what should be executed to build this step."""
        return joinScripts([self.getSetupScript(), self.getMainScript()],
            self.getPackage().getRecipe().scriptLanguage.glue) or ""

    def getJenkinsScript(self):
        import warnings
        warnings.warn("getJenkinsScript is deprecated", DeprecationWarning)
        """Return the relevant parts as shell script that have no Jenkins plugin.

        Deprecated. Returns the same script as bob.input.Step.getScript()
        """
        return self.getScript()

    def getSetupScript(self):
        return self._coreStep.getSetupScript()

    def getMainScript(self):
        return self._coreStep.getMainScript()

    def getPostRunCmds(self):
        return self._coreStep.getPostRunCmds()

    def getDigestScript(self):
        """Return a long term stable script.

        The digest script will not be executed but is the basis to calculate if
        the step has changed. In case of the checkout step the involved SCMs will
        return a stable representation of *what* is checked out and not the real
        script of *how* this is done.
        """
        return self._coreStep.getDigestScript()

    def getUpdateScript(self):
        return self._coreStep.getUpdateScript()

    def isUpdateDeterministic(self):
        return self._coreStep.isUpdateDeterministic()

    def getIncludedFiles(self):
        return self._coreStep.getIncludedFiles()

    def isDeterministic(self):
        """Return whether the step is deterministic.

        Checkout steps that have a script are considered indeterministic unless
        the recipe declares it otherwise (checkoutDeterministic). Then the SCMs
        are checked if they all consider themselves deterministic. Build and
        package steps are always deterministic.

        The determinism is defined recursively for all arguments, tools and the
        sandbox of the step too. That is, the step is only deterministic if all
        its dependencies and this step itself is deterministic.
        """
        return self._coreStep.isDeterministic()

    def isValid(self):
        """Returns True if this step is valid, False otherwise."""
        return self._coreStep.isValid

    def isCheckoutStep(self):
        """Return True if this is a checkout step."""
        return self._coreStep.isCheckoutStep()

    def isBuildStep(self):
        """Return True if this is a build step."""
        return self._coreStep.isBuildStep()

    def isPackageStep(self):
        """Return True if this is a package step."""
        return self._coreStep.isPackageStep()

    def getPackage(self):
        """Get Package object that is the parent of this Step."""
        return self.__package

    def getVariantId(self):
        """Return Variant-Id of this Step.

        The Variant-Id is used to distinguish different packages or multiple
        variants of a package. Each Variant-Id need only be built once but
        subsequent builds might yield different results (e.g. when building
        from branches)."""
        return self._coreStep.variantId

    def getSandbox(self):
        """Return Sandbox used in this Step.

        Returns a Sandbox object or None if this Step is built without one.
        """
        sandbox = self.__package._getSandboxRaw()
        if sandbox and sandbox.isEnabled() and self._coreStep.isValid:
            return sandbox
        else:
            return None

    def getLabel(self):
        """Return path label for step.

        This is currently defined as "src", "build" and "dist" for the
        respective steps.
        """
        return self._coreStep.getLabel()

    def getWorkspacePath(self):
        """Return the workspace path of the step.

        The workspace path represents the location of the step in the user's
        workspace. When building in a sandbox this path is not passed to the
        script but the one from getExecPath() instead.
        """
        if self.isValid():
            return self.__pathsConfig.pathFormatter(self, self.__package.getPluginStates())
        else:
            return "/invalid/workspace/path/of/{}".format(self.__package.getName())

    def stablePaths(self):
        return self.__pathsConfig.stablePaths

    def getTools(self):
        """Get dictionary of tools.

        The dict maps the tool name to a :class:`bob.input.Tool`.
        """
        if self._coreStep.isValid:
            toolKeys = self._coreStep.toolDep
            return { name : tool for name, tool in self.__package._getAllTools().items()
                                 if name in toolKeys }
        else:
            return {}

    def getArguments(self):
        """Get list of all inputs for this Step.

        The arguments are passed as absolute paths to the script starting from $1.
        """
        p = self.__package
        refCache = {}
        return [ a.refDeref(p.getStack(), p._getInputTools(), p._getInputSandboxRaw(),
                            p._getInputInterpreters(), self.__pathsConfig, refCache)
                    for a in self._coreStep.args ]

    def getAllDepSteps(self):
        """Get all dependent steps of this Step.

        This includes the arguments of the Step as well as the packages of the
        used tools, the sandbox and the interpreter.
        """
        sandbox = self.getSandbox()
        interpreter = self.getInterpreter()
        return self.getArguments() + [ d.step for n,d in sorted(self.getTools().items()) ] + (
            [sandbox.getStep()] if sandbox else []) + (
            [interpreter.getStep()] if interpreter else [])

    def getInterpreter(self):
        return self.__package._getInterpreter()

    def getEnv(self):
        """Return dict of environment variables."""
        return self._coreStep.env

    def doesProvideTools(self):
        """Return True if this step provides at least one tool."""
        return bool(self._coreStep.providedTools)

    def isShared(self):
        """Returns True if the result of the Step should be shared globally.

        The exact behaviour of a shared step/package depends on the build
        backend. In general a shared package means that the result is put into
        some shared location where it is likely that the same result is needed
        again.
        """
        return False

    def isRelocatable(self):
        """Returns True if the step is relocatable."""
        return False

    def jobServer(self):
        """Returns True if the jobserver should be used to schedule
        builds for this step."""
        return self._coreStep.jobServer()

    def _getProvidedDeps(self):
        p = self.__package
        refCache = {}
        return [ a.refDeref(p.getStack(), p._getInputTools(), p._getInputSandboxRaw(),
                            p._getInputInterpreters(), self.__pathsConfig, refCache)
                    for a in self._coreStep.providedDeps ]

    def _isFingerprinted(self):
        return self._coreStep.isFingerprinted()

    def _getFingerprintScript(self):
        """Generate final fingerprint script.

        The used fingerprint scripts of the tools and the recipe/classes are
        finally stitched together based on the mask that was calculated in
        Recipt.resolveClasses(). For each possible entry there are two bits in
        the mask: bit 0 is set if the script is taken unconditionally and bit 1
        is set if the script is taken if not empty.
        """
        if not self._coreStep.isFingerprinted():
            return ""

        package = self.__package
        recipe = package.getRecipe()
        packageStep = package.getPackageStep()
        mask = self._coreStep.fingerprintMask
        tools = packageStep.getTools()
        scriptsAndVars = chain(
            ((({}, []) if t is None else (t.fingerprintScript, t.fingerprintVars))
                for t in (tools.get(k) for k in sorted(packageStep._coreStep.toolDep))),
            zip(recipe.fingerprintScriptList, recipe.fingerprintVarsList))
        ret = []
        varSet = set()
        for s,v in scriptsAndVars:
            s = s.get(recipe.scriptLanguage.index)
            if (mask & 1) or ((mask & 2) and s):
                ret.append(s)
                varSet.update(v)
            mask >>= 2
        env = self.getEnv()
        env = { k : v for k,v in env.items() if k in varSet }
        return recipe.scriptLanguage.mangleFingerprints(ret, env)

    @property
    def toolDep(self):
        return self._coreStep.toolDep

    @property
    def toolDepWeak(self):
        return self._coreStep.toolDepWeak

    def getAuditFileNames(self):
        return self._coreStep.auditFileNames

class CoreCheckoutStep(CoreStep):
    __slots__ = ( "scmList", "__checkoutUpdateIf", "__checkoutUpdateDeterministic", "__checkoutAsserts" )

    def __init__(self, corePackage, checkout=None, checkoutSCMs=[],
                 fullEnv=Env(), digestEnv=Env(), env=Env(), args=[],
                 checkoutUpdateIf=[], checkoutUpdateDeterministic=True,
                 toolDep=set(), toolDepWeak=set(), auditFileNames={}):
        if checkout:
            recipeSet = corePackage.recipe.getRecipeSet()
            overrides = recipeSet.scmOverrides()
            self.scmList = [ Scm(scm, fullEnv, overrides, recipeSet)
                for scm in checkoutSCMs
                if fullEnv.evaluate(scm.get("if"), "checkoutSCM") ]
            isValid = (checkout[1] is not None) or bool(self.scmList)

            # Validate all SCM paths. Only relative paths are allowed. If they
            # overlap, the following rules must apply:
            #  1. Deeper paths must be checked out later.
            #     -> required for initial checkout to work
            #  2. A Jenkins native SCM (e.g. git) must not overlap with a
            #     previous non-native SCM.
            #     -> required because SCM plugins are running before scripts on
            #        Jenkins jobs
            knownPaths = []
            for s in self.scmList:
                p = s.getDirectory()
                if isAbsPath(p):
                    raise ParseError("SCM paths must be relative! Offending path: " + p)
                for knownPath, knownHasJenkins in knownPaths:
                    if isPrefixPath(p, knownPath):
                        raise ParseError("SCM path '{}' obstructs '{}'."
                                            .format(p, knownPath),
                                         help="Nested SCMs must be specified in a upper-to-lower directory order.")
                    if isPrefixPath(knownPath, p) and s.hasJenkinsPlugin() and not knownHasJenkins:
                        raise ParseError("SCM path '{}' cannot be checked out after '{}' in Jenkins jobs"
                                            .format(p, knownPath),
                                         help="SCM plugins always run first in a Jenkins job but the SCM in '{}' does not have a native Jenkins plugin."
                                                .format(knownPath))
                knownPaths.append((p, s.hasJenkinsPlugin()))
        else:
            isValid = False
            self.scmList = []

        self.__checkoutAsserts = [ CheckoutAssert (a, fullEnv) for a in corePackage.recipe.checkoutAsserts ]
        self.__checkoutUpdateIf = checkoutUpdateIf
        self.__checkoutUpdateDeterministic = checkoutUpdateDeterministic
        deterministic = corePackage.recipe.checkoutDeterministic
        super().__init__(corePackage, isValid, deterministic, digestEnv, env, args, toolDep,
                         toolDepWeak, auditFileNames)

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
        ret = CheckoutStep(self, package, pathsConfig)
        package._setCheckoutStep(ret)
        return ret

    def getLabel(self):
        return "src"

    def isDeterministic(self):
        return super().isDeterministic() and all(s.isDeterministic() for s in self.scmList)

    def isUpdateDeterministic(self):
        return self.__checkoutUpdateDeterministic

    def hasLiveBuildId(self):
        return super().isDeterministic() and all(s.hasLiveBuildId() for s in self.scmList)

    def isCheckoutStep(self):
        return True

    def getPreRunCmds(self):
        return [s.getProperties(False) for s in self.scmList]

    def getJenkinsPreRunCmds(self):
        return [ s.getProperties(True) for s in self.scmList if not s.hasJenkinsPlugin() ]

    def getSetupScript(self):
        return self.corePackage.recipe.checkoutSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.checkoutMainScript

    def getPostRunCmds(self):
        return [s.getProperties() for s in self.__checkoutAsserts]

    def getDigestScript(self):
        if self.isValid:
            recipe = self.corePackage.recipe
            return "\n".join([s.asDigestScript() for s in self.scmList]
                    + [recipe.checkoutDigestScript]
                    + [s.asDigestScript() for s in self.__checkoutAsserts])
        else:
            return None

    def getUpdateScript(self):
        glue = getLanguage(self.corePackage.recipe.scriptLanguage.index).glue
        return joinScripts(self.__checkoutUpdateIf, glue) or ""

    def getIncludedFiles(self):
        return self.corePackage.recipe.checkoutIncludedFiles

    @property
    def fingerprintMask(self):
        return 0

class CheckoutStep(Step):
    def getJenkinsXml(self, config):
        return [ s.asJenkins(self.getWorkspacePath(), config)
                 for s in self._coreStep.scmList if s.hasJenkinsPlugin() ]

    def getScmList(self):
        return self._coreStep.scmList

    def getScmDirectories(self):
        dirs = {}
        for s in self._coreStep.scmList:
            dirs[s.getDirectory()] = (hashString(s.asDigestScript()), s.getProperties(False))
        return dirs

    def hasLiveBuildId(self):
        """Check if live build-ids are supported.

        This must be supported by all SCMs. Additionally the checkout script
        must be deterministic.
        """
        return self._coreStep.hasLiveBuildId()

    def hasNetAccess(self):
        return True

class CoreBuildStep(CoreStep):
    __slots__ = ["fingerprintMask"]

    def __init__(self, corePackage, script=(None, None, None, {}), digestEnv=Env(),
                 env=Env(), args=[], fingerprintMask=0, toolDep=set(), toolDepWeak=set(),
                 auditFileNames={}):
        isValid = script[1] is not None
        self.fingerprintMask = fingerprintMask
        super().__init__(corePackage, isValid, True, digestEnv, env, args, toolDep, toolDepWeak,
                         auditFileNames)

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
        ret = BuildStep(self, package, pathsConfig)
        package._setBuildStep(ret)
        return ret

    def getLabel(self):
        return "build"

    def isBuildStep(self):
        return True

    def getSetupScript(self):
        return self.corePackage.recipe.buildSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.buildMainScript

    def getDigestScript(self):
        return self.corePackage.recipe.buildDigestScript

    def getIncludedFiles(self):
        return self.corePackage.recipe.buildIncludedFiles

class BuildStep(Step):

    def hasNetAccess(self):
        return self.getPackage().getRecipe().buildNetAccess or any(
            t.getNetAccess() for t in self.getTools().values())

class CorePackageStep(CoreStep):
    __slots__ = ["fingerprintMask"]

    def __init__(self, corePackage, script=(None, None, None, {}), digestEnv=Env(), env=Env(), args=[],
                 fingerprintMask=0, toolDep=set(), toolDepWeak=set(), auditFileNames={}):
        isValid = script[1] is not None
        self.fingerprintMask = fingerprintMask
        super().__init__(corePackage, isValid, True, digestEnv, env, args, toolDep, toolDepWeak,
                         auditFileNames)

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
        ret = PackageStep(self, package, pathsConfig)
        package._setPackageStep(ret)
        return ret

    def getLabel(self):
        return "dist"

    def isPackageStep(self):
        return True

    def getSetupScript(self):
        return self.corePackage.recipe.packageSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.packageMainScript

    def getDigestScript(self):
        return self.corePackage.recipe.packageDigestScript

    def getIncludedFiles(self):
        return self.corePackage.recipe.packageIncludedFiles

class PackageStep(Step):

    def isShared(self):
        """Determine if the PackageStep be shared.

        Requires the recipe to be marked as shared and the result must be
        position independent.
        """
        return self.getPackage().isShared() and self.isRelocatable()

    def isRelocatable(self):
        """Returns True if the package step is relocatable."""
        return self.getPackage().isRelocatable()

    def hasNetAccess(self):
        return self.getPackage().getRecipe().packageNetAccess or any(
            t.getNetAccess() for t in self.getTools().values())

class CorePackageInternal(CoreItem):
    __slots__ = []
    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        return (inputTools, inputSandbox, inputInterpreters)

corePackageInternal = CorePackageInternal()

class CorePackage:
    __slots__ = ("recipe", "internalRef", "directDepSteps", "injectedDepSteps",
        "states", "tools", "sandbox", "interpreters", "checkoutStep", "buildStep", "packageStep",
        "pkgId", "metaEnv", "packageName", "isShared", "name")

    def __init__(self, recipe, tools, diffTools, sandbox, diffSandbox, interpreters, diffInterpreters,
                 directDepSteps, injectedDepSteps, states, pkgId, metaEnv,
                 packageName, isShared):
        self.recipe = recipe
        self.tools = tools
        self.sandbox = sandbox
        self.interpreters = interpreters
        self.internalRef = CoreRef(corePackageInternal, [], diffTools, diffSandbox, diffInterpreters)
        self.directDepSteps = directDepSteps
        self.injectedDepSteps = injectedDepSteps
        self.states = states
        self.pkgId = pkgId
        self.metaEnv = metaEnv
        self.packageName = packageName
        self.isShared = isShared
        self.name = recipe.getPackageName() if packageName is None else packageName

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig):
        tools, sandbox, interpreters = self.internalRef.refDeref(stack, inputTools, inputSandbox,
                                                                 inputInterpreters, pathsConfig)
        return Package(self, stack, pathsConfig, inputTools, tools, inputSandbox, sandbox,
                       inputInterpreters, interpreters)

    def createCoreCheckoutStep(self, checkout, checkoutSCMs, fullEnv, digestEnv,
                               env, args, checkoutUpdateIf, checkoutUpdateDeterministic,
                               toolDep, toolDepWeak, auditFileNames):
        ret = self.checkoutStep = CoreCheckoutStep(self, checkout, checkoutSCMs,
            fullEnv, digestEnv, env, args, checkoutUpdateIf, checkoutUpdateDeterministic,
            toolDep, toolDepWeak, auditFileNames)
        return ret

    def createInvalidCoreCheckoutStep(self):
        ret = self.checkoutStep = CoreCheckoutStep(self)
        return ret

    def createCoreBuildStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak, auditFileNames):
        ret = self.buildStep = CoreBuildStep(self, script, digestEnv, env, args,
                                             fingerprintMask, toolDep, toolDepWeak, auditFileNames)
        return ret

    def createInvalidCoreBuildStep(self, args):
        ret = self.buildStep = CoreBuildStep(self, args=args)
        return ret

    def createCorePackageStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak, auditFileNames):
        ret = self.packageStep = CorePackageStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak, auditFileNames)
        return ret

    def getCorePackageStep(self):
        return self.packageStep

    def getName(self):
        """Name of the package"""
        return self.name

    def getMetaEnv(self):
        return self.metaEnv

    @property
    def jobServer(self):
        return self.recipe.jobServer()

    def isAlias(self):
        return self.packageName is not None

class Package(object):
    """Representation of a package that was created from a recipe.

    Usually multiple packages will be created from a single recipe. This is
    either due to multiple upstream recipes or different variants of the same
    package. This does not preclude the possibility that multiple Package
    objects describe exactly the same package (read: same Variant-Id). It is
    the responsibility of the build backend to detect this and build only one
    package.
    """

    def __init__(self, corePackage, stack, pathsConfig, inputTools, tools, inputSandbox, sandbox,
                 inputInterpreters, interpreters):
        self.__corePackage = corePackage
        self.__stack = stack
        self.__pathsConfig = pathsConfig
        self.__inputTools = inputTools
        self.__tools = tools
        self.__inputSandbox = inputSandbox
        self.__sandbox = sandbox
        self.__inputInterpreters = inputInterpreters
        self.__interpreters = interpreters

    def __eq__(self, other):
        return isinstance(other, Package) and (self.__stack == other.__stack)

    def _getId(self):
        """The package-Id is uniquely representing every package variant.

        On the package level there might be more dependencies than on the step
        level. Meta variables are usually unused and also do not contribute to
        the variant-id. The package-id still guarantees to not collide in these
        cases. OTOH there can be identical packages with different ids, though
        it should be an unusual case.
        """
        return self.__corePackage.pkgId

    def _getInputTools(self):
        return self.__inputTools

    def _getAllTools(self):
        return self.__tools

    def _getInputSandboxRaw(self):
        return self.__inputSandbox

    def _getSandboxRaw(self):
        return self.__sandbox

    def _getInputInterpreters(self):
        return self.__inputInterpreters

    def _getInterpreter(self):
        return self.__interpreters.get(self.__corePackage.recipe.scriptLanguage.index)

    def _getCorePackage(self):
        return self.__corePackage

    def getName(self):
        """Name of the package"""
        return self.__corePackage.name

    def getMetaEnv(self):
        """meta variables of package"""
        return self.__corePackage.getMetaEnv()

    def getStack(self):
        """Returns the recipe processing stack leading to this package.

        The method returns a list of package names. The first entry is a root
        recipe and the last entry is this package."""
        return self.__stack

    def getRecipe(self):
        """Return Recipe object that was the template for this package."""
        return self.__corePackage.recipe

    def getDirectDepSteps(self):
        """Return list of the package steps of the declared dependencies.

        Declared (or direct) dependencies are the ones that are named
        explicitly in the ``depends`` section of the recipe. The order of the
        items is preserved from the recipe.
        """
        refCache = {}
        return [ d.refDeref(self.__stack, self.__inputTools, self.__inputSandbox,
                            self.__inputInterpreters, self.__pathsConfig, refCache)
                    for d in self.__corePackage.directDepSteps ]

    def getInjectedDepSteps(self):
        """Return list of the package steps of the injected dependencies.

        Injected dependencies are dependencies that were provided by upstream
        recipes through ``provideDeps``. They are not named in the recipe.
        """
        refCache = {}
        return [ d.refDeref(self.__stack, self.__inputTools, self.__inputSandbox,
                            self.__inputInterpreters, self.__pathsConfig, refCache)
                    for d in self.__corePackage.injectedDepSteps ]

    def getIndirectDepSteps(self):
        """Return list of the package steps of the injected dependencies.

        .. deprecated:: 1.3
           Use :meth:`bob.input.Package.getInjectedDepSteps` instead.
        """
        import warnings
        warnings.warn("getIndirectDepSteps is deprecated. Use getInjectedDepSteps instead.",
                      DeprecationWarning, stacklevel=2)
        return self.getInjectedDepSteps()

    def getAllDepSteps(self):
        """Return list of all dependencies of the package.

        This list includes all declared and injected dependencies.
        Additionally ambient dependencies (the used sandbox, tools and
        interpreter) are included too.
        """
        allDeps = set(self.getDirectDepSteps())
        allDeps |= set(self.getInjectedDepSteps())
        if self.__sandbox and self.__sandbox.isEnabled():
            allDeps.add(self.__sandbox.getStep())
        for i in self.getPackageStep().getTools().values(): allDeps.add(i.getStep())
        interp = self._getInterpreter()
        if interp:
            allDeps.add(interp.getStep())
        return sorted(allDeps)

    def _setCheckoutStep(self, checkoutStep):
        self.__checkoutStep = checkoutStep

    def getCheckoutStep(self):
        """Return the checkout step of this package."""
        try:
            ret = self.__checkoutStep
        except AttributeError:
            ret = self.__checkoutStep = CheckoutStep(self.__corePackage.checkoutStep,
                self, self.__pathsConfig)
        return ret

    def _setBuildStep(self, buildStep):
        self.__buildStep = buildStep

    def getBuildStep(self):
        """Return the build step of this package."""
        try:
            ret = self.__buildStep
        except AttributeError:
            ret = self.__buildStep = BuildStep(self.__corePackage.buildStep,
                self, self.__pathsConfig)
        return ret

    def _setPackageStep(self, packageStep):
        self.__packageStep = packageStep

    def getPackageStep(self):
        """Return the package step of this package."""
        try:
            ret = self.__packageStep
        except AttributeError:
            ret = self.__packageStep = PackageStep(self.__corePackage.packageStep,
                self, self.__pathsConfig)
        return ret

    def getPluginStates(self):
        """Return state trackers of this package.

        :return: All plugin defined state trackers of the package
        :rtype: Mapping[str, :class:`bob.input.PluginState`]
        """
        return self.__corePackage.states

    def isRelocatable(self):
        """Returns True if the packages is relocatable."""
        return self.__corePackage.recipe.isRelocatable()

    def isAlias(self):
        """Returns True if the package name is an alias.

        Usually, the package name is based on the recipe name with some
        optional suffix due to (possibly nested) multiPackage(s). In case of
        global or per-dependency alias names, any other name may be used,
        though. In this case, ``isAlias()`` returns true and ``getName()`` does
        not equal ``getRecipe().getPackageName()`` as it normally does.
        """
        return self.__corePackage.isAlias()

    def isShared(self):
        # No public API. Intentionally undocumented.
        return self.__corePackage.isShared
