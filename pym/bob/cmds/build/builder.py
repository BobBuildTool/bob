# Bob build tool
# Copyright (C) 2016-2018  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ... import BOB_VERSION
from ...archive import DummyArchive
from ...audit import Audit
from ...errors import BobError, BuildError, MultiBobError
from ...input import RecipeSet
from ...state import BobState
from ...tty import log, stepMessage, stepAction, stepExec, setProgress, \
    SKIPPED, EXECUTED, INFO, WARNING, DEFAULT, \
    ALWAYS, IMPORTANT, NORMAL, INFO, DEBUG, TRACE
from ...utils import asHexStr, hashDirectory, removePath, emptyDirectory, \
    isWindows
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
import signal
import stat
import subprocess
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
    return hashDirectory(step.getWorkspacePath(),
        os.path.join(step.getWorkspacePath(), "..", "cache.bin"))

def compareDirectoryState(left, right):
    """Compare two directory states while ignoring the SCM specs.

    The SCM specs might change even though the digest stays the same (e.g. the
    URL changes but the commit id stays the same).  This function filters the
    spec to detect real changes.
    """
    left  = { d : v[0] for d, v in left.items()  }
    right = { d : v[0] for d, v in right.items() }
    return left == right

def dissectPackageInputState(oldInputBuildId):
    """Take a package step input hashes and convert them to a common
    representation.

    Excluding the legacy storage, two formats are persisted:

      built: [ BuildId, InputHash1, ...]
      downloaded: BuildId

    Returned tuple:
        (wasDownloaded:bool, inputHashes:list, buildId:bytes)
    """
    if (isinstance(oldInputBuildId, list) and (len(oldInputBuildId) >= 1)):
        oldWasDownloaded = False
        oldInputHashes = oldInputBuildId[1:]
        oldInputBuildId = oldInputBuildId[0]
    elif isinstance(oldInputBuildId, bytes):
        oldWasDownloaded = True
        oldInputHashes = None
    else:
        # created by old Bob version or new workspace
        oldWasDownloaded = False
        oldInputHashes = oldInputBuildId

    return (oldWasDownloaded, oldInputHashes, oldInputBuildId)

def packageInputDownloaded(buildId):
    assert isinstance(buildId, bytes)
    return buildId

def packageInputBuilt(buildId, inputHashes):
    assert isinstance(buildId, bytes)
    return [buildId] + inputHashes


class RestartBuildException(Exception):
    pass

class CancelBuildException(Exception):
    pass

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

