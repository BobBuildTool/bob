# Bob build tool
# Copyright (C) 2022  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import hashlib
import os.path
import struct
from abc import ABC, abstractmethod

from .input import DigestHasher
from .languages import getLanguage, ScriptLanguage
from .scm import getScm, ScmOverride
from .state import BobState
from .utils import asHexStr, getPlatformTag

# Fully dumped: Package-/Build-/Checkout-Step of built package
# Partially dumped: everything else
#   packageStep: variantId, workspacePath, package, isRelocatable, sandbox
#   package: packageStep, recipe, name
#   sandbox: None/!None

class AbstractIR(ABC):

    @abstractmethod
    def mungeStep(self, step):
        return step

    @abstractmethod
    def mungePackage(self, package):
        return package

    @abstractmethod
    def mungeRecipe(self, recipe):
        return recipe

    @abstractmethod
    def mungeSandbox(self, sandbox):
        return sandbox

    @abstractmethod
    def mungeTool(self, tool):
        return tool

    @abstractmethod
    def mungeRecipeSet(self, recipeSet):
        return recipeSet

class StepIR(AbstractIR):

    @classmethod
    def fromStep(cls, step, graph, partial=False):
        self = cls()
        self.__data = {}
        self.__data['partial'] = partial
        self.__data['variantId'] = step.getVariantId().hex()
        self.__data['package'] = graph.addPackage(step.getPackage(), partial)
        self.__data['valid'] = step.isValid()
        self.__data['workspacePath'] = step.getWorkspacePath()
        self.__data['isCheckoutStep'] = step.isCheckoutStep()
        self.__data['isBuildStep'] = step.isBuildStep()
        self.__data['isPackageStep']  = step.isPackageStep()
        self.__data['isRelocatable'] = step.isRelocatable()
        self.__data['isShared'] = step.isShared()
        self.__data['sandbox'] = (
            graph.addSandbox(step.getSandbox(False)),
            graph.addSandbox(step.getSandbox(True))
        )

        if not partial:
            self.__data['isFingerprinted'] = step._isFingerprinted()
            self.__data['digestScript'] = step.getDigestScript()
            self.__data['tools'] = { name : graph.addTool(tool) for name, tool in step.getTools().items() }
            self.__data['arguments'] = [ graph.addStep(a, a.getPackage() != step.getPackage()) for a in step.getArguments() ]
            self.__data['allDepSteps'] = (
                [ graph.addStep(a, a.getPackage() != step.getPackage()) for a in step.getAllDepSteps(False) ],
                [ graph.addStep(a, a.getPackage() != step.getPackage()) for a in step.getAllDepSteps(True) ]
            )
            self.__data['env'] = step.getEnv()
            if self.JENKINS:
                self.__data['preRunCmds'] = step.getJenkinsPreRunCmds()
            else:
                self.__data['preRunCmds'] = step.getPreRunCmds()
            self.__data['postRunCmds'] = step.getPostRunCmds()
            self.__data['setupScript'] = step.getSetupScript()
            self.__data['mainScript'] = step.getMainScript()
            self.__data['updateScript'] = step.getUpdateScript()
            self.__data['fingerprintScript'] = step._getFingerprintScript()
            self.__data['jobServer'] = step.jobServer()
            self.__data['label'] = step.getLabel()
            self.__data['isDeterministic'] = step.isDeterministic()
            self.__data['isUpdateDeterministic'] = step.isUpdateDeterministic()
            self.__data['hasNetAccess'] = step.hasNetAccess()
            if self.__data['isCheckoutStep']:
                self.__data['hasLiveBuildId'] = step.hasLiveBuildId()
                self.__data['scmList'] = [
                    (s.getProperties(self.JENKINS), [ o.__getstate__() for o in s.getActiveOverrides()])
                    for s in step.getScmList()
                ]
                self.__data['scmDirectories'] = { d : (h.hex(), p) for (d, (h, p)) in step.getScmDirectories().items() }
            self.__data['sandboxVariantId'] = step._getSandboxVariantId().hex()
            self.__data['toolKeysWeak'] = sorted(step._coreStep._getToolKeysWeak())
            self.__data['digestEnv'] = step._coreStep.digestEnv

        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    def __hash__(self):
        return hash(self.__data['variantId'])

    def __lt__(self, other):
        return self.getVariantId() < other.getVariantId()

    def __le__(self, other):
        return self.getVariantId() <= other.getVariantId()

    def __eq__(self, other):
        return self.getVariantId() == other.getVariantId()

    def __ne__(self, other):
        return self.getVariantId() != other.getVariantId()

    def __gt__(self, other):
        return self.getVariantId() > other.getVariantId()

    def __ge__(self, other):
        return self.getVariantId() >= other.getVariantId()

    @property
    def partial(self):
        return self.__data['partial']

    def getPackage(self):
        return self.mungePackage(self.__data['package'])

    def isValid(self):
        return self.__data['valid']

    def isShared(self):
        return self.__data['isShared']

    def getWorkspacePath(self):
        return self.__data['workspacePath']

    def getExecPath(self, referrer=None):
        """Return the execution path of the step.

        The execution path is where the step is actually run. It may be distinct
        from the workspace path if the build is performed in a sandbox. The
        ``referrer`` is an optional parameter that represents a step that refers
        to this step while building.
        """
        if self.isValid():
            if (referrer or self).getSandbox() is None:
                return self.getStoragePath()
            else:
                return os.path.join("/bob", asHexStr(self.getVariantId()), "workspace")
        else:
            return "/invalid/exec/path/of/{}".format(self.getPackage().getName())

    def getStoragePath(self):
        """Return the storage path of the step.

        The storage path is where the files of the step are stored. For
        checkout and build steps this is always the workspace path. But package
        steps can be shared globally and thus the directory may lie outside of
        the project directoy. The storage path may also change between
        invocations if the shared location changes.
        """
        if self.isPackageStep() and self.isShared():
            return BobState().getStoragePath(self.getWorkspacePath())
        else:
            return self.getWorkspacePath()

    def getSandbox(self, forceSandbox=False):
        return self.mungeSandbox(self.__data['sandbox'][1 if forceSandbox else 0])

    def getVariantId(self):
        return bytes.fromhex(self.__data['variantId'])

    def isCheckoutStep(self):
        return self.__data['isCheckoutStep']

    def isBuildStep(self):
        return self.__data['isBuildStep']

    def isPackageStep(self):
        return self.__data['isPackageStep']

    def _isFingerprinted(self):
        return self.__data['isFingerprinted']

    def isRelocatable(self):
        return self.__data['isRelocatable']

    def getDigestScript(self):
        return self.__data['digestScript']

    def getTools(self):
        return { name : self.mungeTool(tool) for name, tool in self.__data['tools'].items() }

    def getArguments(self):
        return [ self.mungeStep(arg) for arg in self.__data['arguments'] ]

    def getAllDepSteps(self, forceSandbox=False):
        return [ self.mungeStep(dep) for dep in
                    self.__data['allDepSteps'][1 if forceSandbox else 0] ]

    def getEnv(self):
        return self.__data['env']

    def getPaths(self):
        # FIXME: rename to getToolPaths
        """Get sorted list of execution paths to used tools.

        The returned list is intended to be passed as PATH environment variable.
        The paths are sorted by name.
        """
        return sorted([ os.path.join(tool.getStep().getExecPath(self), tool.getPath())
            for tool in self.getTools().values() ])

    def getLibraryPaths(self):
        """Get sorted list of library paths of used tools.

        The returned list is intended to be passed as LD_LIBRARY_PATH environment
        variable. The paths are first sorted by tool name. The order of paths of
        a single tool is kept.
        """
        paths = []
        for (name, tool) in sorted(self.getTools().items()):
            paths.extend([ os.path.join(tool.getStep().getExecPath(self), l) for l in tool.getLibs() ])
        return paths

    def getPreRunCmds(self):
        assert not self.JENKINS
        return self.__data['preRunCmds']

    def getJenkinsPreRunCmds(self):
        assert self.JENKINS
        return self.__data['preRunCmds']

    def getPostRunCmds(self):
        return self.__data['postRunCmds']

    def getSetupScript(self):
        return self.__data['setupScript']

    def getMainScript(self):
        return self.__data['mainScript']

    def getUpdateScript(self):
        return self.__data['updateScript']

    def _getFingerprintScript(self):
        return self.__data['fingerprintScript']

    def jobServer(self):
        return self.__data['jobServer']

    def getLabel(self):
        return self.__data['label']

    def isDeterministic(self):
        return self.__data['isDeterministic']

    def isUpdateDeterministic(self):
        return self.__data['isUpdateDeterministic']

    def hasLiveBuildId(self):
        return self.__data['hasLiveBuildId']

    def hasNetAccess(self):
        return self.__data['hasNetAccess']

    def getScmList(self):
        recipeSet = self.getPackage().getRecipe().getRecipeSet()
        def deserialize(state):
            ret = ScmOverride.__new__(ScmOverride)
            ret.__setstate__(state)
            return ret
        return [ getScm(scm, [deserialize(o) for o in overrides], recipeSet)
                 for scm, overrides in self.__data['scmList'] ]

    def getScmDirectories(self):
        return { d : (bytes.fromhex(h), p) for (d, (h, p)) in self.__data['scmDirectories'].items() }

    def mayUpdate(self, inputChanged, oldHash, rehash):
        if any((s.isLocal() and not s.isDeterministic()) for s in self.getScmList()):
            return True
        if not self.getUpdateScript():
            return False
        if not self.isUpdateDeterministic() or inputChanged:
            return True
        return rehash() != oldHash

    def _getSandboxVariantId(self):
        return bytes.fromhex(self.__data['sandboxVariantId'])

    async def getDigestCoro(self, calculate, forceSandbox=False, hasher=DigestHasher,
                            fingerprint=None, platform=b'', relaxTools=False):
        h = hasher()
        h.update(platform)
        if self._isFingerprinted() and self.getSandbox() \
                and not self.getPackage().getRecipe().getRecipeSet().sandboxFingerprints:
            [d] = await calculate([self.getSandbox().getStep()])
            h.fingerprint(hasher.sliceRecipes(d))
        elif fingerprint:
            h.fingerprint(fingerprint)
        sandbox = not self.getPackage().getRecipe().getRecipeSet().sandboxInvariant and \
            self.getSandbox(forceSandbox)
        if sandbox:
            [d] = await calculate([sandbox.getStep()])
            h.update(hasher.sliceRecipes(d))
            h.update(struct.pack("<I", len(sandbox.getPaths())))
            for p in sandbox.getPaths():
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        else:
            h.update(b'\x00' * 20)
        script = self.getDigestScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        tools = self.getTools()
        weakTools = set(self.__data['toolKeysWeak']) if relaxTools else []
        h.update(struct.pack("<I", len(tools)))
        tools = sorted(tools.items(), key=lambda t: t[0])
        toolsDigests = await calculate([ tool.getStep() for name,tool in tools ])
        for ((name, tool), d) in zip(tools, toolsDigests):
            if name in weakTools:
                h.update(name.encode('utf8'))
            else:
                h.update(hasher.sliceRecipes(d))
                h.update(struct.pack("<II", len(tool.getPath()), len(tool.getLibs())))
                h.update(tool.getPath().encode("utf8"))
                for l in tool.getLibs():
                    h.update(struct.pack("<I", len(l)))
                    h.update(l.encode('utf8'))
        h.update(struct.pack("<I", len(self.__data['digestEnv'])))
        for (key, val) in sorted(self.__data['digestEnv'].items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        args = [ a for a in self.getArguments() if a.isValid() ]
        argsDigests = await calculate(args)
        h.update(struct.pack("<I", len(args)))
        for d in argsDigests:
            h.update(hasher.sliceRecipes(d))
            h.fingerprint(hasher.sliceHost(d))
        return h.digest()

    async def predictLiveBuildId(self):
        """Query server to predict live build-id.

        Returns the live-build-id or None if an SCM query failed.
        """
        if not self.hasLiveBuildId():
            return None
        h = hashlib.sha1()
        h.update(getPlatformTag())
        h.update(self._getSandboxVariantId())
        for s in self.getScmList():
            liveBId = await s.predictLiveBuildId(self)
            if liveBId is None: return None
            h.update(liveBId)
        return h.digest()

    def calcLiveBuildId(self):
        """Calculate live build-id from workspace."""
        if not self.hasLiveBuildId():
            return None
        workspacePath = self.getWorkspacePath()
        h = hashlib.sha1()
        h.update(getPlatformTag())
        h.update(self._getSandboxVariantId())
        for s in self.getScmList():
            liveBId = s.calcLiveBuildId(workspacePath)
            if liveBId is None: return None
            h.update(liveBId)
        return h.digest()

    def getUpdateScriptDigest(self):
        """Return a digest that tracks relevant changes to the update script behaviour"""
        h = hashlib.sha1()
        script = self.getUpdateScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        h.update(struct.pack("<I", len(self.__data['digestEnv'])))
        for (key, val) in sorted(self.__data['digestEnv'].items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        return h.digest()


class PackageIR(AbstractIR):

    @classmethod
    def fromPackage(cls, package, graph, partial=False):
        self = cls()
        self.__data = {}
        self.__data['partial'] = partial
        self.__data['stack'] = package.getStack()
        self.__data['recipe'] = graph.addRecipe(package.getRecipe())
        self.__data['name'] = package.getName()
        self.__data['packageStep'] = graph.addStep(package.getPackageStep(), partial)
        self.__data['metaEnv'] = package.getMetaEnv()
        if not partial:
            self.__data['buildStep'] = graph.addStep(package.getBuildStep(), False)
            self.__data['checkoutStep'] = graph.addStep(package.getCheckoutStep(), False)
        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    def __eq__(self, other):
        return isinstance(other, PackageIR) and (self.__data['stack'] == other.__data['stack'])

    @property
    def partial(self):
        return self.__data['data']['partial']

    def getRecipe(self):
        return self.mungeRecipe(self.__data['recipe'])

    def getCheckoutStep(self):
        return self.mungeStep(self.__data['checkoutStep'])

    def getBuildStep(self):
        return self.mungeStep(self.__data['buildStep'])

    def getPackageStep(self):
        return self.mungeStep(self.__data['packageStep'])

    def getStack(self):
        return self.__data['stack']

    def getName(self):
        return self.__data['name']

    def getMetaEnv(self):
        return self.__data['metaEnv']

class SandboxIR(AbstractIR):
    @classmethod
    def fromSandbox(cls, sandbox, graph):
        self = cls()
        self.__data = {}
        self.__data['step'] = graph.addStep(sandbox.getStep(), True)
        self.__data['paths'] = sandbox.getPaths()
        self.__data['mounts'] = sandbox.getMounts()
        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    def getStep(self):
        return self.mungeStep(self.__data['step'])

    def getPaths(self):
        return self.__data['paths']

    def getMounts(self):
        return self.__data['mounts']

class ToolIR(AbstractIR):
    @classmethod
    def fromTool(cls, tool, graph):
        self = cls()
        self.__data = {}
        self.__data['step'] = graph.addStep(tool.getStep(), True)
        self.__data['path'] = tool.getPath()
        self.__data['libs'] = tool.getLibs()
        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    def getStep(self):
        return self.mungeStep(self.__data['step'])

    def getPath(self):
        return self.__data['path']

    def getLibs(self):
        return self.__data['libs']

class RecipeIR(AbstractIR):
    @classmethod
    def fromRecipe(cls, recipe, graph):
        self = cls()
        self.__data = {}
        self.__data['recipeSet'] = graph.addRecipeSet(recipe.getRecipeSet())
        self.__data['scriptLanguage'] = recipe.scriptLanguage.index.value
        self.__data['name'] = recipe.getName()
        self.__data['layer'] = recipe.getLayer()
        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    def getRecipeSet(self):
        return self.mungeRecipeSet(self.__data['recipeSet'])

    def getName(self):
        return self.__data['name']

    def getLayer(self):
        return self.__data['layer']

    @property
    def scriptLanguage(self):
        return getLanguage(ScriptLanguage(self.__data['scriptLanguage']))

class RecipeSetIR:
    @classmethod
    def fromRecipeSet(cls, recipeSet):
        self = cls()
        self.__data = {}
        self.__data['sandboxInvariant'] = recipeSet.sandboxInvariant
        self.__data['sandboxFingerprints'] = recipeSet.sandboxFingerprints
        self.__data['policies'] = {
            # FIXME: lazily query policies and only add them all in toData()
            'allRelocatable' : recipeSet.getPolicy('allRelocatable'),
            'pruneImportScm' : recipeSet.getPolicy('pruneImportScm'),
            'scmIgnoreUser' : recipeSet.getPolicy('scmIgnoreUser'),
            'secureSSL' : recipeSet.getPolicy('secureSSL'),
            'tidyUrlScm' : recipeSet.getPolicy('tidyUrlScm'),
            'sandboxFingerprints' : recipeSet.getPolicy('sandboxFingerprints'),
            'gitCommitOnBranch' : recipeSet.getPolicy('gitCommitOnBranch'),
            'fixImportScmVariant' : recipeSet.getPolicy('fixImportScmVariant'),
        }
        self.__data['archiveSpec'] = recipeSet.archiveSpec()
        self.__data['envWhiteList'] = sorted(recipeSet.envWhiteList())
        self.__data['projectRoot'] = recipeSet.getProjectRoot()
        return self

    @classmethod
    def fromData(cls, data):
        self = cls()
        self.__data = data
        return self

    def toData(self):
        return self.__data

    @property
    def sandboxInvariant(self):
        return self.__data['sandboxInvariant']

    @property
    def sandboxFingerprints(self):
        return self.__data['sandboxFingerprints']

    def archiveSpec(self):
        return self.__data['archiveSpec']

    def envWhiteList(self):
        return set(self.__data['envWhiteList'])

    def getPolicy(self, name, location=None):
        return self.__data['policies'][name]

    def getProjectRoot(self):
        return self.__data['projectRoot']
