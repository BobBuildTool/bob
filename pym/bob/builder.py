# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_VERSION
from .archive import DummyArchive
from .audit import Audit
from .errors import BobError, BuildError, MultiBobError
from .input import RecipeSet
from .invoker import Invoker, InvocationMode
from .languages import StepSpec
from .scm import getScm
from .state import BobState
from .stringparser import Env
from .tty import log, stepMessage, stepAction, stepExec, setProgress, ttyReinit, \
    SKIPPED, EXECUTED, INFO, WARNING, DEFAULT, \
    ALWAYS, IMPORTANT, NORMAL, INFO, DEBUG, TRACE
from .utils import asHexStr, hashDirectory, removePath, emptyDirectory, \
    isWindows, INVALID_CHAR_TRANS, quoteCmdExe, getPlatformTag, canSymlink
from .share import NullShare
from shlex import quote
from textwrap import dedent
import argparse
import asyncio
import concurrent.futures
import datetime
import hashlib
import io
import locale
import os
import re
import shutil
import signal
import stat
import sys
import tempfile

# Output verbosity:
#    <= -2: package name
#    == -1: package name, package steps
#    ==  0: package name, package steps, stderr
#    ==  1: package name, package steps, stderr, stdout
#    ==  2: package name, package steps, stderr, stdout, set -x

async def gatherTasks(tasks):
    if not tasks:
        return []

    await asyncio.wait(tasks)
    return [ t.result() for t in tasks ]

def hashWorkspace(step):
    # Cache is also used by share module
    return hashDirectory(step.getStoragePath(),
        os.path.join(os.path.dirname(step.getWorkspacePath()), "cache.bin"))

class HashOnce:
    def __init__(self, step):
        self.step = step
        self.result = None
    def hashWorkspace(self):
        if self.result is None:
            self.result = hashWorkspace(self.step)
        return self.result
    def invalidate(self):
        self.result = None

CHECKOUT_STATE_VARIANT_ID = None    # Key in checkout directory state for step variant-id
CHECKOUT_STATE_BUILD_ONLY = 1       # Key for checkout state of build-only builds

# Keys in checkout getDirectoryState that are not directories
CHECKOUT_NON_DIR_KEYS = {CHECKOUT_STATE_VARIANT_ID, CHECKOUT_STATE_BUILD_ONLY}

def compareDirectoryState(left, right):
    """Compare two directory states while ignoring the SCM specs.

    The SCM specs might change even though the digest stays the same (e.g. the
    URL changes but the commit id stays the same).  This function filters the
    spec to detect real changes.

    It also compares the CHECKOUT_STATE_VARIANT_ID to detect recipe changes. In
    contrast, the CHECKOUT_STATE_BUILD_ONLY sub-state is ignored as this is
    only relevant for build-only builds that have their dedicated functions
    below.
    """
    left  = { d : v[0] for d, v in left.items()  if d != CHECKOUT_STATE_BUILD_ONLY }
    right = { d : v[0] for d, v in right.items() if d != CHECKOUT_STATE_BUILD_ONLY }
    return left == right

def checkoutsFromState(state):
    """Return only the tuples related to SCMs from the checkout state.

    The list is sorted so that SCM operation can safely be done in list order.
    """
    return sorted(( (d, v) for d, v in state.items() if d not in CHECKOUT_NON_DIR_KEYS ),
                  key=lambda i: os.path.normcase(os.path.normpath(i[0])) )

def checkoutBuildOnlyState(checkoutStep, inputHashes):
    """Obtain state for build-only checkout updates.

    The assumption is that we can run updates as long as all local SCMs stayed
    the same. As this is just for build-only builds, we can assume that
    dependencies have been setup correctly (direct deps, tools).

    Because of the fixImportScmVariant bug, we include the directory too. This
    should not have been necessary otherwise. Needs to return a tuple because
    that's what is expected in the checkout SCM state (will be silently
    upgraded to a tuple by BobState otherwise).
    """
    return ("\n".join("{} {}".format(scm.getDirectory(), scm.asDigestScript())
                      for scm in checkoutStep.getScmList()
                      if scm.isLocal()),
            checkoutStep.getUpdateScriptDigest(),
            inputHashes)

def checkoutBuildOnlyStateCompatible(left, right):
    """Returns True if it's safe to run build-only checkout updates.

    Current policy is to just require that local SCMs are compatible. A
    different step variant-id, changed update script or dependency changes do
    *not* prohibit an in-place update.
    """
    left = left.get(CHECKOUT_STATE_BUILD_ONLY, (None, None, None))[0]
    right = right.get(CHECKOUT_STATE_BUILD_ONLY, (None, None, None))[0]
    return left == right

def checkoutBuildOnlyStateChanged(left, right):
    """Returns True if the update script has changed"""
    left = left.get(CHECKOUT_STATE_BUILD_ONLY, None)
    right = right.get(CHECKOUT_STATE_BUILD_ONLY, None)
    return left != right

def dissectPackageInputState(oldInputBuildId):
    """Take a package step input hashes and convert them to a common
    representation.

    Excluding the legacy storage, the following formats are persisted:

      built: [ BuildId, InputHash1, ...]
      downloaded: BuildId
      shared: (BuildId, Path)

    Returned tuple:
        (wasDownloaded:bool, wasShared:bool, input:(list[bytes]|str), buildId:bytes)
    """
    oldWasDownloaded = False
    oldWasShared = False
    if (isinstance(oldInputBuildId, list) and (len(oldInputBuildId) >= 1)):
        oldInput = oldInputBuildId[1:]
        oldInputBuildId = oldInputBuildId[0]
    elif isinstance(oldInputBuildId, bytes):
        oldWasDownloaded = True
        oldInput = None
    elif isinstance(oldInputBuildId, tuple):
        oldWasShared = True
        oldInput = oldInputBuildId[1]
        oldInputBuildId = oldInputBuildId[0]
    else:
        # created by old Bob version or new workspace
        oldInput = oldInputBuildId

    return (oldWasDownloaded, oldWasShared, oldInput, oldInputBuildId)

def packageInputDownloaded(buildId):
    assert isinstance(buildId, bytes)
    return buildId

def packageInputBuilt(buildId, inputHashes):
    assert isinstance(buildId, bytes)
    return [buildId] + inputHashes

def packageInputShared(buildId, location):
    assert isinstance(buildId, bytes)
    assert isinstance(location, str)
    return (buildId, location)


class RestartBuildException(Exception):
    pass

class CancelBuildException(Exception):
    pass

class ExternalJobServer:
    def __init__(self, makeFds):
        self.__makeFds = makeFds

    def getMakeFd(self):
        return self.__makeFds

class InternalJobServer:
    def __init__(self, jobs):
        self.__rfd, self.__wfd = os.pipe()
        os.write(self.__wfd, bytes(jobs))

    def getMakeFd(self):
        return [ self.__rfd, self.__wfd ]

class JobServerSemaphore:
    def __init__(self, fds, recursive):
        self.__sem = asyncio.Semaphore(0)
        self.__waitersCnt = 0
        self.__fds = fds
        os.set_blocking (self.__fds[0], False);
        self.__tokens = []
        self.__recursive = recursive
        self.__acquired = 0

    @staticmethod
    def jobavailableCallback(self):
        try:
            while self.__waitersCnt:
                self.__tokens.append(os.read(self.__fds[0], 1))
                self.__waitersCnt -= 1
                self.__sem.release()
        except BlockingIOError:
            pass
        finally:
            if self.__waitersCnt == 0:
                asyncio.get_event_loop().remove_reader(self.__fds[0])

    async def acquire(self):
        if self.__recursive and self.__acquired == 0:
            self.__acquired += 1
            return
        try:
            self.__tokens.append(os.read(self.__fds[0], 1))
        except BlockingIOError:
            if self.__waitersCnt == 0:
                asyncio.get_event_loop().add_reader(self.__fds[0],
                    JobServerSemaphore.jobavailableCallback, self)
            self.__waitersCnt += 1
            await self.__sem.acquire()
            pass
        self.__acquired += 1

    async def __aenter__(self):
        await self.acquire()
        return None

    def release(self):
        if self.__acquired == 0:
            raise ValueError ("BoundedSemaphore released too many times")
        if self.__waitersCnt != 0:
           self.__waitersCnt -= 1;
           self.__sem.release()
           if self.__waitersCnt == 0:
               asyncio.get_event_loop().remove_reader(self.__fds[0])
        else:
            if not self.__recursive or self.__acquired > 1:
                os.write(self.__fds[1], self.__tokens.pop())
        self.__acquired -= 1

    async def __aexit__(self, exc_type, exc, tb):
        self.release()

class LocalBuilderStatistic:
    def __init__(self):
        self.__activeOverrides = set()
        self.checkouts = 0
        self.packagesBuilt = 0
        self.packagesDownloaded = 0

    def addOverrides(self, overrides):
        self.__activeOverrides.update(overrides)

    def getActiveOverrides(self):
        return self.__activeOverrides

class AtticTracker:
    """Track directories that were moved to attic."""

    def __init__(self):
        self.__paths = {}

    def add(self, scmPath, atticPath):
        """Add directory that was moved to attic."""
        scmPath = os.path.normpath(scmPath)
        self.__paths[scmPath + os.sep] = atticPath

    def affected(self, nestedScmPath):
        """Check if a directory is affected by a directory that was previously
           moved to attic.
        """
        nestedScmPath = os.path.normpath(nestedScmPath)
        return self.__match(nestedScmPath) is not None

    def getAtticPath(self, nestedScmPath):
        """Get attic path for a directory that was affected."""
        nestedScmPath = os.path.normpath(nestedScmPath)
        prefix = self.__match(nestedScmPath)
        subDir = nestedScmPath[len(prefix):]
        return os.path.join(self.__paths[prefix], subDir)

    def __match(self, nestedScmPath):
        nestedScmPath += os.sep
        for p in self.__paths:
            if nestedScmPath.startswith(p):
                return p
        return None