class LocalBuilder:

    RUN_TEMPLATE = """#!/bin/bash

on_exit()
{{
     if [[ -n "$_sandbox" ]] ; then
          if [[ $_keep_sandbox = 0 ]] ; then
                rm -rf "$_sandbox"
          else
                echo "Keeping sandbox in $_sandbox" >&2
          fi
     fi
}}

run()
{{
    {SANDBOX_CMD} "$@"
}}

run_script()
{{
    local ret=0 trace=""
    if [[ $_verbose -ge 3 ]] ; then trace="-x" ; fi

    echo "### START: `date`"
    run /bin/bash $trace -- ../script {ARGS}
    ret=$?
    echo "### END($ret): `date`"

    return $ret
}}

# make permissions predictable
umask 0022

_clean={CLEAN}
_keep_env=0
_verbose=1
_no_log=0
_sandbox={SANDBOX_SETUP}
_keep_sandbox=0
_args=`getopt -o cinkqvE -- "$@"`
if [ $? != 0 ] ; then echo "Args parsing failed..." >&2 ; exit 1 ; fi
eval set -- "$_args"

_args=( )
while true ; do
    case "$1" in
        -c) _clean=1 ;;
        -i) _clean=0 ;;
        -n) _no_log=1 ;;
        -k) _keep_sandbox=1 ;;
        -q) : $(( _verbose-- )) ;;
        -v) : $(( _verbose++ )) ;;
        -E) _keep_env=1 ;;
        --) shift ; break ;;
        *) echo "Internal error!" ; exit 1 ;;
    esac
    _args+=("$1")
    shift
done

if [[ $# -gt 1 ]] ; then
    echo "Unexpected arguments!" >&2
    exit 1
fi

trap on_exit EXIT

case "${{1:-run}}" in
    run)
        if [[ $_clean = 1 ]] ; then
            rm -rf "${{0%/*}}/workspace"
            mkdir -p "${{0%/*}}/workspace"
        fi
        if [[ $_keep_env = 1 ]] ; then
            exec "$0" "${{_args[@]}}" __run
        else
            exec /usr/bin/env -i {WHITELIST} "$0" "${{_args[@]}}" __run
        fi
        ;;
    __run)
        cd "${{0%/*}}/workspace"
        if [[ $_no_log = 0 ]] ; then
            case "$_verbose" in
                0)
                    run_script >> ../log.txt 2>&1
                    ;;
                1)
                    set -o pipefail
                    {{
                        {{
                            run_script | tee -a ../log.txt
                        }} 3>&1 1>&2- 2>&3- | tee -a ../log.txt
                    }} 1>&2- 2>/dev/null
                    ;;
                *)
                    set -o pipefail
                    {{
                        {{
                            run_script | tee -a ../log.txt
                        }} 3>&1 1>&2- 2>&3- | tee -a ../log.txt
                    }} 3>&1 1>&2- 2>&3-
                    ;;
            esac
        else
            case "$_verbose" in
                0)
                    run_script 2>&1 > /dev/null
                    ;;
                1)
                    run_script > /dev/null
                    ;;
                *)
                    run_script
                    ;;
            esac
        fi
        ;;
    shell)
        cd "${{0%/*}}/workspace"
        rm -f ../audit.json.gz
        if [[ $_keep_env = 1 ]] ; then
            run /usr/bin/env {ENV} /bin/bash -s {ARGS}
        else
            run /usr/bin/env -i {WHITELIST} {ENV} /bin/bash --norc -s {ARGS}
        fi
        ;;
    *)
        echo "Unknown command" ; exit 1 ;;
esac
"""

    @staticmethod
    def releaseNameFormatter(step, props):
        if step.isCheckoutStep():
            base = step.getPackage().getRecipe().getName()
        else:
            base = step.getPackage().getName()
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
            base = step.getPackage().getName()
        return os.path.join("dev", step.getLabel(), base.replace('::', os.sep))

    @staticmethod
    def makeRunnable(wrapFmt):
        baseDir = os.getcwd()

        def fmt(step, mode, props, referrer):
            if mode == 'workspace':
                ret = wrapFmt(step, props)
            else:
                assert mode == 'exec'
                if referrer.getSandbox() is None:
                    ret = os.path.join(baseDir, wrapFmt(step, props))
                else:
                    ret = os.path.join("/bob", asHexStr(step.getVariantId()))
            return os.path.join(ret, "workspace") if ret is not None else None

        return fmt

    def __init__(self, recipes, verbose, force, skipDeps, buildOnly, preserveEnv,
                 envWhiteList, bobRoot, cleanBuild, noLogFile):
        self.__recipes = recipes
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
        # Make sure we have the host $PATH available until just before
        # executing the final script. Will always be overridded there.
        self.__envWhiteList.add("PATH")
        self.__archive = DummyArchive()
        self.__downloadDepth = 0xffff
        self.__downloadDepthForce = 0xffff
        self.__downloadPackages = None
        self.__bobRoot = bobRoot
        self.__cleanBuild = cleanBuild
        self.__cleanCheckout = False
        self.__srcBuildIds = {}
        self.__buildDistBuildIds = {}
        self.__statistic = LocalBuilderStatistic()
        self.__alwaysCheckout = []
        self.__linkDeps = True
        self.__jobs = 1
        self.__bufferedStdIO = False
        self.__keepGoing = False
        self.__fingerprints = { None : b'', "" : b'' }
        self.__workspaceLocks = {}
        self.__sandboxHelperPath = None

    def setArchiveHandler(self, archive):
        self.__archive = archive

    def setDownloadMode(self, mode):
        self.__downloadDepth = 0xffff
        if mode in ('yes', 'forced'):
            self.__archive.wantDownload(True)
            if mode == 'forced':
                self.__downloadDepth = 0
                self.__downloadDepthForce = 0
            elif self.__archive.canDownloadLocal():
                self.__downloadDepth = 0
        elif mode in ('deps', 'forced-deps'):
            self.__archive.wantDownload(True)
            if mode == 'forced-deps':
                self.__downloadDepth = 1
                self.__downloadDepthForce = 1
            elif self.__archive.canDownloadLocal():
                self.__downloadDepth = 1
        elif mode == 'forced-fallback':
            self.__archive.wantDownload(True)
            self.__downloadDepth = 0
            self.__downloadDepthForce = 1
        elif mode.startswith('packages='):
            self.__archive.wantDownload(True)
            try:
                self.__downloadPackages = re.compile(mode[9:])
            except re.error as e:
                raise BuildError("Invalid download regex '{}': {}".format(e.pattern), e)
        else:
            assert mode == 'no'
            self.__archive.wantDownload(False)

    def setUploadMode(self, mode):
        self.__archive.wantUpload(mode)

    def setCleanCheckout(self, clean):
        self.__cleanCheckout = clean

    def setAlwaysCheckout(self, alwaysCheckout):
        self.__alwaysCheckout = [ re.compile(e) for e in alwaysCheckout ]

    def setLinkDependencies(self, linkDeps):
        self.__linkDeps = linkDeps

    def setJobs(self, jobs):
        self.__jobs = max(jobs, 1)

    def enableBufferedIO(self):
        self.__bufferedStdIO = True

    def setKeepGoing(self, keepGoing):
        self.__keepGoing = keepGoing

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
        if not os.path.isdir(workDir):
            os.makedirs(workDir)
            created = True
        return (workDir, created)

    def __workspaceLock(self, step):
        path = step.getWorkspacePath()
        ret = self.__workspaceLocks.get(path)
        if ret is None:
            self.__workspaceLocks[path] = ret = asyncio.Lock()
        return ret

    async def _generateAudit(self, step, depth, resultHash, executed=True):
        if step.isCheckoutStep():
            buildId = resultHash
        else:
            buildId = await self._getBuildId(step, depth)
        audit = Audit.create(step.getVariantId(), buildId, resultHash)
        audit.addDefine("bob", BOB_VERSION)
        audit.addDefine("recipe", step.getPackage().getRecipe().getName())
        audit.addDefine("package", "/".join(step.getPackage().getStack()))
        audit.addDefine("step", step.getLabel())
        for var, val in step.getPackage().getMetaEnv().items():
            audit.addMetaEnv(var, val)
        audit.setRecipesAudit(step.getPackage().getRecipe().getRecipeSet().getScmAudit())

        # The following things make only sense if we just executed the step
        if executed:
            audit.setEnv(os.path.join(step.getWorkspacePath(), "..", "env"))
            for (name, tool) in sorted(step.getTools().items()):
                audit.addTool(name,
                    os.path.join(tool.getStep().getWorkspacePath(), "..", "audit.json.gz"))
            sandbox = step.getSandbox()
            if sandbox is not None:
                audit.setSandbox(os.path.join(sandbox.getStep().getWorkspacePath(), "..", "audit.json.gz"))
            for dep in step.getArguments():
                if dep.isValid():
                    audit.addArg(os.path.join(dep.getWorkspacePath(), "..", "audit.json.gz"))

        # Always check for SCMs but don't fail if we did not execute the step
        if step.isCheckoutStep():
            for scm in step.getScmList():
                auditSpec = scm.getAuditSpec()
                if auditSpec is not None:
                    (typ, dir) = auditSpec
                    try:
                        audit.addScm(typ, step.getWorkspacePath(), dir)
                    except BobError as e:
                        if executed: raise
                        stepMessage(step, "AUDIT", "WARNING: cannot audit SCM: {} ({})"
                                            .format(e.slogan, dir),
                                       WARNING)

        auditPath = os.path.join(step.getWorkspacePath(), "..", "audit.json.gz")
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

    def __getSandboxHelperPath(self):
        if self.__sandboxHelperPath is None:
            # If Bob is run from the source directly we have to make sure that
            # the sandbox helper is up-to-date and use it from there. Otherwise
            # we assume that Bob is properly installed and that
            # bob-namespace-sandbox is in $PATH.
            try:
                from ...develop.make import makeSandboxHelper
                makeSandboxHelper()
                sandboxHelper = os.path.join(self.__bobRoot, "bin", "bob-namespace-sandbox")
            except ImportError:
                sandboxHelper = "bob-namespace-sandbox"

            self.__sandboxHelperPath = sandboxHelper

        return self.__sandboxHelperPath

    async def _runShell(self, step, scriptName, cleanWorkspace, logger):
        workspacePath = step.getWorkspacePath()
        if cleanWorkspace: emptyDirectory(workspacePath)
        if not os.path.isdir(workspacePath): os.makedirs(workspacePath)
        self.__linkDependencies(step)

        # construct environment
        stepEnv = step.getEnv().copy()
        if step.getSandbox() is None:
            stepEnv["PATH"] = ":".join(step.getPaths() + [os.environ["PATH"]])
        else:
            stepEnv["PATH"] = ":".join(step.getPaths() + step.getSandbox().getPaths())
        stepEnv["LD_LIBRARY_PATH"] = ":".join(step.getLibraryPaths())
        stepEnv["BOB_CWD"] = step.getExecPath()

        # filter runtime environment
        if self.__preserveEnv:
            runEnv = os.environ.copy()
        else:
            runEnv = { k:v for (k,v) in os.environ.items()
                                     if k in self.__envWhiteList }

        # sandbox
        if step.getSandbox() is not None:
            sandboxSetup = "\"$(mktemp -d)\""
            sandboxMounts = [ "declare -a mounts=( )" ]
            sandbox = [ quote(self.__getSandboxHelperPath()) ]
            if self.__verbose >= TRACE:
                sandbox.append('-D')
            sandbox.extend(["-S", "\"$_sandbox\""])
            sandbox.extend(["-W", quote(step.getExecPath())])
            sandbox.extend(["-H", "bob"])
            sandbox.extend(["-d", "/tmp"])
            if not step.hasNetAccess(): sandbox.append('-n')
            sandboxRootFs = os.path.abspath(
                step.getSandbox().getStep().getWorkspacePath())
            for f in os.listdir(sandboxRootFs):
                sandboxMounts.append("mounts+=( -M {} -m /{} )".format(
                    quote(os.path.join(sandboxRootFs, f)), quote(f)))
            for (hostPath, sndbxPath, options) in step.getSandbox().getMounts():
                if "nolocal" in options: continue # skip for local builds?
                line = "-M " + hostPath
                if "rw" in options:
                    line += " -w " + sndbxPath
                elif hostPath != sndbxPath:
                    line += " -m " + sndbxPath
                line = "mounts+=( " + line + " )"
                if "nofail" in options:
                    sandboxMounts.append(
                        """if [[ -e {HOST} ]] ; then {MOUNT} ; fi"""
                            .format(HOST=hostPath, MOUNT=line)
                        )
                else:
                    sandboxMounts.append(line)
            sandboxMounts.append("mounts+=( -M {} -w {} )".format(
                quote(os.path.abspath(os.path.join(
                    step.getWorkspacePath(), ".."))),
                quote(os.path.normpath(os.path.join(
                    step.getExecPath(), ".."))) ))
            addDep = lambda s: (sandboxMounts.append("mounts+=( -M {} -m {} )".format(
                    quote(os.path.abspath(s.getWorkspacePath())),
                    quote(s.getExecPath(step)) )) if s.isValid() else None)
            for s in step.getAllDepSteps(): addDep(s)
            # special handling to mount all previous steps of current package
            s = step
            while s.isValid():
                if len(s.getArguments()) > 0:
                    s = s.getArguments()[0]
                    addDep(s)
                else:
                    break
            sandbox.append('"${mounts[@]}"')
            sandbox.append("--")
        else:
            sandbox = []
            sandboxMounts = []
            sandboxSetup = ""

        # write scripts
        runFile = os.path.join("..", scriptName+".sh")
        absRunFile = os.path.normpath(os.path.join(workspacePath, runFile))
        absRunFile = os.path.join(".", absRunFile)
        with open(absRunFile, "w") as f:
            print(LocalBuilder.RUN_TEMPLATE.format(
                    ENV=" ".join(sorted([
                        "{}={}".format(key, quote(value))
                        for (key, value) in stepEnv.items() ])),
                    WHITELIST=" ".join(sorted([
                        '${'+key+'+'+key+'="$'+key+'"}'
                        for key in self.__envWhiteList ])),
                    ARGS=" ".join([
                        quote(a.getExecPath(step))
                        for a in step.getArguments() ]),
                    SANDBOX_CMD="\n    ".join(sandboxMounts + [" ".join(sandbox)]),
                    SANDBOX_SETUP=sandboxSetup,
                    CLEAN="1" if cleanWorkspace else "0",
                ), file=f)
        scriptFile = os.path.join(workspacePath, "..", "script")
        with open(scriptFile, "w") as f:
            f.write(dedent("""\
                # Error handling
                bob_handle_error()
                {
                    set +x
                    echo "\x1b[31;1mStep failed with return status $1; Command:\x1b[0;31m ${BASH_COMMAND}\x1b[0m"
                    echo "Call stack (most recent call first)"
                    i=0
                    while caller $i >/dev/null ; do
                            j=${BASH_LINENO[$i]}
                            while [[ $j -ge 0 && -z ${_BOB_SOURCES[$j]:+true} ]] ; do
                                    : $(( j-- ))
                            done
                            echo "    #$i: ${_BOB_SOURCES[$j]}, line $(( BASH_LINENO[$i] - j )), in ${FUNCNAME[$((i+1))]}"
                            : $(( i++ ))
                    done

                    exit $1
                }
                declare -A _BOB_SOURCES=( [0]="Bob prolog" )
                trap 'bob_handle_error $? >&2 ; exit 99' ERR
                trap 'for i in "${_BOB_TMP_CLEANUP[@]-}" ; do /bin/rm -f "$i" ; done' EXIT
                set -o errtrace -o nounset -o pipefail

                # Special Bob array variables:
                """))
            print("declare -A BOB_ALL_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(a.getPackage().getName()),
                                   quote(a.getExecPath(step)))
                    for a in step.getAllDepSteps() ] ))), file=f)
            print("declare -A BOB_DEP_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(a.getPackage().getName()),
                                   quote(a.getExecPath(step)))
                    for a in step.getArguments() if a.isValid() ] ))), file=f)
            print("declare -A BOB_TOOL_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(n), quote(os.path.join(t.getStep().getExecPath(step), t.getPath())))
                    for (n,t) in step.getTools().items()] ))), file=f)
            print("", file=f)
            print("# Environment:", file=f)
            for (k,v) in sorted(stepEnv.items()):
                print("export {}={}".format(k, quote(v)), file=f)
            print("declare -p > ../env", file=f)
            print("", file=f)
            print("# BEGIN BUILD SCRIPT", file=f)
            print(step.getScript(), file=f)
            print("# END BUILD SCRIPT", file=f)
        os.chmod(absRunFile, stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP |
            stat.S_IROTH | stat.S_IWOTH)
        cmdLine = ["/bin/bash", runFile, "__run"]
        if self.__verbose < NORMAL:
            cmdLine.append('-q')
        elif self.__verbose == INFO:
            cmdLine.append('-v')
        elif self.__verbose >= DEBUG:
            cmdLine.append('-vv')
        if self.__noLogFile:
            cmdLine.append('-n')

        try:
            if self.__bufferedStdIO:
                ret = await self.__runShellBuffered(cmdLine, step.getWorkspacePath(), runEnv, logger)
            else:
                ret = await self.__runShellRegular(cmdLine, step.getWorkspacePath(), runEnv)
        except OSError as e:
            raise BuildError("Cannot execute build script {}: {}".format(absRunFile, str(e)))

        if ret == -int(signal.SIGINT):
            raise BuildError("User aborted while running {}".format(absRunFile),
                             help = "Run again with '--resume' to skip already built packages.")
        elif ret != 0:
            raise BuildError("Build script {} returned with {}"
                                .format(absRunFile, ret),
                             help="You may resume at this point with '--resume' after fixing the error.")

    async def __runShellRegular(self, cmdLine, cwd, env):
        proc = await asyncio.create_subprocess_exec(*cmdLine, cwd=cwd, env=env)
        ret = None
        while ret is None:
            try:
                ret = await proc.wait()
            except concurrent.futures.CancelledError:
                pass
        return ret

    async def __runShellBuffered(self, cmdLine, cwd, env, logger):
        with tempfile.TemporaryFile() as tmp:
            proc = await asyncio.create_subprocess_exec(*cmdLine, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=tmp, stderr=subprocess.STDOUT)
            ret = None
            while ret is None:
                try:
                    ret = await proc.wait()
                except concurrent.futures.CancelledError:
                    pass
            if ret != 0 and ret != -int(signal.SIGINT):
                tmp.seek(0)
                logger.setError(io.TextIOWrapper(tmp, errors='replace').read().strip())

        return ret

    def getStatistic(self):
        return self.__statistic

    def __createTask(self, coro, step=None, checkoutOnly=False, tracker=None, count=False):
        """Create and return task for coroutine.

        The optional ``step``, ``checkoutOnly`` and the ``tracker`` arguments
        are used to prevent duplicate runs of the same coroutine. Task that
        only differ in the ``checkoutOnly`` parameter wait for each other.
        """
        tracked = (step is not None) and (tracker is not None)

        async def wrapTask(coro, trackingKey, fence):
            try:
                task = asyncio.Task.current_task()
                self.__allTasks.add(task)
                if fence is not None:
                    await fence
                ret = await coro()
                self.__allTasks.remove(task)
                if tracked:
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
            except concurrent.futures.CancelledError:
                pass
            except Exception as e:
                self.__buildErrors.append(e)
                raise CancelBuildException

        if tracked:
            sandbox = step.getSandbox() and step.getSandbox().getStep().getVariantId()
            path = (step.getWorkspacePath(), sandbox, checkoutOnly)
            task = tracker.get(path)
            if task is not None: return task

            # Is there a concurrent task running for *not* checkoutOnly? If yes
            # then we have to wait for it to not call the same coroutine
            # concurrently.
            alternatePath = (step.getWorkspacePath(), sandbox, not checkoutOnly)
            alternateTask = tracker.get(alternatePath)
        else:
            path = None
            alternateTask = None

        if count:
            self.__tasksNum += 1
            setProgress(self.__tasksDone, self.__tasksNum)

        task = asyncio.get_event_loop().create_task(wrapTask(coro, path, alternateTask))
        if tracked:
            tracker[path] = task

        return task

    def cook(self, steps, checkoutOnly, depth=0):
        def cancelJobs():
            if self.__jobs > 1:
                log("Cancel all running jobs...", WARNING)
            self.__running = False
            self.__restart = False
            for i in asyncio.Task.all_tasks(): i.cancel()

        async def dispatcher():
            if self.__jobs > 1:
                packageJobs = [
                    self.__createTask(lambda s=step: self._cookTask(s, checkoutOnly, depth), step, checkoutOnly)
                    for step in steps ]
                await gatherTasks(packageJobs)
            else:
                packageJobs = []
                for step in steps:
                    job = self.__createTask(lambda s=step: self._cookTask(s, checkoutOnly, depth),
                                            step, checkoutOnly)
                    packageJobs.append(job)
                    await asyncio.wait({job})
                # retrieve results as last step to --keep-going
                for job in packageJobs: job.result()

        loop = asyncio.get_event_loop()
        self.__restart = True
        while self.__restart:
            self.__running = True
            self.__restart = False
            self.__cookTasks = {}
            self.__buildIdTasks = {}
            self.__allTasks = set()
            self.__buildErrors = []
            self.__runners = asyncio.BoundedSemaphore(self.__jobs)
            self.__tasksDone = 0
            self.__tasksNum = 0

            j = self.__createTask(dispatcher)
            try:
                loop.add_signal_handler(signal.SIGINT, cancelJobs)
            except NotImplementedError:
                pass # not implemented on windows
            try:
                loop.run_until_complete(j)
            except CancelBuildException:
                pass
            except concurrent.futures.CancelledError:
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
                self.__createTask(lambda s=step: self._cookStep(s, checkoutOnly, depth), step, checkoutOnly, self.__cookTasks, True)
                for step in steps
            ]

            # wait for all tasks to finish
            await self.__yieldJobWhile(gatherTasks(tasks))
        else:
            tasks = []
            for step in steps:
                task = self.__createTask(lambda s=step: self._cookStep(s, checkoutOnly, depth),
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
                        await self._cookBuildStep(step, checkoutOnly, depth)
                        self._setAlreadyRun(step, False, checkoutOnly)
            else:
                assert step.isPackageStep()
                self._preparePackageStep(step)

                # Prohibit up-/download if we are on the old allRelocatable
                # policy and the package is not explicitly relocatable and
                # built outside the sandbox.
                mayUpOrDownload = self.__recipes.getPolicy('allRelocatable') or \
                    step.isRelocatable() or (step.getSandbox() is not None)

                # Calculate build-id and fingerprint of expected artifact if
                # needed. Must be done without the workspace lock because it
                # recurses.
                if checkoutOnly:
                    buildId = None
                else:
                    buildId = await self._getBuildId(step, depth)

                # Try to download if possible. Will only be tried once per
                # invocation!
                downloaded = False
                if mayUpOrDownload and not checkoutOnly:
                    async with self.__workspaceLock(step):
                        if not self._wasDownloadTried(step):
                            downloaded = await self._downloadPackage(step, depth, buildId)
                            self._setDownloadTried(step)
                            if downloaded:
                                self._setAlreadyRun(step, False, checkoutOnly)

                # Recurse and build if not downloaded
                if not downloaded:
                    await self._cook(step.getAllDepSteps(), step.getPackage(), checkoutOnly, depth+1)
                    async with self.__workspaceLock(step):
                        if not self._wasAlreadyRun(step, checkoutOnly):
                            await self._cookPackageStep(step, checkoutOnly, depth, mayUpOrDownload, buildId)
                            self._setAlreadyRun(step, False, checkoutOnly)
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
        if created:
            # invalidate result if folder was created
            oldCheckoutState = {}
            BobState().resetWorkspaceState(prettySrcPath, oldCheckoutState)

        checkoutExecuted = False
        checkoutDigest = checkoutStep.getVariantId()
        checkoutState = checkoutStep.getScmDirectories().copy()
        checkoutState[None] = (checkoutDigest, None)
        if self.__buildOnly and (BobState().getResultHash(prettySrcPath) is not None):
            if not compareDirectoryState(checkoutState, oldCheckoutState):
                stepMessage(checkoutStep, "CHECKOUT", "WARNING: recipe changed but skipped due to --build-only ({})"
                    .format(prettySrcPath), WARNING)
            else:
                stepMessage(checkoutStep, "CHECKOUT", "skipped due to --build-only ({}) {}".format(prettySrcPath, overridesString),
                    SKIPPED, IMPORTANT)
        else:
            if self.__cleanCheckout:
                # check state of SCMs and invalidate if the directory is dirty
                stats = {}
                for scm in checkoutStep.getScmList():
                    stats[scm.getDirectory()] = scm
                for (scmDir, (scmDigest, scmSpec)) in oldCheckoutState.copy().items():
                    if scmDir is None: continue
                    if scmDigest != checkoutState.get(scmDir, (None, None))[0]: continue
                    if not os.path.exists(os.path.join(prettySrcPath, scmDir)): continue
                    if stats[scmDir].status(checkoutStep.getWorkspacePath()).dirty:
                        # Invalidate scmDigest to forcibly move it away in the loop below.
                        # Do not use None here to distinguish it from a non-existent directory.
                        oldCheckoutState[scmDir] = (False, scmSpec)

            checkoutInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                for i in checkoutStep.getAllDepSteps() if i.isValid() ]
            if (self.__force or (not checkoutStep.isDeterministic()) or
                (BobState().getResultHash(prettySrcPath) is None) or
                not compareDirectoryState(checkoutState, oldCheckoutState) or
                (checkoutInputHashes != BobState().getInputHashes(prettySrcPath))):
                # move away old or changed source directories
                for (scmDir, (scmDigest, scmSpec)) in oldCheckoutState.copy().items():
                    if (scmDir is not None) and (scmDigest != checkoutState.get(scmDir, (None, None))[0]):
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        if os.path.exists(scmPath):
                            atticName = datetime.datetime.now().isoformat()+"_"+os.path.basename(scmPath)
                            stepMessage(checkoutStep, "ATTIC",
                                "{} (move to ../attic/{})".format(scmPath, atticName), WARNING)
                            atticPath = os.path.join(prettySrcPath, "..", "attic")
                            if not os.path.isdir(atticPath):
                                os.makedirs(atticPath)
                            atticPath = os.path.join(atticPath, atticName)
                            os.rename(scmPath, atticPath)
                            BobState().setAtticDirectoryState(atticPath, scmSpec)
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

                # Check that new checkouts do not collide with old stuff in
                # workspace. Do it before we store the new SCM state to
                # check again if the step is rerun.
                for scmDir in checkoutState.keys():
                    if scmDir is None or scmDir == ".": continue
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
                    { d:s for (d,s) in checkoutState.items() if d is not None })

                # Forge checkout result before we run the step again.
                # Normally the correct result is set directly after the
                # checkout finished. But if the step fails and the user
                # re-runs with "build-only" the dependent steps should
                # trigger.
                if BobState().getResultHash(prettySrcPath) is not None:
                    BobState().setResultHash(prettySrcPath, datetime.datetime.utcnow())

                with stepExec(checkoutStep, "CHECKOUT",
                              "{} {}".format(prettySrcPath, overridesString)) as a:
                    await self._runShell(checkoutStep, "checkout", False, a)
                self.__statistic.checkouts += 1
                checkoutExecuted = True
                # reflect new checkout state
                BobState().setDirectoryState(prettySrcPath, checkoutState)
                BobState().setInputHashes(prettySrcPath, checkoutInputHashes)
                BobState().setVariantId(prettySrcPath, self.__getIncrementalVariantId(checkoutStep))
            else:
                stepMessage(checkoutStep, "CHECKOUT", "skipped (fixed package {})".format(prettySrcPath),
                    SKIPPED, IMPORTANT)

        # We always have to rehash the directory as the user might have
        # changed the source code manually.
        oldCheckoutHash = BobState().getResultHash(prettySrcPath)
        checkoutHash = hashWorkspace(checkoutStep)
        BobState().setResultHash(prettySrcPath, checkoutHash)

        # Generate audit trail. Has to be done _after_ setResultHash()
        # because the result is needed to calculate the buildId.
        if (checkoutHash != oldCheckoutHash) or checkoutExecuted:
            await self._generateAudit(checkoutStep, depth, checkoutHash, checkoutExecuted)

        # upload live build-id cache in case of fresh checkout
        if created and self.__archive.canUploadLocal() and checkoutStep.hasLiveBuildId():
            liveBId = checkoutStep.calcLiveBuildId()
            if liveBId is not None:
                await self.__archive.uploadLocalLiveBuildId(checkoutStep, liveBId, checkoutHash)

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

    async def _cookBuildStep(self, buildStep, checkoutOnly, depth):
        # Add the execution path of the build step to the buildDigest to
        # detect changes between sandbox and non-sandbox builds. This is
        # necessary in any build mode. Include the actual directories of
        # dependencies in buildDigest too. Directories are reused in
        # develop build mode and thus might change even though the variant
        # id of this step is stable. As most tools rely on stable input
        # directories we have to make a clean build if any of the
        # dependency directories change.
        buildDigest = [self.__getIncrementalVariantId(buildStep), buildStep.getExecPath()] + \
            [ i.getExecPath(buildStep) for i in buildStep.getArguments() if i.isValid() ]

        # get directory into shape
        (prettyBuildPath, created) = self._constructDir(buildStep, "build")
        oldBuildDigest = BobState().getDirectoryState(prettyBuildPath, False)
        if created or (buildDigest != oldBuildDigest):
            # not created but exists -> something different -> prune workspace
            if not created and os.path.exists(prettyBuildPath):
                stepMessage(buildStep, "PRUNE", "{} (recipe changed)".format(prettyBuildPath),
                    WARNING)
                emptyDirectory(prettyBuildPath)
            # invalidate build step
            BobState().resetWorkspaceState(prettyBuildPath, buildDigest)

        # run build if input has changed
        buildInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
            for i in buildStep.getAllDepSteps() if i.isValid() ]
        buildFingerprint = await self._getFingerprint(buildStep)
        if buildFingerprint: buildInputHashes.append(buildFingerprint)
        if checkoutOnly:
            stepMessage(buildStep, "BUILD", "skipped due to --checkout-only ({})".format(prettyBuildPath),
                    SKIPPED, IMPORTANT)
        elif (not self.__force) and (BobState().getInputHashes(prettyBuildPath) == buildInputHashes):
            stepMessage(buildStep, "BUILD", "skipped (unchanged input for {})".format(prettyBuildPath),
                    SKIPPED, IMPORTANT)
            # We always rehash the directory in development mode as the
            # user might have compiled the package manually.
            if not self.__cleanBuild:
                BobState().setResultHash(prettyBuildPath, hashWorkspace(buildStep))
        else:
            with stepExec(buildStep, "BUILD", prettyBuildPath) as a:
                # Squash state because running the step will change the
                # content. If the execution fails we have nothing reliable
                # left and we _must_ run it again.
                BobState().delInputHashes(prettyBuildPath)
                BobState().setResultHash(prettyBuildPath, datetime.datetime.utcnow())
                # build it
                await self._runShell(buildStep, "build", self.__cleanBuild, a)
                buildHash = hashWorkspace(buildStep)
            await self._generateAudit(buildStep, depth, buildHash)
            BobState().setResultHash(prettyBuildPath, buildHash)
            BobState().setVariantId(prettyBuildPath, buildDigest[0])
            BobState().setInputHashes(prettyBuildPath, buildInputHashes)

    def _preparePackageStep(self, packageStep):
        # get directory into shape
        (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
        packageDigest = packageStep.getVariantId()
        oldPackageDigest = BobState().getDirectoryState(prettyPackagePath, False)
        if created or (packageDigest != oldPackageDigest):
            # not created but exists -> something different -> prune workspace
            if not created and os.path.exists(prettyPackagePath):
                stepMessage(packageStep, "PRUNE", "{} (recipe changed)".format(prettyPackagePath),
                    WARNING)
                emptyDirectory(prettyPackagePath)
            # invalidate result if folder was created
            BobState().resetWorkspaceState(prettyPackagePath, packageDigest)

    async def _downloadPackage(self, packageStep, depth, packageBuildId):
        # Dissect input parameters that lead to current workspace the last time
        prettyPackagePath = packageStep.getWorkspacePath()
        oldWasDownloaded, oldInputHashes, oldInputBuildId = \
            dissectPackageInputState(BobState().getInputHashes(prettyPackagePath))

        # If possible try to download the package. If we downloaded the
        # package in the last run we have to make sure that the Build-Id is
        # still the same. The overall behaviour should look like this:
        #
        # new workspace -> try download
        # previously built
        #   still same build-id -> normal build
        #   build-id changed -> prune and try download, fall back to build
        # previously downloaded
        #   still same build-id -> done
        #   build-id changed -> prune and try download, fall back to build
        workspaceChanged = False
        wasDownloaded = False
        packageDigest = packageStep.getVariantId()
        if depth >= self.__downloadDepth or (self.__downloadPackages and
                self.__downloadPackages.search(packageStep.getPackage().getName())):
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
                audit = os.path.join(prettyPackagePath, "..", "audit.json.gz")
                wasDownloaded = await self.__archive.downloadPackage(packageStep,
                    packageBuildId, audit, prettyPackagePath)
                if wasDownloaded:
                    self.__statistic.packagesDownloaded += 1
                    BobState().setInputHashes(prettyPackagePath,
                        packageInputDownloaded(packageBuildId))
                    packageHash = hashWorkspace(packageStep)
                    workspaceChanged = True
                    wasDownloaded = True
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

        return wasDownloaded

    async def _cookPackageStep(self, packageStep, checkoutOnly, depth, mayUpOrDownload, packageBuildId):
        # Dissect input parameters that lead to current workspace the last time
        prettyPackagePath = packageStep.getWorkspacePath()
        oldWasDownloaded, oldInputHashes, oldInputBuildId = \
            dissectPackageInputState(BobState().getInputHashes(prettyPackagePath))

        # Take checkout step into account because it is guaranteed to
        # be available and the build step might reference it (think of
        # "make -C" or cross-workspace symlinks.
        workspaceChanged = False
        packageInputs = [ packageStep.getPackage().getCheckoutStep() ]
        packageInputs.extend(packageStep.getAllDepSteps())
        packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
            for i in packageInputs if i.isValid() ]
        packageFingerprint = await self._getFingerprint(packageStep)
        if packageFingerprint: packageInputHashes.append(packageFingerprint)

        # Run package step if we have not yet downloaded the package or if
        # downloads are not possible anymore. Even if the package was
        # previously downloaded the oldInputHashes will be None to trigger
        # an actual build.
        if checkoutOnly:
            stepMessage(packageStep, "PACKAGE", "skipped due to --checkout-only ({})".format(prettyPackagePath),
                SKIPPED, IMPORTANT)
        elif (not self.__force) and (oldInputHashes == packageInputHashes):
            stepMessage(packageStep, "PACKAGE", "skipped (unchanged input for {})".format(prettyPackagePath),
                SKIPPED, IMPORTANT)
        else:
            with stepExec(packageStep, "PACKAGE", prettyPackagePath) as a:
                # invalidate result because folder will be cleared
                BobState().delInputHashes(prettyPackagePath)
                BobState().setResultHash(prettyPackagePath, datetime.datetime.utcnow())
                await self._runShell(packageStep, "package", True, a)
                packageHash = hashWorkspace(packageStep)
                packageDigest = self.__getIncrementalVariantId(packageStep)
                workspaceChanged = True
                self.__statistic.packagesBuilt += 1
            audit = await self._generateAudit(packageStep, depth, packageHash)
            if mayUpOrDownload and self.__archive.canUploadLocal():
                await self.__archive.uploadPackage(packageStep, packageBuildId,
                    audit, prettyPackagePath)

        # Rehash directory if content was changed
        if workspaceChanged:
            BobState().setResultHash(prettyPackagePath, packageHash)
            BobState().setVariantId(prettyPackagePath, packageDigest)
            BobState().setInputHashes(prettyPackagePath,
                packageInputBuilt(packageBuildId, packageInputHashes))

    async def __queryLiveBuildId(self, step):
        """Predict live build-id of checkout step.

        Query the SCMs for their live-buildid and cache the result. Normally
        the current result is retuned unless we're in build-only mode. Then the
        cached result is used. Only if there is no cached entry the query is
        performed.
        """

        key = b'\x00' + step._getSandboxVariantId()
        if self.__buildOnly:
            liveBId = BobState().getBuildId(key)
            if liveBId is not None: return liveBId

        liveBId = await step.predictLiveBuildId()
        if liveBId is not None:
            BobState().setBuildId(key, liveBId)
        return liveBId

    def __invalidateLiveBuildId(self, step):
        """Invalidate last live build-id of a step."""

        key = b'\x00' + step._getSandboxVariantId()
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

        bid = await self.__archive.downloadLocalLiveBuildId(step, liveBId)
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
           step.hasLiveBuildId() and self.__archive.canDownloadLocal():
            with stepAction(step, "QUERY", step.getPackage().getName(), (IMPORTANT, NORMAL)) as a:
                liveBId = await self.__queryLiveBuildId(step)
                if liveBId:
                    ret = await self.__translateLiveBuildId(step, liveBId)
                if ret is None:
                    a.fail("unknown", WARNING)

        # do the checkout if we still don't have a build-id
        if ret is None:
            await self._cook([step], step.getPackage(), depth)
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
                self.__createTask(lambda s=step: self.__getBuildIdTask(s, depth), step, False, self.__buildIdTasks)
                for step in steps
            ]
            ret = await self.__yieldJobWhile(gatherTasks(tasks), True)
        else:
            tasks = []
            for step in steps:
                task = self.__createTask(lambda s=step: self.__getBuildIdTask(s, depth),
                                         step, False, self.__buildIdTasks)
                tasks.append(task)
                await self.__yieldJobWhile(asyncio.wait({task}), True)
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
                fingerprint = await self._getFingerprint(step)
                ret = await step.getDigestCoro(lambda x: self.__getBuildIdList(x, depth+1),
                    True, fingerprint=fingerprint)
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
        key = (step.getWorkspacePath(), step.getVariantId())

        # Invalidate wrong live-build-id
        self.__invalidateLiveBuildId(step)

        # Invalidate (possibly) derived build-ids
        self.__srcBuildIds[key] = (checkoutHash, False)
        self.__buildDistBuildIds = {}

        # Forget all executed build- and package-steps
        self._clearWasRun()
        self._clearDownloadTried()

        # start from scratch
        raise RestartBuildException()

    def __getIncrementalVariantId(self, step):
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

        def getStoredVId(dep):
            ret = BobState().getVariantId(dep.getWorkspacePath())
            if ret is None:
                ret = dep.getVariantId()
            return ret

        return step.getDigest(getStoredVId)

    async def __yieldJobWhile(self, coro, ignoreExecutionStop = False):
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
                except concurrent.futures.CancelledError:
                    pass
        if not self.__running and not ignoreExecutionStop: raise CancelBuildException
        return ret

    async def _getFingerprint(self, step):
        # Inside a sandbox the situation is clear. The variant-id and build-id
        # are already tied directly to the sandbox variant-id by getDigest().
        # This is pessimistic but easier than calculating the fingerprint
        # inside the sandbox.
        if step.getSandbox() is not None:
            return b''

        # A relocatable step with no fingerprinting is easy
        isFingerprinted = step._isFingerprinted()
        trackRelocation = step.isPackageStep() and not step.isRelocatable() and \
            self.__recipes.getPolicy('allRelocatable')
        if not isFingerprinted and not trackRelocation:
            return b''

        # Execute the fingerprint script (or use cached result)
        if isFingerprinted:
            script = step._getFingerprintScript()
            fingerprint = self.__fingerprints.get(script)
            if fingerprint is None:
                with stepAction(step, "FNGRPRNT", step.getPackage().getName(), DEBUG) as a:
                    fingerprint = await self.__runFingerprintScript(script, a)
                self.__fingerprints[script] = fingerprint
        else:
            fingerprint = b''

        # If the package is not relocatable the exec path is mixed into the
        # fingerprint to tag the relocation information at the artifact.
        if trackRelocation:
            fingerprint += step.getExecPath().encode(
                locale.getpreferredencoding(False), 'replace')

        return hashlib.sha1(fingerprint).digest()

    async def __runFingerprintScript(self, script, logger):
        if self.__preserveEnv:
            runEnv = os.environ.copy()
        else:
            runEnv = { k:v for (k,v) in os.environ.items() if k in self.__envWhiteList }

        stdout = stderr = None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                runEnv["BOB_CWD"] = tmp
                proc = await asyncio.create_subprocess_exec("/bin/bash", "-x", "-c",
                    script, cwd=tmp, env=runEnv, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as e:
                raise BuildError("Cannot execute fingerprint script: {}"
                                    .format(str(e)))

            while stdout is None:
                try:
                    stdout,stderr = await proc.communicate()
                except concurrent.futures.CancelledError:
                    pass

        ret = proc.returncode
        if ret != 0:
            help = "Script output: {}\nScript: {}".format(
                stderr.decode(locale.getpreferredencoding(False), 'replace').strip(),
                script)
            raise BuildError("Fingerprint script returned with {}".format(ret),
                help=help)

        log = repr(stdout.decode(locale.getpreferredencoding(False), 'replace').strip())
        if len(log) > 43: log = log[:40] + "..."
        logger.setResult(log)
        return stdout
