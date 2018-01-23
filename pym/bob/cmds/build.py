# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .. import BOB_VERSION
from ..archive import DummyArchive, getArchiver
from ..audit import Audit
from ..errors import BobError, BuildError, ParseError
from ..input import RecipeSet
from ..state import BobState
from ..tty import colorize
from ..utils import asHexStr, hashDirectory, hashFile, removePath, \
emptyDirectory, copyTree, isWindows
from datetime import datetime
from glob import glob
from pipes import quote
from textwrap import dedent
import argparse
import datetime
import os
import re
import shutil
import stat
import subprocess
import time

# Output verbosity:
#    <= -2: package name
#    == -1: package name, package steps
#    ==  0: package name, package steps, stderr
#    ==  1: package name, package steps, stderr, stdout
#    ==  2: package name, package steps, stderr, stdout, set -x

def hashWorkspace(step):
return hashDirectory(step.getWorkspacePath(),
    os.path.join(step.getWorkspacePath(), "..", "cache.bin"))

def runHook(recipes, hook, args):
hookCmd = recipes.getBuildHook(hook)
ret = True
if hookCmd:
    try:
        hookCmd = os.path.expanduser(hookCmd)
        ret = subprocess.call([hookCmd] + args) == 0
    except OSError as e:
        raise BuildError(hook + ": cannot run '" + hookCmd + ": " + str(e))

return ret

class RestartBuildException(Exception):
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
    if [[ $_keep_env = 1 ]] ; then
        exec /usr/bin/env {ENV} "$0" "${{_args[@]}}" __shell
    else
        exec /usr/bin/env -i {WHITELIST} {ENV} "$0" "${{_args[@]}}" __shell
    fi
    ;;
__shell)
    cd "${{0%/*}}/workspace"
    rm -f ../audit.json.gz
    if [[ $_keep_env = 1 ]] ; then
        run /bin/bash -s {ARGS}
    else
        run /bin/bash --norc -s {ARGS}
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
def developNamePersister(wrapFmt):
    """Creates a separate directory for every recipe and step variant.

    Only identical steps of the same recipe are put into the same
    directory. In contrast to the releaseNamePersister() identical steps of
    different recipes are put into distinct directories.
    """
    dirs = {}

    def fmt(step, props):
        baseDir = wrapFmt(step, props)
        digest = (step.getPackage().getRecipe().getName(), step.getVariantId())
        if digest in dirs:
            res = dirs[digest]
        else:
            num = dirs.setdefault(baseDir, 0) + 1
            res = os.path.join(baseDir, str(num))
            dirs[baseDir] = num
            dirs[digest] = res
        return res

    return fmt

@staticmethod
def makeRunnable(wrapFmt):
    baseDir = os.getcwd()

    def fmt(step, mode, props):
        if mode == 'workspace':
            ret = wrapFmt(step, props)
        else:
            assert mode == 'exec'
            if step.getSandbox() is None:
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
    self.__verbose = max(-2, min(3, verbose))
    self.__noLogFile = noLogFile
    self.__force = force
    self.__skipDeps = skipDeps
    self.__buildOnly = buildOnly
    self.__preserveEnv = preserveEnv
    self.__envWhiteList = envWhiteList
    self.__currentPackage = None
    self.__archive = DummyArchive()
    self.__downloadDepth = 0xffff
    self.__downloadDepthForce = 0xffff
    self.__bobRoot = bobRoot
    self.__cleanBuild = cleanBuild
    self.__cleanCheckout = False
    self.__srcBuildIds = {}
    self.__buildDistBuildIds = {}
    self.__statistic = LocalBuilderStatistic()
    self.__alwaysCheckout = []
    self.__licenseScanners = []

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
    else:
        assert mode == 'no'
        self.__archive.wantDownload(False)

def setUploadMode(self, mode):
    self.__archive.wantUpload(mode)

def setLicenseScanners(self, licenseScanners):
    self.__licenseScanners = licenseScanners

def setCleanCheckout(self, clean):
    self.__cleanCheckout = clean

def setAlwaysCheckout(self, alwaysCheckout):
    self.__alwaysCheckout = [ re.compile(e) for e in alwaysCheckout ]

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
    self.__wasRun = dict(state['wasRun'])
    self.__srcBuildIds = { (path, vid) : (bid, True)
        for (path, vid), bid in state['predictedBuidId'].items() }

def _wasAlreadyRun(self, step, skippedOk=False):
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

def _constructDir(self, step, label):
    created = False
    workDir = step.getWorkspacePath()
    if not os.path.isdir(workDir):
        os.makedirs(workDir)
        created = True
    return (workDir, created)

def _generateAudit(self, step, depth, resultHash, executed=True):
    audit = Audit.create(step.getVariantId(), self._getBuildId(step, depth), resultHash)
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
                    print(colorize("   WARNING: cannot audit SCM: {} ({})"
                                        .format(e.slogan, dir),
                                   "33"))

    auditPath = os.path.join(step.getWorkspacePath(), "..", "audit.json.gz")
    audit.save(auditPath)
    return auditPath

def __linkDependencies(self, step):
    """Create symlinks to the dependency workspaces"""

    # this will only work on POSIX
    if isWindows(): return

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