class LocalBuilder:

    RUN_TEMPLATE_POSIX = """#!/bin/bash
cd {ROOT}
{BOB} _invoke {CLEAN} {SPEC} "$@"
"""

    RUN_TEMPLATE_WINDOWS = """@ECHO OFF
cd {ROOT}
{BOB} _invoke {CLEAN} {SPEC} %*
"""

    @staticmethod
    def releaseNameFormatter(step, props):
        if step.isCheckoutStep():
            base = step.getPackage().getRecipe().getName()
        else:
            base = step.getPackage().getRecipe().getPackageName()
        return os.path.join("work", base.replace('::', os.sep), step.getLabel())

    @staticmethod
    def releaseNamePersister(wrapFmt):

        def fmt(step, props):
            return BobState().getByNameDirectory(
                wrapFmt(step, props),
                asHexStr(step.getVariantId()),
                step.isCheckoutStep())

        return fmt

    @staticmethod
    def releaseNameInterrogator(step, props):
        return BobState().getExistingByNameDirectory(asHexStr(step.getVariantId()))

    @staticmethod
    def developNameFormatter(step, props):
        if step.isCheckoutStep():
            base = step.getPackage().getRecipe().getName()
        else:
            base = step.getPackage().getRecipe().getPackageName()
        return os.path.join("dev", step.getLabel(), base.replace('::', os.sep))

    @staticmethod
    def makeRunnable(wrapFmt):

        def fmt(step, props):
            ret = wrapFmt(step, props)
            if ret is not None:
                return os.path.join(ret, "workspace")
            else:
                return None

        return fmt

    def __init__(self, verbose, force, skipDeps, buildOnly, preserveEnv,
                 envWhiteList, bobRoot, cleanBuild, noLogFile):
        self.__wasRun= {}
        self.__wasSkipped = {}
        self.__wasDownloadTried = {}
        self.__verbose = max(ALWAYS, min(TRACE, verbose))
        self.__noLogFile = noLogFile
        self.__force = force
        self.__skipDeps = skipDeps
        self.__buildOnly = buildOnly
        self.__preserveEnv = preserveEnv
        self.__envWhiteList = set(envWhiteList)
        self.__jobServer = None
        self.__archive = DummyArchive()
        self.__downloadDepth = 0xffff
        self.__downloadDepthForce = 0xffff
        self.__downloadLayerModes = []
        self.__downloadPackages = None
        self.__uploadDepth = 0xffff
        self.__bobRoot = bobRoot
        self.__cleanBuild = cleanBuild
        self.__cleanCheckout = False
        self.__srcBuildIds = {}
        self.__buildDistBuildIds = {}
        self.__buildDistBuildIdsPreset = {}
        self.__statistic = LocalBuilderStatistic()
        self.__alwaysCheckout = []
        self.__linkDeps = True
        self.__jobs = 1
        self.__makeFds = None
        self.__bufferedStdIO = False
        self.__keepGoing = False
        self.__audit = True
        self.__fingerprints = { None : b'', "" : b'' }
        self.__workspaceLocks = {}
        self.__auditMeta = {}
        self.__share = NullShare()
        self.__useSharedPackages = False
        self.__installSharedPackages = False
        self.__executor = None
        self.__attic = True
        self.__slimSandbox = False

    def setExecutor(self, executor):
        self.__executor = executor

    def setArchiveHandler(self, archive):
        self.__archive = archive

    def setLocalDownloadMode(self, mode):
        self.__archive.wantDownloadLocal(mode != 'no')
        self.__setDownloadMode(mode)

    def setJenkinsDownloadMode(self, mode):
        self.__archive.wantDownloadJenkins(mode)
        self.__setDownloadMode('forced-fallback')

    def __setDownloadMode(self, mode):
        self.__downloadDepth = 0xffff
        if mode in ('yes', 'forced'):
            if mode == 'forced':
                self.__downloadDepth = 0
                self.__downloadDepthForce = 0
            elif self.__archive.canDownload():
                self.__downloadDepth = 0
        elif mode in ('deps', 'forced-deps'):
            if mode == 'forced-deps':
                self.__downloadDepth = 1
                self.__downloadDepthForce = 1
            elif self.__archive.canDownload():
                self.__downloadDepth = 1
        elif mode == 'forced-fallback':
            self.__downloadDepth = 0
            self.__downloadDepthForce = 1
        elif mode.startswith('packages='):
            try:
                self.__downloadPackages = re.compile(mode[9:])
            except re.error as e:
                raise BuildError("Invalid download regex '{}': {}".format(e.pattern, e))
        else:
            assert mode == 'no'

    def setLocalDownloadLayerMode(self, modes):
        self.__downloadLayerModes = []
        self.__archive.wantDownloadLocal(True)
        for mode in modes:
            regex = ""
            modeKey, _, modeLayer = mode.partition("=")
            try:
                regex = re.compile(modeLayer)
            except re.error as e:
                raise BuildError("Invalid download layer regex '{}': {}".format(e.pattern, e))
            self.__downloadLayerModes.append((regex, modeKey))

    def setLocalUploadMode(self, mode):
        self.__archive.wantUploadLocal(mode)
        self.__uploadDepth = 0xffff # upload everything

    def setJenkinsUploadMode(self, mode):
        self.__archive.wantUploadJenkins(mode)
        self.__uploadDepth = 0 # upload only top level packages

    def setCleanCheckout(self, clean):
        self.__cleanCheckout = clean

    def setAlwaysCheckout(self, alwaysCheckout):
        try:
            self.__alwaysCheckout = [ re.compile(e) for e in alwaysCheckout ]
        except re.error as e:
            raise BuildError("Invalid --always-checkout regex '{}': {}".format(e.pattern, e))

    def setLinkDependencies(self, linkDeps):
        self.__linkDeps = linkDeps

    def setJobs(self, jobs):
        self.__jobs = max(jobs, 1)

    def setMakeFds(self, makeFds):
        self.__makeFds = makeFds

    def enableBufferedIO(self):
        self.__bufferedStdIO = True

    def setKeepGoing(self, keepGoing):
        self.__keepGoing = keepGoing

    def setAudit(self, audit):
        self.__audit = audit

    def setAuditMeta(self, keys):
        r = re.compile(r"[0-9A-Za-z._-]+")
        for k in keys.keys():
            if not r.fullmatch(k):
                raise BuildError("Meta audit key '{}' contains invalid characters!"
                                    .format(k))
        self.__auditMeta = keys

    def setAtticEnable(self, enable):
        self.__attic = enable

    def setSlimSandbox(self, enable):
        self.__slimSandbox = enable

    def setShareHandler(self, handler):
        self.__share = handler

    def setShareMode(self, useShared, installShared):
        self.__useSharedPackages = useShared
        self.__installSharedPackages = installShared

    def setBuildDistBuildIds(self, buildIds):
        """Set build-ids of known dependencies.

        Used on Jenkins to let the build logic know the build-ids of all
        dependencies. Prevents the builder from calulating them which would
        require the whole dependency graph to be available.
        """
        self.__buildDistBuildIds = buildIds.copy()
        self.__buildDistBuildIdsPreset = buildIds

    def saveBuildState(self):
        state = {}
        # Save 'wasRun' as plain dict. Skipped steps are dropped because they
        # were not really executed. Either they are simply skipped again or, if
        # the user changes his mind, they will finally be executed.
        state['wasRun'] = { path : (vid, isCheckoutStep)
            for path, (vid, isCheckoutStep) in self.__wasRun.items()
            if not self.__wasSkipped.get(path, False) }
        # Save all predicted src build-ids. In case of a resume we won't ask
        # the server again for a live-build-id. Regular src build-ids are
        # cached by the usual 'wasRun' and 'resultHash' states.
        state['predictedBuidId'] = { (path, vid) : bid
            for (path, vid), (bid, predicted) in self.__srcBuildIds.items()
            if predicted }
        BobState().setBuildState(state)

    def loadBuildState(self):
        state = BobState().getBuildState()
        self.__wasRun = dict(state.get('wasRun', {}))
        self.__srcBuildIds = { (path, vid) : (bid, True)
            for (path, vid), bid in state.get('predictedBuidId', {}).items() }

    def _wasAlreadyRun(self, step, skippedOk):
        path = step.getWorkspacePath()
        if path in self.__wasRun:
            digest = self.__wasRun[path][0]
            # invalidate invalid cached entries
            if digest != step.getVariantId():
                del self.__wasRun[path]
                return False
            elif (not skippedOk) and self.__wasSkipped.get(path, False):
                return False
            else:
                return True
        else:
            return False

    def _setAlreadyRun(self, step, isCheckoutStep, skipped=False):
        path = step.getWorkspacePath()
        self.__wasRun[path] = (step.getVariantId(), isCheckoutStep)
        self.__wasSkipped[path] = skipped

    def _clearWasRun(self):
        """Clear "was-run" info for build- and package-steps."""
        self.__wasRun = { path : (vid, isCheckoutStep)
            for path, (vid, isCheckoutStep) in self.__wasRun.items()
            if isCheckoutStep }

    def _wasDownloadTried(self, step):
        return self.__wasDownloadTried.get(step.getWorkspacePath(), False)

    def _setDownloadTried(self, step):
        self.__wasDownloadTried[step.getWorkspacePath()] = True

    def _clearDownloadTried(self):
        self.__downloadDisposition = {}

    def _constructDir(self, step, label):
        created = False
        workDir = step.getWorkspacePath()
        if os.path.islink(workDir) or os.path.isfile(workDir):
            # Remove symlink or file which was left by a shared package
            os.unlink(workDir)
        if not os.path.isdir(workDir):
            os.makedirs(workDir)
            created = True

        # On Jenkins we check a canary. In rare circumstances it is possible
        # that the path formatter chose an existing path and the Job was not
        # built clean in an old workspace.
        if step.JENKINS:
            canary = os.path.join(os.path.dirname(workDir), "canary.txt")
            content = asHexStr(step.getVariantId())
            try:
                with open(canary, "r") as f:
                    if f.read() != content:
                        raise BuildError("Workspace contains stale data! Delete it and restart job.")
            except FileNotFoundError:
                # If an SCM is used that is supported by Jenkins natively, the
                # directory will always be present, even if if was deleted
                # previously. Use the canary as indicator if the workspace was
                # (re-)created...
                created = True
                with open(canary, "w") as f: f.write(content)
            except OSError as e:
                raise BuildError("Error reading canary: " + str(e))

        return (workDir, created)

    def __workspaceLock(self, step):
        path = step.getWorkspacePath()
        ret = self.__workspaceLocks.get(path)
        if ret is None:
            self.__workspaceLocks[path] = ret = asyncio.Lock()
        return ret

    async def _generateAudit(self, step, depth, resultHash, buildId, executed=True):
        auditPath = os.path.join(os.path.dirname(step.getWorkspacePath()), "audit.json.gz")
        if os.path.lexists(auditPath): removePath(auditPath)
        if not self.__audit:
            return None

        def auditOf(s):
            return os.path.join(os.path.dirname(s.getWorkspacePath()), "audit.json.gz")

        metaDefines = self.__auditMeta.copy()
        metaDefines.update({
            "bob" : BOB_VERSION,
            "recipe" : step.getPackage().getRecipe().getName(),
            "package" : "/".join(step.getPackage().getStack()),
            "step" : step.getLabel(),
            "language" : step.getPackage().getRecipe().scriptLanguage.index.value,
        })

        with stepAction(step, "AUDIT", step.getWorkspacePath(), INFO) as a:
            audit = Audit.create(step.getVariantId(), buildId, resultHash)
            for var, val in metaDefines.items():
                audit.addDefine(var, val)
            for var, val in step.getPackage().getMetaEnv().items():
                audit.addMetaEnv(var, val)
            audit.setRecipesAudit(await step.getPackage().getRecipe().getRecipeSet().getScmAudit())

            # The following things make only sense if we just executed the step
            if executed:
                try:
                    audit.setEnv(os.path.join(step.getWorkspacePath(), "..", "env"))
                    for (name, tool) in sorted(step.getTools().items()):
                        audit.addTool(name, auditOf(tool.getStep()))
                    sandbox = step.getSandbox()
                    if sandbox is not None:
                        audit.setSandbox(auditOf(sandbox.getStep()))
                    for dep in step.getArguments():
                        if dep.isValid(): audit.addArg(auditOf(dep))
                except BobError as e:
                    a.fail(e.slogan, WARNING)
                    if not a.visible:
                        stepMessage(step, "AUDIT", "{}: failed: {}"
                                            .format(step.getWorkspacePath(), e.slogan),
                                    WARNING)
                    return None

            # Always check for SCMs but don't fail if we did not execute the step
            if step.isCheckoutStep():
                for scm in step.getScmList():
                    auditSpec = scm.getAuditSpec()
                    if auditSpec is not None:
                        (typ, dir, extra) = auditSpec
                        try:
                            await audit.addScm(typ, step.getWorkspacePath(), dir, extra)
                        except BobError as e:
                            if executed: raise
                            stepMessage(step, "AUDIT", "WARNING: cannot audit SCM: {} ({})"
                                                .format(e.slogan, dir),
                                           WARNING)

            audit.save(auditPath)
            return auditPath

    def __linkDependencies(self, step):
        """Create symlinks to the dependency workspaces"""

        # this will only work on POSIX
        if isWindows(): return

        if not self.__linkDeps: return

        # always re-create the deps directory
        basePath = os.getcwd()
        depsPath = os.path.join(basePath, step.getWorkspacePath(), "..", "deps")
        removePath(depsPath)
        os.makedirs(depsPath)

        def linkTo(dest, linkName):
            os.symlink(os.path.relpath(os.path.join(basePath, dest, ".."),
                                       os.path.join(linkName, "..")),
                       linkName)

        # there can only be one sandbox
        if step.getSandbox() is not None:
            sandboxPath = os.path.join(depsPath, "sandbox")
            linkTo(step.getSandbox().getStep().getWorkspacePath(), sandboxPath)

        # link tools by name
        tools = step.getTools()
        if tools:
            toolsPath = os.path.join(depsPath, "tools")
            os.makedirs(toolsPath)
            for (n,t) in tools.items():
                linkTo(t.getStep().getWorkspacePath(), os.path.join(toolsPath, n))

        # link dependencies by position and name
        args = step.getArguments()
        if args:
            argsPath = os.path.join(depsPath, "args")
            os.makedirs(argsPath)
            i = 1
            for a in args:
                if a.isValid():
                    linkTo(a.getWorkspacePath(),
                           os.path.join(argsPath,
                                        "{:02}-{}".format(i, a.getPackage().getName())))
                i += 1

    async def _runShell(self, step, scriptName, logger, workspaceCreated, cleanWorkspace=None,
                        mode=InvocationMode.CALL):
        workspacePath = step.getWorkspacePath()
        if not os.path.isdir(workspacePath): os.makedirs(workspacePath)
        self.__linkDependencies(step)

        # write spec
        specFile = os.path.join(workspacePath, "..", "step.spec")
        envFile = os.path.join(workspacePath, "..", "env")
        logFile = os.path.join(workspacePath, "..", "log.txt")
        scriptHint = os.path.join(workspacePath, "..", "script")
        spec = StepSpec.fromStep(step, envFile, self.__envWhiteList, logFile,
            scriptHint=scriptHint, isJenkins=step.JENKINS,
            slimSandbox=self.__slimSandbox)

        # Write invocation wrapper except on Jenkins. There will be nobody
        # around to actually execute it...
        if not step.JENKINS:
            with open(specFile, "w") as f:
                spec.toFile(f)

            if sys.platform == "win32":
                runFile = os.path.join("..", scriptName + ".cmd")
                runFileContent = self.RUN_TEMPLATE_WINDOWS.format(
                        ROOT=quoteCmdExe(os.getcwd()),
                        BOB=quoteCmdExe(self.__bobRoot),
                        SPEC=quoteCmdExe(specFile),
                        CLEAN="-c" if cleanWorkspace else "",
                    )
            else:
                runFile = os.path.join("..", scriptName + ".sh")
                runFileContent = self.RUN_TEMPLATE_POSIX.format(
                        ROOT=quote(os.getcwd()),
                        BOB=quote(self.__bobRoot),
                        SPEC=quote(specFile),
                        CLEAN="-c" if cleanWorkspace else "",
                    )
            absRunFile = os.path.normpath(os.path.join(workspacePath, runFile))
            absRunFile = os.path.join(".", absRunFile)

            with open(absRunFile, "w") as f:
                print(runFileContent, file=f)
            os.chmod(absRunFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
                stat.S_IROTH | stat.S_IWOTH)
        else:
            absRunFile = "Job"

        invoker = Invoker(spec, self.__preserveEnv, self.__noLogFile,
            self.__verbose >= INFO, self.__verbose >= NORMAL,
            self.__verbose >= DEBUG, self.__bufferedStdIO,
            executor=self.__executor)
        if step.jobServer() and self.__jobServer:
            invoker.setMakeParameters(self.__jobServer.getMakeFd(), self.__jobs)
        ret = await invoker.executeStep(mode, workspaceCreated, cleanWorkspace)
        if not self.__bufferedStdIO: ttyReinit() # work around MSYS2 messing up the console
        if ret == -int(signal.SIGINT):
            raise BuildError("User aborted while running {}".format(absRunFile),
                             help = "Run again with '--resume' to skip already built packages.")
        elif ret != 0:
            if self.__bufferedStdIO:
                logger.setError(invoker.getStdio().strip())
            raise BuildError("{} returned with {}"
                                .format(absRunFile, ret),
                             help="You may resume at this point with '--resume' after fixing the error.",
                             returncode=ret)

    def getStatistic(self):
        return self.__statistic

    def __createGenericTask(self, loop, coro):
        """Create and return task for coroutine."""
        return loop.create_task(self.__taskWrapper(coro))

    def __createCookTask(self, coro, step, checkoutOnly, tracker, count):
        """Create and return task for a cook()-like coroutine.

        The ``step``, ``checkoutOnly`` and the ``tracker`` arguments are used
        to prevent duplicate runs of the same coroutine. Task that only differ
        in the ``checkoutOnly`` parameter wait for each other.

        If ``count`` is True then the task will be counted in the global
        progress.
        """
        sandbox = step.getSandbox() and step.getSandbox().getStep().getVariantId()
        path = (step.getWorkspacePath(), sandbox, checkoutOnly)
        task = tracker.get(path)
        if task is not None: return task

        # Is there a concurrent task running for *not* checkoutOnly? If yes
        # then we have to wait for it to not call the same coroutine
        # concurrently.
        alternatePath = (step.getWorkspacePath(), sandbox, not checkoutOnly)
        alternateTask = tracker.get(alternatePath)

        if count:
            self.__tasksNum += 1
            setProgress(self.__tasksDone, self.__tasksNum)

        task = asyncio.get_event_loop().create_task(self.__taskWrapper(coro,
            path, tracker, alternateTask, step, count))
        tracker[path] = task

        return task

    def __createFingerprintTask(self, coro, step, trackingKey):
        """Create a fingerprinting task identified uniquely by ``trackingKey``.

        The task will be counted in the global progress.
        """
        task = self.__fingerprintTasks.get(trackingKey)
        if task is not None: return task

        self.__tasksNum += 1
        setProgress(self.__tasksDone, self.__tasksNum)

        task = asyncio.get_event_loop().create_task(self.__taskWrapper(coro,
            trackingKey, self.__fingerprintTasks, step=step, count=True))
        self.__fingerprintTasks[trackingKey] = task

        return task

    async def __taskWrapper(self, coro, trackingKey=None, tracker=None,
                            fence=None, step=None, count=False):
        try:
            task = asyncio.current_task()
            self.__allTasks.add(task)
            if fence is not None:
                await fence
            ret = await coro()
            self.__allTasks.remove(task)
            if trackingKey is not None:
                # Only remove us from the task list if we finished successfully.
                # Other concurrent tasks might want to cook the same step again.
                # They have to get the same exception again.
                del tracker[trackingKey]
            if count:
                self.__tasksDone += 1
                setProgress(self.__tasksDone, self.__tasksNum)
            return ret
        except BuildError as e:
            if not self.__keepGoing:
                self.__running = False
            if step:
                e.setStack(step.getPackage().getStack())
            self.__buildErrors.append(e)
            raise CancelBuildException
        except RestartBuildException:
            if self.__running:
                log("Restart build due to wrongly predicted sources.", WARNING)
                self.__restart = True
                self.__running = False
            raise CancelBuildException
        except CancelBuildException:
            raise
        except asyncio.CancelledError:
            raise CancelBuildException
        except Exception as e:
            self.__buildErrors.append(e)
            raise CancelBuildException

    def cook(self, steps, checkoutOnly, loop, depth=0):
        def cancelJobs():
            if self.__jobs > 1:
                log("Cancel all running jobs...", WARNING)
            self.__running = False
            self.__restart = False
            for i in asyncio.all_tasks(): i.cancel()

        async def dispatcher():
            if self.__jobs > 1:
                packageJobs = [
                    self.__createGenericTask(loop, lambda s=step: self._cookTask(s, checkoutOnly, depth))
                    for step in steps ]
                await gatherTasks(packageJobs)
            else:
                packageJobs = []
                for step in steps:
                    job = self.__createGenericTask(loop, lambda s=step: self._cookTask(s, checkoutOnly, depth))
                    packageJobs.append(job)
                    await asyncio.wait({job})
                # retrieve results as last step to --keep-going
                for job in packageJobs: job.result()

        self.__restart = True
        while self.__restart:
            self.__running = True
            self.__restart = False
            self.__cookTasks = {}
            self.__buildIdTasks = {}
            self.__fingerprintTasks = {}
            self.__allTasks = set()
            self.__buildErrors = []
            if sys.platform == "win32" or self.__jobs == 1:
                self.__runners = asyncio.BoundedSemaphore(self.__jobs)
            else:
                if self.__makeFds:
                    self.__jobServer = ExternalJobServer(self.__makeFds)
                    self.__runners = JobServerSemaphore(self.__jobServer.getMakeFd(), True)
                else:
                    self.__jobServer = InternalJobServer(self.__jobs)
                    self.__runners = JobServerSemaphore(self.__jobServer.getMakeFd(), False)
            self.__tasksDone = 0
            self.__tasksNum = 0

            j = self.__createGenericTask(loop, dispatcher)
            try:
                loop.add_signal_handler(signal.SIGINT, cancelJobs)
            except NotImplementedError:
                pass # not implemented on windows
            try:
                loop.run_until_complete(j)
            except CancelBuildException:
                pass
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    loop.remove_signal_handler(signal.SIGINT)
                except NotImplementedError:
                    pass # not implemented on windows

            # Reap all remaining tasks to prevent Python warnings about ignored
            # exceptions or that tasks are still pending. We don't care about
            # their result. This is already handled via __buildErrors.
            for i in self.__allTasks: i.cancel()
            if self.__allTasks:
                loop.run_until_complete(asyncio.gather(*self.__allTasks,
                                                       return_exceptions=True))
            self.__allTasks.clear()

            if len(self.__buildErrors) > 1:
                raise MultiBobError(self.__buildErrors)
            elif self.__buildErrors:
                raise self.__buildErrors[0]

        if not self.__running:
            raise BuildError("Canceled by user!",
                             help = "Run again with '--resume' to skip already built packages.")

    async def _cookTask(self, step, checkoutOnly, depth):
        async with self.__runners:
            if not self.__running: raise CancelBuildException
            await self._cook([step], step.getPackage(), checkoutOnly, depth)

    async def _cook(self, steps, parentPackage, checkoutOnly, depth=0):
        # skip everything except the current package
        if self.__skipDeps:
            steps = [ s for s in steps if s.getPackage() == parentPackage ]

        # bail out if nothing has to be done
        steps = [ s for s in steps
                  if s.isValid() and not self._wasAlreadyRun(s, checkoutOnly) ]
        if not steps: return

        if self.__jobs > 1:
            # spawn the child tasks
            tasks = [
                self.__createCookTask(lambda s=step: self._cookStep(s, checkoutOnly, depth),
                                      step, checkoutOnly, self.__cookTasks, True)
                for step in steps
            ]

            # wait for all tasks to finish
            await self.__yieldJobWhile(gatherTasks(tasks))
        else:
            tasks = []
            for step in steps:
                task = self.__createCookTask(lambda s=step: self._cookStep(s, checkoutOnly, depth),
                                             step, checkoutOnly, self.__cookTasks, True)
                tasks.append(task)
                await self.__yieldJobWhile(asyncio.wait({task}))
            # retrieve results as last step to --keep-going
            for t in tasks: t.result()

    async def _cookStep(self, step, checkoutOnly, depth):
        await self.__runners.acquire()
        try:
            if not self.__running:
                raise CancelBuildException
            elif not step.isValid():
                pass
            elif self._wasAlreadyRun(step, checkoutOnly):
                pass
            elif step.isCheckoutStep():
                await self._cook(step.getAllDepSteps(), step.getPackage(), False, depth+1)
                async with self.__workspaceLock(step):
                    if not self._wasAlreadyRun(step, checkoutOnly):
                        await self._cookCheckoutStep(step, depth)
            elif step.isBuildStep():
                await self._cook(step.getAllDepSteps(), step.getPackage(), checkoutOnly, depth+1)
                async with self.__workspaceLock(step):
                    if not self._wasAlreadyRun(step, checkoutOnly):
                        if not checkoutOnly:
                            buildId = await self._getBuildId(step, depth)
                            await self._cookBuildStep(step, depth, buildId)
                        self._setAlreadyRun(step, False, checkoutOnly)
            else:
                assert step.isPackageStep()
                self._preparePackageStep(step)

                # Calculate build-id and fingerprint of expected artifact if
                # needed. Must be done without the workspace lock because it
                # recurses.
                if checkoutOnly:
                    buildId = None
                else:
                    buildId = await self._getBuildId(step, depth)

                # Try to use a shared location.
                audit = None
                shared = False
                if not checkoutOnly:
                    shared, audit = self._useSharedPackage(step, buildId)
                    if shared:
                        self._setAlreadyRun(step, False, checkoutOnly)

                # Try to download if needed and possible. Will only be tried
                # once per invocation! Might download to a shared (temporary)
                # location if sharing is possible for this package.
                downloaded = False
                if not shared and not checkoutOnly:
                    async with self.__workspaceLock(step):
                        if not self._wasDownloadTried(step):
                            downloaded, audit = await self._downloadPackage(step, depth, buildId)
                            self._setDownloadTried(step)
                            if downloaded:
                                self._setAlreadyRun(step, False, checkoutOnly)

                # Recurse and build if not downloaded or shared
                built = False
                if not shared and not downloaded:
                    await self._cook(step.getAllDepSteps(), step.getPackage(), checkoutOnly, depth+1)
                    async with self.__workspaceLock(step):
                        if not self._wasAlreadyRun(step, checkoutOnly):
                            if not checkoutOnly:
                                built, audit = await self._cookPackageStep(step,
                                    depth, buildId)
                            self._setAlreadyRun(step, False, checkoutOnly)

                # Upload package if it was built. On Jenkins we always upload
                # because it is essential that the tgz/buildid exists.
                if (built or step.JENKINS) and self.__archive.canUpload() and (depth <= self.__uploadDepth):
                    await self.__archive.uploadPackage(step, buildId,
                        audit, step.getStoragePath(), executor=self.__executor)

                # If the package can be shared and was built/downloaded it must
                # be moved to the final location.
                if not shared and (downloaded or built):
                    self._installSharedPackage(step, buildId)
        except BuildError as e:
            e.setStack(step.getPackage().getStack())
            raise
        finally:
            # we're done, let the others do their work
            self.__runners.release()

    async def _cookCheckoutStep(self, checkoutStep, depth):
        overrides = set()
        scmList = checkoutStep.getScmList()
        for scm in scmList:
            overrides.update(scm.getActiveOverrides())
        self.__statistic.addOverrides(overrides)
        overrides = len(overrides)
        overridesString = ("(" + str(overrides) + " scm " + ("overrides" if overrides > 1 else "override") +")") if overrides else ""

        # get directory into shape
        (prettySrcPath, created) = self._constructDir(checkoutStep, "src")
        oldCheckoutState = BobState().getDirectoryState(prettySrcPath, True)
        isFreshCheckout = not BobState().hasDirectoryState(prettySrcPath)
        if created:
            # invalidate result if folder was created
            oldCheckoutState = {}
            BobState().resetWorkspaceState(prettySrcPath, oldCheckoutState)
            isFreshCheckout = True
        oldCheckoutHash = BobState().getResultHash(prettySrcPath)

        checkoutInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
            for i in checkoutStep.getAllDepSteps() if i.isValid() ]

        checkoutExecuted = False
        checkoutDigest = checkoutStep.getVariantId()
        checkoutState = checkoutStep.getScmDirectories().copy()
        checkoutState[CHECKOUT_STATE_VARIANT_ID] = (checkoutDigest, None)
        checkoutState[CHECKOUT_STATE_BUILD_ONLY] = checkoutBuildOnlyState(checkoutStep, checkoutInputHashes)
        currentResultHash = HashOnce(checkoutStep)
        if self.__buildOnly and (BobState().getResultHash(prettySrcPath) is not None):
            inputChanged = checkoutBuildOnlyStateChanged(checkoutState, oldCheckoutState)
            rehash = currentResultHash.hashWorkspace
            if checkoutStep.mayUpdate(inputChanged, BobState().getResultHash(prettySrcPath), rehash):
                if checkoutBuildOnlyStateCompatible(checkoutState, oldCheckoutState):
                    with stepExec(checkoutStep, "UPDATE",
                                  "{} {}".format(prettySrcPath, overridesString)) as a:
                        await self._runShell(checkoutStep, "checkout", a, created, mode=InvocationMode.UPDATE)
                    newCheckoutState = oldCheckoutState.copy()
                    newCheckoutState[CHECKOUT_STATE_BUILD_ONLY] = checkoutState[CHECKOUT_STATE_BUILD_ONLY]
                    BobState().setDirectoryState(prettySrcPath, newCheckoutState)
                    currentResultHash.invalidate() # force recalculation
                else:
                    stepMessage(checkoutStep, "UPDATE", "WARNING: recipe changed - cannot update ({})"
                        .format(prettySrcPath), WARNING)
            elif not compareDirectoryState(checkoutState, oldCheckoutState):
                stepMessage(checkoutStep, "CHECKOUT", "WARNING: recipe changed but skipped due to --build-only ({})"
                    .format(prettySrcPath), WARNING)
            else:
                stepMessage(checkoutStep, "CHECKOUT", "skipped due to --build-only ({}) {}".format(prettySrcPath, overridesString),
                    SKIPPED, IMPORTANT)
        else:
            scmMap = { scm.getDirectory() : scm
                       for scm in checkoutStep.getScmList() }

            if self.__cleanCheckout:
                # check state of SCMs and invalidate if the directory is dirty
                for (scmDir, (scmDigest, scmSpec)) in checkoutsFromState(oldCheckoutState):
                    if scmDigest != checkoutState.get(scmDir, (None, None))[0]: continue
                    if not os.path.exists(os.path.join(prettySrcPath, scmDir)): continue
                    if scmMap[scmDir].status(checkoutStep.getWorkspacePath()).dirty:
                        # Invalidate scmDigest to forcibly move it away in the loop below.
                        # Do not use None here to distinguish it from a non-existent directory.
                        oldCheckoutState[scmDir] = (False, scmSpec)

            oldResultHash = BobState().getResultHash(prettySrcPath)
            checkoutReason = None
            if created:
                checkoutReason = "initial checkout"
            elif self.__force:
                checkoutReason = "forced"
            elif not checkoutStep.isDeterministic():
                checkoutReason = "indeterministic"
            elif not compareDirectoryState(checkoutState, oldCheckoutState):
                checkoutReason = "recipe changed"
            elif (checkoutInputHashes != BobState().getInputHashes(prettySrcPath)):
                checkoutReason = "dependency changed"
            elif (checkoutStep.getMainScript() or checkoutStep.getPostRunCmds()) \
                 and (oldResultHash != currentResultHash.hashWorkspace()):
                checkoutReason = "workspace changed"

            if checkoutReason:
                # Switch or move away old or changed source directories. In
                # case of nested SCMs the loop relies on the property of
                # checkoutsFromState() to return the directories in a top-down
                # order.
                atticPaths = AtticTracker()
                for (scmDir, (scmDigest, scmSpec)) in checkoutsFromState(oldCheckoutState):
                    scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                    if atticPaths.affected(scmPath):
                        # An SCM in the hierarchy above this one was moved to
                        # attic. This implies that all nested SCMs are affected
                        # too...
                        atticPath = atticPaths.getAtticPath(scmPath)
                        if os.path.exists(atticPath):
                            BobState().setAtticDirectoryState(atticPath, scmSpec)
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)
                    elif scmDigest != checkoutState.get(scmDir, (None, None))[0]:
                        canSwitch = (scmDir in scmMap) and scmDigest and \
                                     scmSpec is not None and \
                                     scmMap[scmDir].canSwitch(getScm(scmSpec)) and \
                                     os.path.exists(scmPath)
                        didSwitch = False
                        if canSwitch:
                            didSwitch = await self.__runScmSwitch(checkoutStep,
                                scmPath, scmMap[scmDir], scmSpec)

                        if didSwitch:
                            oldCheckoutState[scmDir] = checkoutState[scmDir]
                            BobState().setDirectoryState(prettySrcPath, oldCheckoutState)
                            continue

                        if os.path.exists(scmPath):
                            if not self.__attic:
                                raise BuildError("SCM '{}' inline switch not possible and move to attic disabled '{}'!"
                                            .format(scmDir, prettySrcPath))
                            atticName = datetime.datetime.now().isoformat().translate(INVALID_CHAR_TRANS)+"_"+os.path.basename(scmPath)
                            stepMessage(checkoutStep, "ATTIC",
                                "{} (move to ../attic/{})".format(scmPath, atticName), WARNING)
                            atticPath = os.path.join(prettySrcPath, "..", "attic")
                            if not os.path.isdir(atticPath):
                                os.makedirs(atticPath)
                            atticPath = os.path.join(atticPath, atticName)
                            os.rename(scmPath, atticPath)
                            if scmDir in scmMap:
                                scmMap[scmDir].postAttic(prettySrcPath)
                            BobState().setAtticDirectoryState(atticPath, scmSpec)
                            atticPaths.add(scmPath, atticPath)
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

                # Check that new checkouts do not collide with old stuff in
                # workspace. Do it before we store the new SCM state to
                # check again if the step is rerun.
                if not checkoutStep.JENKINS:
                    for scmDir in [ k for k,v in checkoutsFromState(checkoutState)]:
                        if scmDir == ".": continue
                        if scmDir in oldCheckoutState: continue
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        if os.path.exists(scmPath):
                            raise BuildError("New SCM checkout '{}' collides with existing file in workspace '{}'!"
                                                .format(scmDir, prettySrcPath))

                # Store new SCM checkout state. The script state is not stored
                # so that this step will run again if it fails. OTOH we must
                # record the SCM directories as some checkouts might already
                # succeeded before the step ultimately fails.
                BobState().setDirectoryState(prettySrcPath,
                    { d:s for (d,s) in checkoutState.items() if d != CHECKOUT_STATE_VARIANT_ID })

                # Forge checkout result before we run the step again.
                # Normally the correct result is set directly after the
                # checkout finished. But if the step fails and the user
                # re-runs with "build-only" the dependent steps should
                # trigger.
                if BobState().getResultHash(prettySrcPath) is not None:
                    oldCheckoutHash = datetime.datetime.now()
                    BobState().setResultHash(prettySrcPath, oldCheckoutHash)

                with stepExec(checkoutStep, "CHECKOUT",
                              "{} ({}) {}".format(prettySrcPath, checkoutReason, overridesString)) as a:
                    await self._runShell(checkoutStep, "checkout", a, created)
                self.__statistic.checkouts += 1
                checkoutExecuted = True
                currentResultHash.invalidate() # force recalculation
                # reflect new checkout state
                BobState().setDirectoryState(prettySrcPath, checkoutState)
                BobState().setInputHashes(prettySrcPath, checkoutInputHashes)
                BobState().setVariantId(prettySrcPath, await self.__getIncrementalVariantId(checkoutStep))
            else:
                stepMessage(checkoutStep, "CHECKOUT", "skipped (fixed package {})".format(prettySrcPath),
                    SKIPPED, IMPORTANT)

        # We always have to rehash the directory as the user might have
        # changed the source code manually.
        checkoutHash = currentResultHash.hashWorkspace()

        # Generate audit trail before setResultHash() to force re-generation if
        # the audit trail fails.
        if checkoutHash != oldCheckoutHash or self.__force:
            # Contrary to build- and package-steps, the hash of the checkout
            # workspace is it's build-id.
            checkoutBuildId = checkoutHash
            await self._generateAudit(checkoutStep, depth, checkoutHash, checkoutBuildId, checkoutExecuted)
            BobState().setResultHash(prettySrcPath, checkoutHash)

        # upload live build-id cache in case of fresh checkout
        if isFreshCheckout and self.__archive.canUpload() and checkoutStep.hasLiveBuildId():
            liveBId = checkoutStep.calcLiveBuildId()
            if liveBId is not None:
                await self.__archive.uploadLocalLiveBuildId(checkoutStep, liveBId, checkoutHash,
                    executor=self.__executor)

        # We're done. The sanity check below won't change the result but would
        # trigger this step again.
        self._setAlreadyRun(checkoutStep, True)

        # Predicted build-id and real one after checkout do not need to
        # match necessarily. Handle it as some build results might be
        # inconsistent to the sources now.
        buildId, predicted = self.__srcBuildIds.get((prettySrcPath, checkoutDigest),
            (checkoutHash, False))
        if buildId != checkoutHash:
            assert predicted, "Non-predicted incorrect Build-Id found!"
            self.__handleChangedBuildId(checkoutStep, checkoutHash)

    async def _cookBuildStep(self, buildStep, depth, buildBuildId):
        # Add the execution path of the build step to the buildDigest to
        # detect changes between sandbox and non-sandbox builds. This is
        # necessary in any build mode. Include the actual directories of
        # dependencies in buildDigest too. Directories are reused in
        # develop build mode and thus might change even though the variant
        # id of this step is stable. As most tools rely on stable input
        # directories we have to make a clean build if any of the
        # dependency directories change.
        buildVariantId = await self.__getIncrementalVariantId(buildStep)
        buildDigest = [buildVariantId, buildStep.getExecPath()] + \
            [ i.getExecPath(buildStep) for i in buildStep.getAllDepSteps() if i.isValid() ]

        # get directory into shape
        (prettyBuildPath, created) = self._constructDir(buildStep, "build")
        oldBuildDigest = BobState().getDirectoryState(prettyBuildPath, False)
        if created or (buildDigest != oldBuildDigest):
            # not created but exists -> something different -> prune workspace
            if not created and os.path.exists(prettyBuildPath):
                stepMessage(buildStep, "PRUNE", "{} (recipe changed)".format(prettyBuildPath),
                    WARNING)
                emptyDirectory(prettyBuildPath)
                created = True
            # invalidate build step
            BobState().resetWorkspaceState(prettyBuildPath, buildDigest)

        # run build if input has changed
        buildInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
            for i in buildStep.getAllDepSteps() if i.isValid() ]
        buildFingerprint = await self._getFingerprint(buildStep, depth)
        if buildFingerprint: buildInputHashes.append(buildFingerprint)
        if (not self.__force) and (BobState().getInputHashes(prettyBuildPath) == buildInputHashes):
            stepMessage(buildStep, "BUILD", "skipped (unchanged input for {})".format(prettyBuildPath),
                    SKIPPED, IMPORTANT)
            # We always rehash the directory in development mode as the
            # user might have compiled the package manually.
            if not self.__cleanBuild:
                BobState().setResultHash(prettyBuildPath, hashWorkspace(buildStep))
        else:
            with stepExec(buildStep, "BUILD", prettyBuildPath, (ALWAYS, NORMAL)) as fullAction:
                # Squash state because running the step will change the
                # content. If the execution fails we have nothing reliable
                # left and we _must_ run it again.
                BobState().delInputHashes(prettyBuildPath)
                BobState().setResultHash(prettyBuildPath, datetime.datetime.now())
                # build it
                with stepExec(buildStep, "BUILD", prettyBuildPath, INFO) as buildAction:
                    visibleAction = fullAction if fullAction.visible else buildAction
                    await self._runShell(buildStep, "build", visibleAction, created, self.__cleanBuild)
                buildHash = hashWorkspace(buildStep)
                await self._generateAudit(buildStep, depth, buildHash, buildBuildId)
            BobState().setResultHash(prettyBuildPath, buildHash)
            BobState().setVariantId(prettyBuildPath, buildDigest[0])
            BobState().setInputHashes(prettyBuildPath, buildInputHashes)

    def _preparePackageStep(self, packageStep):
        """Make sure to erase everything if something else is built.

        Will remove the workspace and the associated state if something else
        should be built in an existing workspace. If the workspace does not
        exist the stale state will be removed unconditionally.
        """
        prettyPackagePath = packageStep.getWorkspacePath()
        packageDigest = packageStep.getVariantId()
        oldPackageDigest = BobState().getDirectoryState(prettyPackagePath, False)
        somethingThere = os.path.lexists(prettyPackagePath) # might be a symlink

        # Prune if something else was there before
        if somethingThere and packageDigest != oldPackageDigest:
            stepMessage(packageStep, "PRUNE", "{} (recipe changed)".format(prettyPackagePath),
                WARNING)
            if os.path.islink(prettyPackagePath) or os.path.isfile(prettyPackagePath):
                # Remove symlink or file which was left by a shared package
                os.unlink(prettyPackagePath)
            else:
                emptyDirectory(prettyPackagePath)
            somethingThere = False

        # Reset state if we start from scratch
        if not somethingThere:
            BobState().resetWorkspaceState(prettyPackagePath, packageDigest)

    def _useSharedPackage(self, packageStep, packageBuildId):
        """Shared packages handling.

        Sharing is done agressively. If a shared package is available then it
        is always used. The goal is to throw away duplicate workspaces to save
        space as much as possible. OTOH shared packages are only installed if
        they really need to be built/downloaded.

        * shared package is ready to be used
            * previously shared with same destination -> done
            * previously shared but now different destination/build-id
                * reset symlink/file
                * set result hash
            * otherwise
                * remove any previous stuff and use shared package
                * set result hash
        * no sharing possible
            * was shared before: unshare - remove symlink/file if exists
            * download/build as normal
        * no shared package available but could be created
            * new -> build/download
            * force -> prune and build/download
            * was built before
                * same buildid -> normal build
                * different buildid -> prune and build/download
            * was downloaded before
                * same buildid -> done
                * different buildid -> prune and build/download
            * was shared before -> prune and build/download
        """

        prettyPackagePath = packageStep.getWorkspacePath()
        auditPath = os.path.join(os.path.dirname(prettyPackagePath), "audit.json.gz")
        packageDigest = packageStep.getVariantId()
        oldWasDownloaded, oldWasShared, oldInputLocation, oldInputBuildId = \
            dissectPackageInputState(BobState().getInputHashes(prettyPackagePath))
        if packageStep.isShared() and self.__useSharedPackages:
            sharedPath, sharedHash = self.__share.useSharedPackage(prettyPackagePath,
                packageBuildId)
        else:
            sharedPath = sharedHash = None

        # A shared package is available. Use it.
        if sharedPath:
            sharedWorkspace = os.path.join(sharedPath, "workspace")
            sharedAudit = os.path.join(sharedPath, "audit.json.gz")
            if oldWasShared and (sharedPath == oldInputLocation):
                stepMessage(packageStep, "PACKAGE", "skipped (already shared in {})".format(prettyPackagePath),
                    SKIPPED, IMPORTANT)
                BobState().setStoragePath(prettyPackagePath, sharedWorkspace)
                return True, sharedAudit
            elif oldWasShared:
                stepMessage(packageStep, "PRUNE", "{} (shared location changed)".format(prettyPackagePath),
                    WARNING)
                os.unlink(prettyPackagePath)
            elif os.path.exists(prettyPackagePath):
                stepMessage(packageStep, "PRUNE", "{} (use shared package)".format(prettyPackagePath),
                    WARNING)
                removePath(prettyPackagePath)
            removePath(auditPath)
            BobState().resetWorkspaceState(prettyPackagePath, packageDigest)

            stepMessage(packageStep, "SHARE", "{}".format(prettyPackagePath),
                EXECUTED, IMPORTANT)
            os.makedirs(os.path.dirname(prettyPackagePath), exist_ok=True)
            if canSymlink():
                os.symlink(sharedWorkspace, prettyPackagePath)
                os.symlink(sharedAudit, auditPath)
            else:
                with open(prettyPackagePath, "w") as f: f.write(sharedWorkspace)
                shutil.copyfile(sharedAudit, auditPath)
            BobState().setResultHash(prettyPackagePath, sharedHash)
            BobState().setVariantId(prettyPackagePath, packageStep.getVariantId())
            BobState().setInputHashes(prettyPackagePath,
                                      packageInputShared(packageBuildId, sharedPath))
            BobState().setStoragePath(prettyPackagePath, sharedWorkspace)
            return True, sharedAudit

        # Whatever happens we will first use the regular workspace path as
        # storage location. At the end we might move to the shared location.
        BobState().setStoragePath(prettyPackagePath, prettyPackagePath)

        # Remove defunct sharing. The package may be downloaded or built and
        # later installed to the shared location. This is handled by the other
        # functions.
        if oldWasShared:
            stepMessage(packageStep, "PRUNE", "{} (unshare)".format(prettyPackagePath),
                WARNING)
            os.unlink(prettyPackagePath)
            removePath(auditPath)
            BobState().resetWorkspaceState(prettyPackagePath, packageDigest)

        return False, None

    async def _downloadPackage(self, packageStep, depth, packageBuildId):
        """If possible try to download the package.

        If we downloaded the package in the last run we have to make sure that
        the Build-Id is still the same. The overall behaviour should look like
        this:

        * new workspace -> try download
        * previously built
          * still same build-id -> normal build
          * build-id changed -> prune and try download, fall back to build
        * previously downloaded
          * still same build-id -> done
          * build-id changed -> prune and try download, fall back to build
        """
        layer = "/".join(packageStep.getPackage().getRecipe().getLayer())
        layerDownloadMode = None
        if layer:
            for mode in self.__downloadLayerModes:
                if mode[0].match(layer):
                    layerDownloadMode = mode[1]

        # see if we can try to download
        tryDownload = layerDownloadMode != 'no' and (
            depth >= self.__downloadDepth or
            (self.__downloadPackages and self.__downloadPackages.search(packageStep.getPackage().getName())) or
            layerDownloadMode)
        if not tryDownload:
            return False, None

        # Dissect input parameters that lead to current workspace the last time
        (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
        oldWasDownloaded, oldWasShared, oldInputHashes, oldInputBuildId = \
            dissectPackageInputState(BobState().getInputHashes(prettyPackagePath))
        workspaceChanged = False
        wasDownloaded = False
        audit = os.path.join(prettyPackagePath, "..", "audit.json.gz")
        packageDigest = packageStep.getVariantId()

        # prune directory if we previously downloaded/built something different
        if (oldInputBuildId is not None) and (oldInputBuildId != packageBuildId):
            prune = True
            reason = "build-id changed"
        elif self.__force:
            prune = True
            reason = "build forced"
        else:
            prune = False

        if prune:
            stepMessage(packageStep, "PRUNE", "{} ({})".format(prettyPackagePath,
                reason), WARNING)
            emptyDirectory(prettyPackagePath)
            BobState().resetWorkspaceState(prettyPackagePath, packageDigest)
            oldInputBuildId = None
            oldInputFingerprint = None
            oldInputHashes = None

        # Try to download the package if the directory is currently
        # empty. If the directory holds a result and was downloaded it
        # we're done.
        if BobState().getResultHash(prettyPackagePath) is None:
            wasDownloaded = await self.__archive.downloadPackage(packageStep,
                packageBuildId, audit, prettyPackagePath, executor=self.__executor)
            if wasDownloaded:
                self.__statistic.packagesDownloaded += 1
                BobState().setInputHashes(prettyPackagePath,
                    packageInputDownloaded(packageBuildId))
                packageHash = hashWorkspace(packageStep)
                workspaceChanged = True
            elif layerDownloadMode == 'forced':
                raise BuildError("Downloading artifact of layer %s failed" % layer)
            elif depth >= self.__downloadDepthForce:
                raise BuildError("Downloading artifact failed")
        elif oldWasDownloaded:
            stepMessage(packageStep, "PACKAGE", "skipped (already downloaded in {})".format(prettyPackagePath),
                SKIPPED, IMPORTANT)
            wasDownloaded = True

        # Rehash directory if content was changed
        if workspaceChanged:
            BobState().setResultHash(prettyPackagePath, packageHash)
            BobState().setVariantId(prettyPackagePath, packageDigest)
            if wasDownloaded:
                BobState().setInputHashes(prettyPackagePath,
                    packageInputDownloaded(packageBuildId))

        return wasDownloaded, audit

    async def _cookPackageStep(self, packageStep, depth, packageBuildId):
        # Dissect input parameters that lead to current workspace the last time
        (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
        oldWasDownloaded, oldWasShared, oldInputHashes, oldInputBuildId = \
            dissectPackageInputState(BobState().getInputHashes(prettyPackagePath))

        # Take checkout step into account because it is guaranteed to
        # be available and the build step might reference it (think of
        # "make -C" or cross-workspace symlinks.
        workspaceChanged = False
        audit = None
        packageInputs = [ packageStep.getPackage().getCheckoutStep() ]
        packageInputs.extend(packageStep.getAllDepSteps())
        packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
            for i in packageInputs if i.isValid() ]
        packageFingerprint = await self._getFingerprint(packageStep, depth)
        if packageFingerprint: packageInputHashes.append(packageFingerprint)

        # Run package step if we have not yet downloaded the package or if
        # downloads are not possible anymore. Even if the package was
        # previously downloaded the oldInputHashes will be None to trigger
        # an actual build.
        if (not self.__force) and (oldInputHashes == packageInputHashes):
            stepMessage(packageStep, "PACKAGE", "skipped (unchanged input for {})".format(prettyPackagePath),
                SKIPPED, IMPORTANT)
            audit = os.path.join(os.path.dirname(prettyPackagePath), "audit.json.gz")
        else:
            with stepExec(packageStep, "PACKAGE", prettyPackagePath, (ALWAYS, NORMAL)) as fullAction:
                # invalidate result because folder will be cleared
                BobState().delInputHashes(prettyPackagePath)
                BobState().setResultHash(prettyPackagePath, datetime.datetime.now())
                with stepExec(packageStep, "PACKAGE", prettyPackagePath, INFO) as packageAction:
                    visibleAction = fullAction if fullAction.visible else packageAction
                    await self._runShell(packageStep, "package", visibleAction, True)
                packageHash = hashWorkspace(packageStep)
                packageDigest = await self.__getIncrementalVariantId(packageStep)
                workspaceChanged = True
                self.__statistic.packagesBuilt += 1
                audit = await self._generateAudit(packageStep, depth, packageHash, packageBuildId)

        # Rehash directory if content was changed
        if workspaceChanged:
            BobState().setResultHash(prettyPackagePath, packageHash)
            BobState().setVariantId(prettyPackagePath, packageDigest)
            BobState().setInputHashes(prettyPackagePath,
                packageInputBuilt(packageBuildId, packageInputHashes))

        return workspaceChanged, audit

    def _installSharedPackage(self, packageStep, buildId):
        """Try to install shared package

        If the package is shared we must copy or move it to the final location.
        This might fail if a concurrent build was faster on the same host. In
        this case we use what was created by the competing build (if the user
        allows using shared packages).
        """
        if not self.__installSharedPackages:
            return
        if not packageStep.isShared() or not self.__share.canInstall():
            return

        prettyPackagePath = packageStep.getWorkspacePath()

        # If the audit trail is missing we do not want to install the package.
        # The audit trail would be broken and such packages are not uploaded
        # either.
        if not os.path.exists(os.path.join(prettyPackagePath, "..", "audit.json.gz")):
            stepMessage(packageStep, "INSTALL", "skipped (no audit trail)",
                SKIPPED, IMPORTANT)
            return

        details = " to {}".format(self.__share.remoteName(buildId))
        with stepAction(packageStep, "INSTALL", prettyPackagePath, details=details) as a:
            sharedPath, wasInstalled = self.__share.installSharedPackage(
                prettyPackagePath, buildId,
                BobState().getResultHash(prettyPackagePath),
                self.__useSharedPackages)
            if wasInstalled:
                a.setResult("ok", EXECUTED)
            else:
                a.setResult("skipped (package already installed)", SKIPPED)

        if self.__useSharedPackages:
            sharedWorkspace = os.path.join(sharedPath, "workspace")
            sharedAudit = os.path.join(sharedPath, "audit.json.gz")
            auditPath = os.path.join(os.path.dirname(prettyPackagePath), "audit.json.gz")
            removePath(prettyPackagePath)
            os.makedirs(os.path.dirname(prettyPackagePath), exist_ok=True)
            if canSymlink():
                removePath(auditPath)
                os.symlink(sharedWorkspace, prettyPackagePath)
                os.symlink(sharedAudit, auditPath)
            else:
                with open(prettyPackagePath, "w") as f: f.write(sharedWorkspace)
            BobState().setInputHashes(prettyPackagePath,
                                      packageInputShared(buildId, sharedPath))
            BobState().setStoragePath(prettyPackagePath, sharedWorkspace)

    async def __queryLiveBuildId(self, step):
        """Predict live build-id of checkout step.

        Query the SCMs for their live-buildid and cache the result. Normally
        the current result is retuned unless we're in build-only mode. Then the
        cached result is used. Only if there is no cached entry the query is
        performed.
        """

        key = b'\x00' + step.getVariantId()
        if self.__buildOnly:
            liveBId = BobState().getBuildId(key)
            if liveBId is not None: return liveBId

        liveBId = await step.predictLiveBuildId()
        if liveBId is not None:
            BobState().setBuildId(key, liveBId)
        return liveBId

    def __invalidateLiveBuildId(self, step):
        """Invalidate last live build-id of a step."""

        key = b'\x00' + step.getVariantId()
        liveBId = BobState().getBuildId(key)
        if liveBId is not None:
            BobState().delBuildId(key)

    async def __translateLiveBuildId(self, step, liveBId):
        """Translate live build-id into real build-id.

        We maintain a local cache of previous translations. In case of a cache
        miss the archive is interrogated. A valid result is cached.
        """
        key = b'\x01' + liveBId
        bid = BobState().getBuildId(key)
        if bid is not None:
            return bid

        bid = await self.__archive.downloadLocalLiveBuildId(step, liveBId, executor=self.__executor)
        if bid is not None:
            BobState().setBuildId(key, bid)

        return bid

    async def __getCheckoutStepBuildId(self, step, depth):
        ret = None

        # Try to use live build-ids for checkout steps. Do not use them if
        # there is already a workspace or if the package matches one of the
        # 'always-checkout' patterns. Fall back to a regular checkout if any
        # condition is not met.
        name = step.getPackage().getName()
        path = step.getWorkspacePath()
        if not os.path.exists(step.getWorkspacePath()) and \
           not any(pat.search(name) for pat in self.__alwaysCheckout) and \
           step.hasLiveBuildId() and self.__archive.canDownload():
            with stepAction(step, "QUERY", step.getPackage().getName(), (IMPORTANT, NORMAL)) as a:
                liveBId = await self.__queryLiveBuildId(step)
                if liveBId:
                    ret = await self.__translateLiveBuildId(step, liveBId)
                if ret is None:
                    a.fail("unknown", WARNING)

        # do the checkout if we still don't have a build-id
        if ret is None:
            await self._cook([step], step.getPackage(), False, depth)
            # return directory hash
            ret = BobState().getResultHash(step.getWorkspacePath())
            predicted = False
        else:
            predicted = True

        return ret, predicted

    async def _getBuildId(self, step, depth):
        """Calculate build-id and cache result.

        The cache uses the workspace path as index because there might be
        multiple directories with the same variant-id. As the src build-ids can
        be cached for a long time the variant-id is used as index too to
        prevent possible false hits if the recipes change between runs.

        Checkout steps are cached separately from build and package steps.
        Build-ids of checkout steps may be predicted through live-build-ids. If
        we the prediction was wrong the build and package step build-ids are
        invalidated because they could be derived from the wrong checkout
        build-id.
        """

        # Pass over to __getBuildIdList(). It will try to create a task and (if
        # the calculation already failed) will make sure that we get the same
        # exception again. This prevents recalculation of already failed build
        # ids.
        [ret] = await self.__getBuildIdList([step], depth)
        return ret

    async def __getBuildIdList(self, steps, depth):
        if self.__jobs > 1:
            tasks = [
                self.__createCookTask(lambda s=step: self.__getBuildIdTask(s, depth),
                                      step, False, self.__buildIdTasks, False)
                for step in steps
            ]
            ret = await self.__yieldJobWhile(gatherTasks(tasks))
        else:
            tasks = []
            for step in steps:
                task = self.__createCookTask(lambda s=step: self.__getBuildIdTask(s, depth),
                                             step, False, self.__buildIdTasks, False)
                tasks.append(task)
                await self.__yieldJobWhile(asyncio.wait({task}))
            # retrieve results as last step to --keep-going
            ret = [ t.result() for t in tasks ]
        return ret

    async def __getBuildIdTask(self, step, depth):
        async with self.__runners:
            ret = await self.__getBuildIdSingle(step, depth)
        return ret

    async def __getBuildIdSingle(self, step, depth):
        path = step.getWorkspacePath()
        if step.isCheckoutStep():
            key = (path, step.getVariantId())
            ret, predicted = self.__srcBuildIds.get(key, (None, False))
            if ret is None:
                tmp = await self.__getCheckoutStepBuildId(step, depth)
                self.__srcBuildIds[key] = tmp
                ret = tmp[0]
        else:
            ret = self.__buildDistBuildIds.get(path)
            if ret is None:
                fingerprint = await self._getFingerprint(step, depth)
                ret = await step.getDigestCoro(lambda x: self.__getBuildIdList(x, depth+1),
                    fingerprint=fingerprint, platform=getPlatformTag(),
                    relaxTools=True)
                self.__buildDistBuildIds[path] = ret

        return ret

    def __handleChangedBuildId(self, step, checkoutHash):
        """Handle different build-id of src step after checkout.

        Through live-build-ids it is possible that an initially queried
        build-id does not match the real build-id after the sources have been
        checked out. As we might have already downloaded artifacts based on
        the now invalidated build-id we have to restart the build and check all
        build-ids, build- and package-steps again.
        """
        if step.isDeterministic() and step.getPackage().getRecipe().getRecipeSet().getPolicy("failUnstableCheckouts"):
            raise BuildError("Unstable checkoutScript detected! Checkout result did not match previous builds.",
                             help="The checkoutScript of the package was marked as deterministic. Repeated invocations generated different results, though. Look out for timestamps or similar problems.")

        key = (step.getWorkspacePath(), step.getVariantId())

        # Invalidate wrong live-build-id
        self.__invalidateLiveBuildId(step)

        # Invalidate (possibly) derived build-ids
        self.__srcBuildIds[key] = (checkoutHash, False)
        self.__buildDistBuildIds = self.__buildDistBuildIdsPreset.copy()

        # Forget all executed build- and package-steps
        self._clearWasRun()
        self._clearDownloadTried()

        # start from scratch
        raise RestartBuildException()

    async def __getIncrementalVariantId(self, step):
        """Calculate the variant-id with respect to workspace state.

        The real variant-id can be calculated solely by looking at the recipes.
        But as we allow the user to build single packages, skip dependencies
        and support checkout-/build-only builds the actual variant-id is
        dependent on the current project state.

        For every workspace we store the variant-id of the last build. When
        calculating the incremental variant-id of a step we take these stored
        variant-ids for the dependencies. If no variant-id was stored we take
        the real one because this is typically an old workspace where we want
        to prevent useless rebuilds. It could also be that the workspace was
        deleted.

        Important: this method can only work reliably if the dependent steps
        have been cooked. Otherwise the state may have stale data.
        """

        async def getStoredVId(deps):
            ret = []
            for dep in deps:
                ret.append(BobState().getVariantId(dep.getWorkspacePath()) or dep.getVariantId())
            return ret

        return await step.getDigestCoro(lambda x: getStoredVId(x))

    async def __yieldJobWhile(self, coro):
        """Yield the job slot while waiting for a coroutine.

        Handles the dirty details of cancellation. Might throw CancelledError
        if overall execution was stopped.
        """
        self.__runners.release()
        try:
            ret = await coro
        finally:
            acquired = False
            while not acquired:
                try:
                    await self.__runners.acquire()
                    acquired = True
                except asyncio.CancelledError:
                    pass
        if not self.__running: raise CancelBuildException
        return ret

    async def _getFingerprint(self, step, depth):
        # A relocatable step with no fingerprinting is easy
        isFingerprinted = step._isFingerprinted()
        trackRelocation = step.isPackageStep() and not step.isRelocatable()
        if not isFingerprinted and not trackRelocation:
            return b''

        # Execute the fingerprint script (or use cached result)
        if isFingerprinted:
            # Cache based on the script digest.
            sandbox = (step.getSandbox() is not None) and step.getSandbox().getStep()
            key = hashlib.sha1(step._getFingerprintScript().encode('utf8')).digest()
            if sandbox:
                # Add the sandbox build-id to the cache key to distinguish
                # between different sandboxes.
                sandboxBuildId = await self._getBuildId(sandbox, depth+1)
                key = hashlib.sha1(key + sandboxBuildId).digest()

            # Run fingerprint calculation in another task. The task will only
            # be created once for each ``key``.
            fingerprint = self.__fingerprints.get(key)
            if fingerprint is None:
                fingerprint = BobState().getFingerprint(key)
            if fingerprint is None:
                fingerprintTask = self.__createFingerprintTask(
                    lambda: self.__calcFingerprintTask(step, sandbox, key, depth),
                    step, key)
                await self.__yieldJobWhile(asyncio.wait({fingerprintTask}))
                fingerprint = fingerprintTask.result()
        else:
            fingerprint = b''

        # If the package is not relocatable the exec path is mixed into the
        # fingerprint to tag the relocation information at the artifact.
        if trackRelocation:
            fingerprint += os.path.abspath(step.getExecPath()).encode(
                locale.getpreferredencoding(False), 'replace')

        return hashlib.sha1(fingerprint).digest()

    async def __calcFingerprintTask(self, step, sandbox, key, depth):
        async with self.__runners:
            # If this is built in a sandbox then the artifact cache may help...
            if sandbox:
                fingerprint = await self.__archive.downloadLocalFingerprint(sandbox, key,
                    executor=self.__executor)
            else:
                fingerprint = None

            # When we don't know it yet we really have to execute the script.
            # In case a sandbox is used we have to make sure it's available.
            if fingerprint is None:
                if sandbox:
                    await self._cook([sandbox], sandbox.getPackage(), False, depth+1)
                with stepExec(step, "FNGRPRNT", step.getPackage().getName()) as a:
                    fingerprint = await self.__runFingerprintScript(step, a)

                # Always upload if this was calculated in a sandbox. The task will
                # only be run once so we don't need to worry here about duplicate
                # uploads.
                if sandbox:
                    await self.__archive.uploadLocalFingerprint(sandbox, key, fingerprint,
                        executor=self.__executor)

            # Cache result so that we don't ever need to spawn a task
            self.__fingerprints[key] = fingerprint

            # Persistently cache fingerprint if built in a sandbox. It's
            # reasonable to assume that the result is reproducible.
            if sandbox:
                BobState().setFingerprint(key, fingerprint)

        return fingerprint

    async def __runFingerprintScript(self, step, logger):
        spec = StepSpec.fromStep(step, None, self.__envWhiteList, isJenkins=step.JENKINS,
                                 slimSandbox=self.__slimSandbox)
        invoker = Invoker(spec, self.__preserveEnv, True,
                          self.__verbose >= INFO, self.__verbose >= NORMAL,
                          self.__verbose >= DEBUG, self.__bufferedStdIO,
                          executor=self.__executor)
        (ret, stdout, stderr) = await invoker.executeFingerprint()

        if ret == -int(signal.SIGINT):
            raise BuildError("Fingerprint script interrupted by user")
        elif ret != 0:
            if self.__bufferedStdIO:
                logger.setError(invoker.getStdio().strip())
            raise BuildError("Fingerprint script returned with {}".format(ret))

        return stdout

    async def __runScmSwitch(self, step, scmPath, scm, oldSpec):
        logFile = os.path.join(step.getWorkspacePath(), "..", "log.txt")
        spec = StepSpec.fromStep(step, None, self.__envWhiteList, logFile, isJenkins=step.JENKINS,
                                 slimSandbox=self.__slimSandbox)
        invoker = Invoker(spec, self.__preserveEnv, self.__noLogFile,
            self.__verbose >= INFO, self.__verbose >= NORMAL,
            self.__verbose >= DEBUG, self.__bufferedStdIO,
            executor=self.__executor)

        class Abort(Exception):
            pass

        try:
            with stepExec(step, "SWITCH", scmPath) as logger:
                ret = await invoker.executeScmSwitch(scm, oldSpec)
                if not self.__bufferedStdIO: ttyReinit() # work around MSYS2 messing up the console
                if ret == -int(signal.SIGINT):
                    raise BuildError("User aborted while inline switching SCM",
                                     help = "Run again with '--resume' to skip already built packages.")
                elif ret != 0:
                    if self.__bufferedStdIO:
                        logger.setError(invoker.getStdio().strip())
                    # Use an exception here to implicitly set the failed state
                    # of the logger.
                    raise Abort()
            return True
        except Abort:
            return False