def _runShell(self, step, scriptName, cleanWorkspace):
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
    runEnv.update(stepEnv)

    # sandbox
    if step.getSandbox() is not None:
        sandboxSetup = "\"$(mktemp -d)\""
        sandboxMounts = [ "declare -a mounts=( )" ]
        sandbox = [ quote(os.path.join(self.__bobRoot, "bin", "namespace-sandbox")) ]
        if self.__verbose >= 3:
            sandbox.append('-D')
        sandbox.extend(["-S", "\"$_sandbox\""])
        sandbox.extend(["-W", quote(step.getExecPath())])
        sandbox.extend(["-H", "bob"])
        sandbox.extend(["-d", "/tmp"])
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
                quote(s.getExecPath()) )) if s.isValid() else None)
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
                    quote(a.getExecPath())
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
            trap 'bob_handle_error $? >&2' ERR
            trap 'for i in "${_BOB_TMP_CLEANUP[@]-}" ; do rm -f "$i" ; done' EXIT
            set -o errtrace -o nounset -o pipefail

            # Special Bob array variables:
            """))
        print("declare -A BOB_ALL_PATHS=( {} )".format(" ".join(sorted(
            [ "[{}]={}".format(quote(a.getPackage().getName()),
                               quote(a.getExecPath()))
                for a in step.getAllDepSteps() ] ))), file=f)
        print("declare -A BOB_DEP_PATHS=( {} )".format(" ".join(sorted(
            [ "[{}]={}".format(quote(a.getPackage().getName()),
                               quote(a.getExecPath()))
                for a in step.getArguments() if a.isValid() ] ))), file=f)
        print("declare -A BOB_TOOL_PATHS=( {} )".format(" ".join(sorted(
            [ "[{}]={}".format(quote(n), quote(os.path.join(t.getStep().getExecPath(), t.getPath())))
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
    if self.__verbose < 0:
        cmdLine.append('-q')
    elif self.__verbose == 1:
        cmdLine.append('-v')
    elif self.__verbose >= 2:
        cmdLine.append('-vv')
    if self.__noLogFile:
        cmdLine.append('-n')

    try:
        proc = subprocess.Popen(cmdLine, cwd=step.getWorkspacePath(), env=runEnv)
        if proc.wait() != 0:
            raise BuildError("Build script {} returned with {}"
                                .format(absRunFile, proc.returncode),
                             help="You may resume at this point with '--resume' after fixing the error.")
    except OSError as e:
        raise BuildError("Cannot execute build script {}: {}".format(absRunFile, str(e)))
    except KeyboardInterrupt:
        raise BuildError("User aborted while running {}".format(absRunFile),
                         help = "Run again with '--resume' to skip already built packages.")

def _info(self, *args, **kwargs):
    if self.__verbose >= -1:
        print(*args, **kwargs)

def getStatistic(self):
    return self.__statistic

def cook(self, step, checkoutOnly):
    done = False
    while not done:
        try:
            self._cook([step], step.getPackage(), checkoutOnly)
            done = True
        except RestartBuildException:
            print(colorize("** Restart build due to wrongly predicted sources.", "33"))
            self.__currentPackage = None

def _cook(self, steps, parentPackage, checkoutOnly, depth=0):
    currentPackage = self.__currentPackage

    # skip everything except the current package
    if self.__skipDeps:
        steps = [ s for s in steps if s.getPackage() == parentPackage ]

    for step in reversed(steps):
        # skip if already processed steps
        if self._wasAlreadyRun(step):
            continue

        # update if package changes
        newPackage = "/".join(step.getPackage().getStack())
        if newPackage != self.__currentPackage:
            self.__currentPackage = newPackage
            print(">>", colorize(self.__currentPackage, "32;1"))

        # execute step
        try:
            if step.isCheckoutStep():
                if step.isValid():
                    self._cookCheckoutStep(step, depth)
            elif step.isBuildStep():
                if step.isValid():
                    self._cookBuildStep(step, checkoutOnly, depth)
            else:
                assert step.isPackageStep() and step.isValid()
                self._cookPackageStep(step, checkoutOnly, depth)
        except BuildError as e:
            e.setStack(step.getPackage().getStack())
            raise e

    # back to original package
    if currentPackage != self.__currentPackage:
        self.__currentPackage = currentPackage
        if currentPackage:
            print(">>", colorize(self.__currentPackage, "32;1"))

def _cookCheckoutStep(self, checkoutStep, depth):
    overrides = set()
    scmList = checkoutStep.getScmList()
    for scm in scmList:
        overrides.update(scm.getActiveOverrides())
    self.__statistic.addOverrides(overrides)
    overrides = len(overrides)
    overridesString = ("(" + str(overrides) + " scm " + ("overrides" if overrides > 1 else "override") +")") if overrides else ""

    checkoutDigest = checkoutStep.getVariantId()
    if self._wasAlreadyRun(checkoutStep):
        prettySrcPath = checkoutStep.getWorkspacePath()
        self._info("   CHECKOUT  skipped (reuse {}) {}".format(prettySrcPath, overridesString))
    else:
        # depth first
        self._cook(checkoutStep.getAllDepSteps(), checkoutStep.getPackage(),
                  False, depth+1)

        # get directory into shape
        (prettySrcPath, created) = self._constructDir(checkoutStep, "src")
        oldCheckoutState = BobState().getDirectoryState(prettySrcPath, {})
        if created:
            # invalidate result if folder was created
            BobState().delInputHashes(prettySrcPath)
            BobState().delResultHash(prettySrcPath)
            oldCheckoutState = {}
            BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

        checkoutExecuted = False
        checkoutState = checkoutStep.getScmDirectories().copy()
        checkoutState[None] = checkoutDigest
        if self.__buildOnly and (BobState().getResultHash(prettySrcPath) is not None):
            if checkoutState != oldCheckoutState:
                print(colorize("   CHECKOUT  WARNING: recipe changed but skipped due to --build-only ({})"
                    .format(prettySrcPath), "33"))
            else:
                self._info("   CHECKOUT  skipped due to --build-only ({}) {}".format(prettySrcPath, overridesString))
        else:
            if self.__cleanCheckout:
                # check state of SCMs and invalidate if the directory is dirty
                stats = {}
                for scm in checkoutStep.getScmList():
                    stats.update({ dir : scm for dir in scm.getDirectories().keys() })
                for (scmDir, scmDigest) in oldCheckoutState.copy().items():
                    if scmDir is None: continue
                    if scmDigest != checkoutState.get(scmDir): continue
                    status = stats[scmDir].status(checkoutStep.getWorkspacePath())[0]
                    if (status == 'dirty') or (status == 'error'):
                        oldCheckoutState[scmDir] = None

            checkoutInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                for i in checkoutStep.getAllDepSteps() if i.isValid() ]
            if (self.__force or (not checkoutStep.isDeterministic()) or
                (BobState().getResultHash(prettySrcPath) is None) or
                (checkoutState != oldCheckoutState) or
                (checkoutInputHashes != BobState().getInputHashes(prettySrcPath))):
                # move away old or changed source directories
                for (scmDir, scmDigest) in oldCheckoutState.copy().items():
                    if (scmDir is not None) and (scmDigest != checkoutState.get(scmDir)):
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        if os.path.exists(scmPath):
                            atticName = datetime.datetime.now().isoformat()+"_"+os.path.basename(scmPath)
                            print(colorize("   ATTIC     {} (move to ../attic/{})".format(scmPath, atticName), "33"))
                            atticPath = os.path.join(prettySrcPath, "..", "attic")
                            if not os.path.isdir(atticPath):
                                os.makedirs(atticPath)
                            os.rename(scmPath, os.path.join(atticPath, atticName))
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

                # Check that new checkouts do not collide with old stuff in
                # workspace. Do it before we store the new SCM state to
                # check again if the step is rerun.
                for scmDir in checkoutState.keys():
                    if scmDir is None or scmDir == ".": continue
                    if oldCheckoutState.get(scmDir) is not None: continue
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

                print(colorize("   CHECKOUT  {} {}".format(prettySrcPath, overridesString)
                    , "32"))
                self._runShell(checkoutStep, "checkout", False)
                self.__statistic.checkouts += 1
                checkoutExecuted = True
                # reflect new checkout state
                BobState().setDirectoryState(prettySrcPath, checkoutState)
                BobState().setInputHashes(prettySrcPath, checkoutInputHashes)
            else:
                self._info("   CHECKOUT  skipped (fixed package {})".format(prettySrcPath))

        # We always have to rehash the directory as the user might have
        # changed the source code manually.
        oldCheckoutHash = BobState().getResultHash(prettySrcPath)
        checkoutHash = hashWorkspace(checkoutStep)
        BobState().setResultHash(prettySrcPath, checkoutHash)

        self._setAlreadyRun(checkoutStep, True)

        # Generate audit trail. Has to be done _after_ setResultHash()
        # because the result is needed to calculate the buildId.
        if (checkoutHash != oldCheckoutHash) or checkoutExecuted:
            self._generateAudit(checkoutStep, depth, checkoutHash, checkoutExecuted)

        # upload live build-id cache in case of fresh checkout
        if created and self.__archive.canUploadLocal() and checkoutStep.hasLiveBuildId():
            liveBId = checkoutStep.calcLiveBuildId()
            if liveBId is not None:
                self.__archive.uploadLocalLiveBuildId(liveBId, checkoutHash, self.__verbose)

        # Predicted build-id and real one after checkout do not need to
        # match necessarily. Handle it as some build results might be
        # inconsistent to the sources now.
        buildId, predicted = self.__srcBuildIds.get((prettySrcPath, checkoutDigest),
            (checkoutHash, False))
        if buildId != checkoutHash:
            assert predicted, "Non-predicted incorrect Build-Id found!"
            self.__handleChangedBuildId(checkoutStep, checkoutHash)

            # Trigger spdx generation
            for s in self.__licenseScanners:
                s.getLicenseFile(self.__currentPackage, prettySrcPath)

    def _cookBuildStep(self, buildStep, checkoutOnly, depth):
        # Include actual directories of dependencies in buildDigest.
        # Directories are reused in develop build mode and thus might change
        # even though the variant id of this step is stable. As most tools rely
        # on stable input directories we have to make a clean build if any of
        # the dependency directories change.
        buildDigest = [buildStep.getVariantId()] + [
            i.getExecPath() for i in buildStep.getArguments() if i.isValid() ]
        if self._wasAlreadyRun(buildStep, checkoutOnly):
            prettyBuildPath = buildStep.getWorkspacePath()
            self._info("   BUILD     skipped (reuse {})".format(prettyBuildPath))
        else:
            # depth first
            self._cook(buildStep.getAllDepSteps(), buildStep.getPackage(),
                       checkoutOnly, depth+1)

            # get directory into shape
            (prettyBuildPath, created) = self._constructDir(buildStep, "build")
            oldBuildDigest = BobState().getDirectoryState(prettyBuildPath)
            if created or (buildDigest != oldBuildDigest):
                # not created but exists -> something different -> prune workspace
                if not created and os.path.exists(prettyBuildPath):
                    print(colorize("   PRUNE     {} (recipe changed)".format(prettyBuildPath), "33"))
                    emptyDirectory(prettyBuildPath)
                # invalidate build step
                BobState().delInputHashes(prettyBuildPath)
                BobState().delResultHash(prettyBuildPath)

            if buildDigest != oldBuildDigest:
                BobState().setDirectoryState(prettyBuildPath, buildDigest)

            # run build if input has changed
            buildInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                for i in buildStep.getAllDepSteps() if i.isValid() ]
            if checkoutOnly:
                self._info("   BUILD     skipped due to --checkout-only ({})".format(prettyBuildPath))
            elif (not self.__force) and (BobState().getInputHashes(prettyBuildPath) == buildInputHashes):
                self._info("   BUILD     skipped (unchanged input for {})".format(prettyBuildPath))
                # We always rehash the directory in development mode as the
                # user might have compiled the package manually.
                if not self.__cleanBuild:
                    BobState().setResultHash(prettyBuildPath, hashWorkspace(buildStep))
            else:
                print(colorize("   BUILD     {}".format(prettyBuildPath), "32"))
                # Squash state because running the step will change the
                # content. If the execution fails we have nothing reliable
                # left and we _must_ run it again.
                BobState().delInputHashes(prettyBuildPath)
                BobState().setResultHash(prettyBuildPath, datetime.datetime.utcnow())
                # build it
                self._runShell(buildStep, "build", self.__cleanBuild)
                buildHash = hashWorkspace(buildStep)
                self._generateAudit(buildStep, depth, buildHash)
                BobState().setResultHash(prettyBuildPath, buildHash)
                BobState().setInputHashes(prettyBuildPath, buildInputHashes)
            self._setAlreadyRun(buildStep, False, checkoutOnly)

    def _cookPackageStep(self, packageStep, checkoutOnly, depth):
        packageDigest = packageStep.getVariantId()
        if self._wasAlreadyRun(packageStep, checkoutOnly):
            prettyPackagePath = packageStep.getWorkspacePath()
            self._info("   PACKAGE   skipped (reuse {})".format(prettyPackagePath))
        else:
            # get directory into shape
            (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
            oldPackageDigest = BobState().getDirectoryState(prettyPackagePath)
            if created or (packageDigest != oldPackageDigest):
                # not created but exists -> something different -> prune workspace
                if not created and os.path.exists(prettyPackagePath):
                    print(colorize("   PRUNE     {} (recipe changed)".format(prettyPackagePath), "33"))
                    emptyDirectory(prettyPackagePath)
                # invalidate result if folder was created
                BobState().delInputHashes(prettyPackagePath)
                BobState().delResultHash(prettyPackagePath)

            if packageDigest != oldPackageDigest:
                BobState().setDirectoryState(prettyPackagePath, packageDigest)

            # Can we theoretically download the result? This requires a
            # relocatable package or that we're building in a sandbox with
            # stable paths. Try to determine a build-id for these artifacts.
            if packageStep.isRelocatable() or (packageStep.getSandbox() is not None):
                packageBuildId = self._getBuildId(packageStep, depth)
            else:
                packageBuildId = None

            # If we download the package in the last run the Build-Id is stored
            # as input hash. Otherwise the input hashes of the package step is
            # a list with the buildId as first element. Split that off for the
            # logic below...
            oldInputBuildId = BobState().getInputHashes(prettyPackagePath)
            if (isinstance(oldInputBuildId, list) and (len(oldInputBuildId) >= 1)):
                oldInputHashes = oldInputBuildId[1:]
                oldInputBuildId = oldInputBuildId[0]
                oldWasDownloaded = False
            elif isinstance(oldInputBuildId, bytes):
                oldWasDownloaded = True
                oldInputHashes = None
            else:
                # created by old Bob version or new workspace
                oldInputHashes = oldInputBuildId
                oldWasDownloaded = False

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
            if ( (not checkoutOnly) and packageBuildId and (depth >= self.__downloadDepth) ):
                # prune directory if we previously downloaded/built something different
                if ((oldInputBuildId is not None) and (oldInputBuildId != packageBuildId)) or self.__force:
                    print(colorize("   PRUNE     {} ({})".format(prettyPackagePath,
                            "build forced" if self.__force else "build-id changed"), "33"))
                    emptyDirectory(prettyPackagePath)
                    BobState().delInputHashes(prettyPackagePath)
                    BobState().delResultHash(prettyPackagePath)
                    oldInputBuildId = None
                    oldInputHashes = None

                # Try to download the package if the directory is currently
                # empty. If the directory holds a result and was downloaded it
                # we're done.
                if BobState().getResultHash(prettyPackagePath) is None:
                    audit = os.path.join(prettyPackagePath, "..", "audit.json.gz")
                    if self.__archive.downloadPackage(packageBuildId, audit, prettyPackagePath, self.__verbose):
                        self.__statistic.packagesDownloaded += 1
                        BobState().setInputHashes(prettyPackagePath, packageBuildId)
                        packageHash = hashWorkspace(packageStep)
                        workspaceChanged = True
                        wasDownloaded = True
                    elif depth >= self.__downloadDepthForce:
                        raise BuildError("Downloading artifact failed")
                elif oldWasDownloaded:
                    self._info("   PACKAGE   skipped (already downloaded in {})".format(prettyPackagePath))
                    wasDownloaded = True

            # Run package step if we have not yet downloaded the package or if
            # downloads are not possible anymore. Even if the package was
            # previously downloaded the oldInputHashes will be None to trigger
            # an actual build.
            if not wasDownloaded:
                # depth first
                self._cook(packageStep.getAllDepSteps(), packageStep.getPackage(),
                           checkoutOnly, depth+1)

                # Take checkout step into account because it is guaranteed to
                # be available and the build step might reference it (think of
                # "make -C" or cross-workspace symlinks.
                packageInputs = [ packageStep.getPackage().getCheckoutStep() ]
                packageInputs.extend(packageStep.getAllDepSteps())
                packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                    for i in packageInputs if i.isValid() ]
                if checkoutOnly:
                    self._info("   PACKAGE   skipped due to --checkout-only ({})".format(prettyPackagePath))
                elif (not self.__force) and (oldInputHashes == packageInputHashes):
                    self._info("   PACKAGE   skipped (unchanged input for {})".format(prettyPackagePath))
                else:
                    print(colorize("   PACKAGE   {}".format(prettyPackagePath), "32"))
                    # invalidate result because folder will be cleared
                    BobState().delInputHashes(prettyPackagePath)
                    BobState().setResultHash(prettyPackagePath, datetime.datetime.utcnow())
                    self._runShell(packageStep, "package", True)
                    packageHash = hashWorkspace(packageStep)
                    audit = self._generateAudit(packageStep, depth, packageHash)
                    workspaceChanged = True
                    self.__statistic.packagesBuilt += 1
                    if packageBuildId and self.__archive.canUploadLocal():
                        self.__archive.uploadPackage(packageBuildId, audit, prettyPackagePath, self.__verbose)

            # Rehash directory if content was changed
            if workspaceChanged:
                BobState().setResultHash(prettyPackagePath, packageHash)
                if wasDownloaded:
                    BobState().setInputHashes(prettyPackagePath, packageBuildId)
                else:
                    BobState().setInputHashes(prettyPackagePath, [packageBuildId] + packageInputHashes)
            self._setAlreadyRun(packageStep, False, checkoutOnly)

    def __queryLiveBuildId(self, step):
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

        if self.__verbose > 0:
            print(colorize("   QUERY-SRC {} .. ".format(step.getPackage().getName()), "32"), end="")
        liveBId = None
        try:
            liveBId = step.predictLiveBuildId()
        finally:
            if self.__verbose > 0:
                if liveBId is None:
                    print(colorize("unknown", "33"))
                else:
                    print(colorize("ok", "32"))

        if liveBId is not None:
            BobState().setBuildId(key, liveBId)
        return liveBId

    def __invalidateLiveBuildId(self, step):
        """Invalidate last live build-id of a step."""

        key = b'\x00' + step._getSandboxVariantId()
        liveBId = BobState().getBuildId(key)
        if liveBId is not None:
            BobState().delBuildId(key)

    def __translateLiveBuildId(self, liveBId):
        """Translate live build-id into real build-id.

        We maintain a local cache of previous translations. In case of a cache
        miss the archive is interrogated. A valid result is cached.
        """
        key = b'\x01' + liveBId
        bid = BobState().getBuildId(key)
        if bid is not None:
            return bid

        bid = self.__archive.downloadLocalLiveBuildId(liveBId, self.__verbose)
        if bid is not None:
            BobState().setBuildId(key, bid)

        return bid

    def __getCheckoutStepBuildId(self, step, depth):
        ret = None
        v = (self.__verbose >= -1) and (self.__verbose < 1)

        # Try to use live build-ids for checkout steps. Do not use them if
        # there is already a workspace or if the package matches one of the
        # 'always-checkout' patterns. Fall back to a regular checkout if any
        # condition is not met.
        name = step.getPackage().getName()
        if not os.path.exists(step.getWorkspacePath()) and \
           not any(pat.match(name) for pat in self.__alwaysCheckout) and \
           step.hasLiveBuildId() and self.__archive.canDownloadLocal():
            if v:
                print(colorize("   QUERY     {} .. ".format(step.getPackage().getName()), "32"), end="")
            try:
                liveBId = self.__queryLiveBuildId(step)
                if liveBId:
                    ret = self.__translateLiveBuildId(liveBId)
            finally:
                if v:
                    if ret is None:
                        print(colorize("unknown", "33"))
                    else:
                        print(colorize("ok", "32"))

        # do the checkout if we still don't have a build-id
        if ret is None:
            # do checkout
            self._cook([step], step.getPackage(), depth)
            # return directory hash
            ret = BobState().getResultHash(step.getWorkspacePath())
            predicted = False
        else:
            predicted = True

        return ret, predicted

    def _getBuildId(self, step, depth):
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
        path = step.getWorkspacePath()
        if step.isCheckoutStep():
            key = (path, step.getVariantId())
            ret, predicted = self.__srcBuildIds.get(key, (None, False))
            if ret is None:
                tmp = self.__getCheckoutStepBuildId(step, depth)
                self.__srcBuildIds[key] = tmp
                ret = tmp[0]
        else:
            ret = self.__buildDistBuildIds.get(path)
            if ret is None:
                ret = step.getDigest(lambda s: self._getBuildId(s, depth+1), True)
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

        # start from scratch
        raise RestartBuildException()


def touch(packages):
    done = set()
    def touchStep(step):
        if step in done: return
        done.add(step)
        for d in step.getAllDepSteps():
            if d.isValid(): touchStep(d)
        step.getWorkspacePath()

    touchStep(packages.getRootPackage().getPackageStep())


def commonBuildDevelop(parser, argv, bobRoot, develop):
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to build")
    parser.add_argument('--destination', metavar="DEST", default=None,
        help="Destination of build result (will be overwritten!)")
    parser.add_argument('-f', '--force', default=None, action='store_true',
        help="Force execution of all build steps")
    parser.add_argument('-n', '--no-deps', default=None, action='store_true',
        help="Don't build dependencies")
    parser.add_argument('-p', '--with-provided', dest='build_provided', default=None, action='store_true',
        help="Build provided dependencies")
    parser.add_argument('--without-provided', dest='build_provided', default=None, action='store_false',
        help="Build without provided dependencies")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-b', '--build-only', dest='build_mode', default=None,
        action='store_const', const='build-only',
        help="Don't checkout, just build and package")
    group.add_argument('-B', '--checkout-only', dest='build_mode',
        action='store_const', const='checkout-only',
        help="Don't build, just check out sources")
    group.add_argument('--normal', dest='build_mode',
        action='store_const', const='normal',
        help="Checkout, build and package")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--clean', action='store_true', default=None,
        help="Do clean builds (clear build directory)")
    group.add_argument('--incremental', action='store_false', dest='clean',
        help="Reuse build directory for incremental builds")
    parser.add_argument('--always-checkout', default=[], action='append', metavar="RE",
        help="Regex pattern of packages that should always be checked out")
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('--no-logfiles', default=None, action='store_true',
        help="Disable logFile generation.")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('--upload', default=None, action='store_true',
        help="Upload to binary archive")
    parser.add_argument('--download', metavar="MODE", default=None,
        help="Download from binary archive (yes, no, deps, forced, forced-deps, forced-fallback)",
        choices=['yes', 'no', 'deps', 'forced', 'forced-deps', 'forced-fallback'])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=None,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    parser.add_argument('--clean-checkout', action='store_true', default=None, dest='clean_checkout',
        help="Do a clean checkout if SCM state is dirty.")
    parser.add_argument('--licenseScan', default=False, action='store_true',
        help="Run License Scanners.")
    args = parser.parse_args(argv)

    defines = {}
    for define in args.defines:
        d = define.split("=")
        if len(d) == 1:
            defines[d[0]] = ""
        elif len(d) == 2:
            defines[d[0]] = d[1]
        else:
            parser.error("Malformed define: "+define)

    startTime = time.time()

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', LocalBuilder.developNamePersister)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    # if arguments are not passed on cmdline use them from default.yaml or set to default yalue
    if develop:
        cfg = recipes.getCommandConfig().get('dev', {})
    else:
        cfg = recipes.getCommandConfig().get('build', {})

    defaults = {
            'destination' : '',
            'force' : False,
            'no_deps' : False,
            'build_mode' : 'normal',
            'clean' : not develop,
            'upload' : False,
            'download' : "deps" if develop else "yes",
            'sandbox' : not develop,
            'clean_checkout' : False,
            'no_logfiles' : False,
            'licenseScan' : False
        }

    for a in vars(args):
        if getattr(args, a) == None:
            setattr(args, a, cfg.get(a, defaults.get(a)))

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    if develop:
        nameFormatter = recipes.getHook('developNameFormatter')
        developPersister = recipes.getHook('developNamePersister')
        nameFormatter = developPersister(nameFormatter)
    else:
        nameFormatter = recipes.getHook('releaseNameFormatter')
        nameFormatter = LocalBuilder.releaseNamePersister(nameFormatter)
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)
    packages = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if develop:
        touch(packages)

    builder = LocalBuilder(recipes, cfg.get('verbosity', 0) + args.verbose - args.quiet, args.force,
                           args.no_deps, True if args.build_mode == 'build-only' else False,
                           args.preserve_env, envWhiteList, bobRoot, args.clean,
                           args.no_logfiles)

    builder.setArchiveHandler(getArchiver(recipes))
    if args.licenseScan:
        from ..licenseScanners.fossyScanner import FossologyLicenseScanner
        scanners = [ FossologyLicenseScanner ]
        scanners.extend(recipes.getLicenseScanners())
        builder.setLicenseScanners(scanners)
    builder.setUploadMode(args.upload)
    builder.setDownloadMode(args.download)
    builder.setCleanCheckout(args.clean_checkout)
    builder.setAlwaysCheckout(args.always_checkout + cfg.get('always_checkout', []))
    if args.resume: builder.loadBuildState()

    backlog = []
    results = []
    for p in args.packages:
        for package in packages.queryPackagePath(p):
            packageStep = package.getPackageStep()
            backlog.append(packageStep)
            # automatically include provided deps when exporting
            build_provided = (args.destination and args.build_provided == None) or args.build_provided
            if build_provided: backlog.extend(packageStep._getProvidedDeps())

    success = runHook(recipes, 'preBuildHook',
        ["/".join(p.getPackage().getStack()) for p in backlog])
    if not success:
        raise BuildError("preBuildHook failed!",
            help="A preBuildHook is set but it returned with a non-zero status.")
    success = False
    try:
        for p in backlog:
            builder.cook(p, True if args.build_mode == 'checkout-only' else False)
            resultPath = p.getWorkspacePath()
            if resultPath not in results:
                results.append(resultPath)
        success = True
    finally:
        builder.saveBuildState()
        runHook(recipes, 'postBuildHook', ["success" if success else "fail"] + results)

    # tell the user
    if results:
        if len(results) == 1:
            print("Build result is in", results[0])
        else:
            print("Build results are in:\n  ", "\n   ".join(results))

        endTime = time.time()
        stats = builder.getStatistic()
        activeOverrides = len(stats.getActiveOverrides())
        print("Duration: " + str(datetime.timedelta(seconds=(endTime - startTime))) + ", "
                + str(stats.checkouts)
                    + " checkout" + ("s" if (stats.checkouts != 1) else "")
                    + " (" + str(activeOverrides) + (" overrides" if (activeOverrides != 1) else " override") + " active), "
                + str(stats.packagesBuilt)
                    + " package" + ("s" if (stats.packagesBuilt != 1) else "") + " built, "
                + str(stats.packagesDownloaded) + " downloaded.")

        # copy build result if requested
        ok = True
        if args.destination:
            for result in results:
                ok = copyTree(result, args.destination) and ok
        if not ok:
            raise BuildError("Could not copy everything to destination. Your aggregated result is probably incomplete.")
    else:
        print("Your query matched no packages. Naptime!")

def doBuild(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob build", description='Build packages in release mode.')
    commonBuildDevelop(parser, argv, bobRoot, False)

def doDevelop(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob dev", description='Build packages in development mode.')
    commonBuildDevelop(parser, argv, bobRoot, True)

def doProject(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob project", description='Generate Project Files')
    parser.add_argument('projectGenerator', nargs='?', help="Generator to use.")
    parser.add_argument('package', nargs='?', help="Sub-package that is the root of the project")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for project generator")

    parser.add_argument('--list', default=False, action='store_true', help="List available Generators")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('--download', metavar="MODE", default="no",
        help="Download from binary archive (yes, no, deps)", choices=['yes', 'no', 'deps'])
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-n', dest="execute_prebuild", default=True, action='store_false',
        help="Do not build (bob dev) before generate project Files. RunTargets may not work")
    parser.add_argument('-b', dest="execute_buildonly", default=False, action='store_true',
        help="Do build only (bob dev -b) before generate project Files. No checkout")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=False,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    args = parser.parse_args(argv)

    defines = {}
    for define in args.defines:
        d = define.split("=")
        if len(d) == 1:
            defines[d[0]] = ""
        elif len(d) == 2:
            defines[d[0]] = d[1]
        else:
            parser.error("Malformed define: "+define)

    recipes = RecipeSet()
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', LocalBuilder.developNamePersister)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    nameFormatter = recipes.getHook('developNameFormatter')
    developPersister = recipes.getHook('developNamePersister')
    nameFormatter = developPersister(nameFormatter)
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)
    packages = recipes.generatePackages(nameFormatter, defines, sandboxEnabled=args.sandbox)
    touch(packages)

    from ..generators.QtCreatorGenerator import qtProjectGenerator
    from ..generators.EclipseCdtGenerator import eclipseCdtGenerator
    generators = { 'qt-creator' : qtProjectGenerator , 'eclipseCdt' : eclipseCdtGenerator }
    generators.update(recipes.getProjectGenerators())

    if args.list:
        for g in generators:
            print(g)
        return 0
    else:
        if not args.package or not args.projectGenerator:
            raise BobError("The following arguments are required: projectGenerator, package")

    try:
        generator = generators[args.projectGenerator]
    except KeyError:
        raise BobError("Generator '{}' not found!".format(args.projectGenerator))

    extra = [ "--download=" + args.download ]
    for d in args.defines:
        extra.append('-D')
        extra.append(d)
    for c in args.configFile:
        extra.append('-c')
        extra.append(c)
    for e in args.white_list:
        extra.append('-e')
        extra.append(e)
    if args.preserve_env: extra.append('-E')
    if args.sandbox: extra.append('--sandbox')

    package = packages.walkPackagePath(args.package)

    # execute a bob dev with the extra arguments to build all executables.
    # This makes it possible for the plugin to collect them and generate some runTargets.
    if args.execute_prebuild:
        devArgs = extra.copy()
        if args.resume: devArgs.append('--resume')
        if args.execute_buildonly: devArgs.append('-b')
        devArgs.append(args.package)
        doDevelop(devArgs, bobRoot)

    print(">>", colorize("/".join(package.getStack()), "32;1"))
    print(colorize("   PROJECT   {} ({})".format(args.package, args.projectGenerator), "32"))
    generator(package, args.args, extra)

def doStatus(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob status", description='Show SCM status')
    parser.add_argument('packages', nargs='+', help="(Sub-)packages")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_true',  dest='develop', help="Use developer mode", default=True)
    group.add_argument('--release', action='store_false', dest='develop', help="Use release mode")

    parser.add_argument('-r', '--recursive', default=False, action='store_true',
                        help="Recursively display dependencies")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('-v', '--verbose', default=1, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('--show-overrides', default=False, action='store_true', dest='show_overrides',
        help="Show scm override status")
    args = parser.parse_args(argv)

    defines = {}
    for define in args.defines:
        d = define.split("=")
        if len(d) == 1:
            defines[d[0]] = ""
        elif len(d) == 2:
            defines[d[0]] = d[1]
        else:
            parser.error("Malformed define: "+define)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', LocalBuilder.developNamePersister)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    if args.develop:
        # Develop names are stable. All we need to do is to replicate build's algorithm,
        # and when we produce a name, check whether it exists.
        nameFormatter = recipes.getHook('developNameFormatter')
        developPersister = recipes.getHook('developNamePersister')
        nameFormatter = developPersister(nameFormatter)
    else:
        # Release names are taken from persistence.
        nameFormatter = LocalBuilder.releaseNameInterrogator
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)

    packages = recipes.generatePackages(nameFormatter, defines, not args.develop)
    if args.develop:
       touch(packages)

    def showStatus(package, recurse, verbose, done):
        checkoutStep = package.getCheckoutStep()
        if checkoutStep.isValid() and (not checkoutStep.getVariantId() in done):
            done.add(checkoutStep.getVariantId())
            print(">>", colorize("/".join(package.getStack()), "32;1"))
            if checkoutStep.getWorkspacePath() is not None:
                oldCheckoutState = BobState().getDirectoryState(checkoutStep.getWorkspacePath(), {})
                if not os.path.isdir(checkoutStep.getWorkspacePath()):
                    oldCheckoutState = {}
                checkoutState = checkoutStep.getScmDirectories().copy()
                stats = {}
                for scm in checkoutStep.getScmList():
                    stats.update({ dir : scm for dir in scm.getDirectories().keys() })
                for (scmDir, scmDigest) in sorted(oldCheckoutState.copy().items(), key=lambda a:'' if a[0] is None else a[0]):
                    if scmDir is None: continue
                    if scmDigest != checkoutState.get(scmDir): continue
                    status, shortStatus, longStatus = stats[scmDir].status(checkoutStep.getWorkspacePath())
                    if (status == 'clean') or (status == 'empty'):
                        if (verbose >= 3):
                            print(colorize("   STATUS      {0}".format(os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "32"))
                    elif (status == 'dirty'):
                        print(colorize("   STATUS {0: <4} {1}".format(shortStatus, os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "33"))
                        if (verbose >= 2) and (longStatus != ""):
                            for line in longStatus.splitlines():
                                print('   ' + line)
                    if args.show_overrides:
                        overridden, shortStatus, longStatus = stats[scmDir].statusOverrides(checkoutStep.getWorkspacePath())
                        if overridden:
                            print(colorize("   STATUS {0: <4} {1}".format(shortStatus, os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "32"))
                            if (verbose >= 2) and (longStatus != ""):
                                for line in longStatus.splitlines():
                                    print('   ' + line)

        if recurse:
            for d in package.getDirectDepSteps():
                showStatus(d.getPackage(), recurse, verbose, done)

    done = set()
    for p in args.packages:
        for package in packages.queryPackagePath(p):
            showStatus(package, args.recursive, args.verbose, done)

### Clean #############################

def collectPaths(package):
    paths = set()
    checkoutStep = package.getCheckoutStep()
    if checkoutStep.isValid(): paths.add(checkoutStep.getWorkspacePath())
    buildStep = package.getBuildStep()
    if buildStep.isValid(): paths.add(buildStep.getWorkspacePath())
    paths.add(package.getPackageStep().getWorkspacePath())
    for d in package.getDirectDepSteps():
        paths |= collectPaths(d.getPackage())
    return paths

def doClean(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob clean",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Clean unused directories.

This command removes currently unused directories from previous "bob build"
invocations.  By default only 'build' and 'package' steps are evicted. Adding
'-s' will clean 'checkout' steps too. Make sure that you have checked in (and
pushed) all your changes, tough. When in doubt add '--dry-run' to see what
would get removed without actually deleting that already.
""")
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-s', '--src', default=False, action='store_true',
        help="Clean source steps too")
    parser.add_argument('-v', '--verbose', default=False, action='store_true',
        help="Print what is done")
    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.parse()

    nameFormatter = LocalBuilder.makeRunnable(LocalBuilder.releaseNameInterrogator)

    # collect all used paths (with and without sandboxing)
    usedPaths = set()
    packages = recipes.generatePackages(nameFormatter, sandboxEnabled=True)
    usedPaths |= collectPaths(packages.getRootPackage())
    packages = recipes.generatePackages(nameFormatter, sandboxEnabled=False)
    usedPaths |= collectPaths(packages.getRootPackage())

    # get all known existing paths
    cleanSources = args.src
    allPaths = ( os.path.join(dir, "workspace")
        for (dir, isSourceDir) in BobState().getAllNameDirectores()
        if (not isSourceDir or (isSourceDir and cleanSources)) )
    allPaths = set(d for d in allPaths if os.path.exists(d))

    # delete unused directories
    for d in allPaths - usedPaths:
        if args.verbose or args.dry_run:
            print("rm", d)
        if not args.dry_run:
            removePath(d)

def doQueryPath(argv, bobRoot):
    # Local imports
    from string import Formatter

    # Configure the parser
    parser = argparse.ArgumentParser(prog="bob query-path",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""Query path information.

This command lists existing workspace directory names for packages given
on the command line. Output is formatted with a format string that can
contain placeholders
   {name}     package name
   {src}      checkout directory
   {build}    build directory
   {dist}     package directory
The default format is '{name}<tab>{dist}'.

If a directory does not exist for a step (because that step has never
been executed or does not exist), the line is omitted.
""")
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to query")
    parser.add_argument('-f', help='Output format string', default='{name}\t{dist}', metavar='FORMAT')
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox', help="Disable sandboxing")
    parser.set_defaults(sandbox=None)

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--develop', action='store_true',  dest='dev', help="Use developer mode", default=True)
    group.add_argument('--release', action='store_false', dest='dev', help="Use release mode")

    # Parse args
    args = parser.parse_args(argv)
    if args.sandbox == None:
        args.sandbox = not args.dev

    # Process defines
    defines = {}
    for define in args.defines:
        d = define.split("=")
        if len(d) == 1:
            defines[d[0]] = ""
        elif len(d) == 2:
            defines[d[0]] = d[1]
        else:
            parser.error("Malformed define: "+define)

    # Process the recipes
    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.defineHook('developNameFormatter', LocalBuilder.developNameFormatter)
    recipes.defineHook('developNamePersister', LocalBuilder.developNamePersister)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    # State variables in a class
    class State:
        def __init__(self):
            self.packageText = ''
            self.showPackage = True
        def appendText(self, what):
            self.packageText += what
        def appendStep(self, step):
            dir = step.getWorkspacePath()
            if step.isValid() and (dir is not None) and os.path.isdir(dir):
                self.packageText += dir
            else:
                self.showPackage = False
        def print(self):
            if (self.showPackage):
                print(self.packageText)

    if args.dev:
        # Develop names are stable. All we need to do is to replicate build's algorithm,
        # and when we produce a name, check whether it exists.
        nameFormatter = recipes.getHook('developNameFormatter')
        developPersister = recipes.getHook('developNamePersister')
        nameFormatter = developPersister(nameFormatter)
    else:
        # Release names are taken from persistence.
        nameFormatter = LocalBuilder.releaseNameInterrogator
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)

    # Find roots
    packages = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if args.dev:
        touch(packages)

    # Loop through packages
    for p in args.packages:
        # Format this package.
        # Only show the package if all of the requested directory names are present
        for package in packages.queryPackagePath(p):
            state = State()
            for (text, var, spec, conversion) in Formatter().parse(args.f):
                state.appendText(text)
                if var is None:
                    pass
                elif var == 'name':
                    state.appendText("/".join(package.getStack()))
                elif var == 'src':
                    state.appendStep(package.getCheckoutStep())
                elif var == 'build':
                    state.appendStep(package.getBuildStep())
                elif var == 'dist':
                    state.appendStep(package.getPackageStep())
                else:
                    raise ParseError("Unknown field '{" + var + "}'")

            # Show
            state.print()
