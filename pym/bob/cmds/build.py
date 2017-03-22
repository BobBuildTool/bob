# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from .. import BOB_VERSION
from ..archive import DummyArchive, getArchiver
from ..audit import Audit
from ..errors import BuildError, ParseError
from ..input import RecipeSet, walkPackagePath
from ..state import BobState
from ..tty import colorize
from ..utils import asHexStr, hashDirectory, hashFile, removePath, emptyDirectory, copyTree
from datetime import datetime
from glob import glob
from pipes import quote
import argparse
import datetime
import os
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

_keep_env=0
_verbose=1
_sandbox={SANDBOX_SETUP}
_keep_sandbox=0
_args=`getopt -o kqvE -- "$@"`
if [ $? != 0 ] ; then echo "Args parsing failed..." >&2 ; exit 1 ; fi
eval set -- "$_args"

_args=( )
while true ; do
    case "$1" in
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
        if [[ $_keep_env = 1 ]] ; then
            exec "$0" "${{_args[@]}}" __run
        else
            exec /usr/bin/env -i {WHITELIST} "$0" "${{_args[@]}}" __run
        fi
        ;;
    __run)
        cd "${{0%/*}}/workspace"
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
                 envWhiteList, bobRoot, cleanBuild):
        self.__recipes = recipes
        self.__wasRun= {}
        self.__wasSkipped = {}
        self.__verbose = max(-2, min(3, verbose))
        self.__force = force
        self.__skipDeps = skipDeps
        self.__buildOnly = buildOnly
        self.__preserveEnv = preserveEnv
        self.__envWhiteList = envWhiteList
        self.__currentPackage = None
        self.__archive = DummyArchive()
        self.__downloadDepth = 0xffff
        self.__bobRoot = bobRoot
        self.__cleanBuild = cleanBuild
        self.__cleanCheckout = False
        self.__buildIds = {}
        self.__statistic = LocalBuilderStatistic()

    def setArchiveHandler(self, archive):
        self.__archive = archive

    def setDownloadMode(self, mode):
        self.__downloadDepth = 0xffff
        if mode == 'yes':
            self.__archive.wantDownload(True)
            if self.__archive.canDownloadLocal():
                self.__downloadDepth = 0
        elif mode == 'deps':
            self.__archive.wantDownload(True)
            if self.__archive.canDownloadLocal():
                self.__downloadDepth = 1
        else:
            assert mode == 'no'
            self.__archive.wantDownload(False)

    def setUploadMode(self, mode):
        self.__archive.wantUpload(mode)

    def setCleanCheckout(self, clean):
        self.__cleanCheckout = clean

    def saveBuildState(self):
        # Save as plain dict. Skipped steps are dropped because they were not
        # really executed. Either they are simply skipped again or, if the
        # user changes his mind, they will finally be executed.
        state = { k:v for (k,v) in self.__wasRun.items()
                      if not self.__wasSkipped.get(k, False) }
        BobState().setBuildState(state)

    def loadBuildState(self):
        self.__wasRun = dict(BobState().getBuildState())

    def _wasAlreadyRun(self, step, skippedOk=False):
        path = step.getWorkspacePath()
        if path in self.__wasRun:
            digest = self.__wasRun[path]
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

    def _setAlreadyRun(self, step, skipped=False):
        path = step.getWorkspacePath()
        self.__wasRun[path] = step.getVariantId()
        self.__wasSkipped[path] = skipped

    def _constructDir(self, step, label):
        created = False
        workDir = step.getWorkspacePath()
        if not os.path.isdir(workDir):
            os.makedirs(workDir)
            created = True
        return (workDir, created)

    def _generateAudit(self, step, depth, resultHash):
        audit = Audit.create(step.getVariantId(), self._getBuildId(step, depth), resultHash)
        audit.addDefine("bob", BOB_VERSION)
        audit.addDefine("recipe", step.getPackage().getRecipe().getName())
        audit.addDefine("package", "/".join(step.getPackage().getStack()))
        audit.addDefine("step", step.getLabel())
        audit.setRecipesAudit(step.getPackage().getRecipe().getRecipeSet().getScmAudit())
        audit.setEnv(os.path.join(step.getWorkspacePath(), "..", "env"))
        if step.isCheckoutStep():
            for scm in step.getScmList():
                (typ, dirs) = scm.getAuditSpec()
                for dir in dirs:
                    audit.addScm(typ, step.getWorkspacePath(), dir)
        for (name, tool) in sorted(step.getTools().items()):
            audit.addTool(name,
                os.path.join(tool.getStep().getWorkspacePath(), "..", "audit.json.gz"))
        sandbox = step.getSandbox()
        if sandbox is not None:
            audit.setSandbox(os.path.join(sandbox.getStep().getWorkspacePath(), "..", "audit.json.gz"))
        for dep in step.getArguments():
            if dep.isValid():
                audit.addArg(os.path.join(dep.getWorkspacePath(), "..", "audit.json.gz"))

        auditPath = os.path.join(step.getWorkspacePath(), "..", "audit.json.gz")
        audit.save(auditPath)
        return auditPath

    def _runShell(self, step, scriptName):
        workspacePath = step.getWorkspacePath()
        if not os.path.isdir(workspacePath): os.makedirs(workspacePath)

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
                    SANDBOX_SETUP=sandboxSetup
                ), file=f)
        scriptFile = os.path.join(workspacePath, "..", "script")
        with open(scriptFile, "w") as f:
            print("set -o errtrace", file=f)
            print("set -o nounset", file=f)
            print("set -o pipefail", file=f)
            print("trap 'RET=$? ; echo \"\x1b[31;1mStep failed on line ${LINENO}: Exit status ${RET}; Command:\x1b[0;31m ${BASH_COMMAND}\x1b[0m\" >&2 ; exit $RET' ERR", file=f)
            print("trap 'for i in \"${_BOB_TMP_CLEANUP[@]-}\" ; do rm -f \"$i\" ; done' EXIT", file=f)
            print("", file=f)
            print("# Special args:", file=f)
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

    def cook(self, steps, parentPackage, checkoutOnly, depth=0):
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
            self.cook(checkoutStep.getAllDepSteps(), checkoutStep.getPackage(),
                      False, depth+1)

            # get directory into shape
            (prettySrcPath, created) = self._constructDir(checkoutStep, "src")
            oldCheckoutState = BobState().getDirectoryState(prettySrcPath, {})
            if created:
                # invalidate result if folder was created
                BobState().delResultHash(prettySrcPath)
                oldCheckoutState = {}
                BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

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
                        status = stats[scmDir].status(checkoutStep.getWorkspacePath(), scmDir)[0]
                        if (status == 'dirty') or (status == 'error'):
                            oldCheckoutState[scmDir] = None

                if (self.__force or (not checkoutStep.isDeterministic()) or
                    (BobState().getResultHash(prettySrcPath) is None) or
                    (checkoutState != oldCheckoutState)):
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

                    # Store new SCM checkout state. The script state is not stored
                    # so that this step will run again if it fails. OTOH we must
                    # record the SCM directories as some checkouts might already
                    # succeeded before the step ultimately fails.
                    BobState().setDirectoryState(prettySrcPath,
                        { d:s for (d,s) in checkoutState.items() if d is not None })

                    # check that new checkouts do not collide with old stuff in workspace
                    for scmDir in checkoutState.keys():
                        if scmDir is None or scmDir == ".": continue
                        if oldCheckoutState.get(scmDir) is not None: continue
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        if os.path.exists(scmPath):
                            raise BuildError("New SCM checkout '{}' collides with existing file in workspace '{}'!"
                                                .format(scmDir, prettySrcPath))

                    # Forge checkout result before we run the step again.
                    # Normally the correct result is set directly after the
                    # checkout finished. But if the step fails and the user
                    # re-runs with "build-only" the dependent steps should
                    # trigger.
                    if BobState().getResultHash(prettySrcPath) is not None:
                        BobState().setResultHash(prettySrcPath, datetime.datetime.utcnow())

                    print(colorize("   CHECKOUT  {} {}".format(prettySrcPath, overridesString)
                        , "32"))
                    self._runShell(checkoutStep, "checkout")
                    self.__statistic.checkouts += 1
                    # reflect new checkout state
                    BobState().setDirectoryState(prettySrcPath, checkoutState)
                else:
                    self._info("   CHECKOUT  skipped (fixed package {})".format(prettySrcPath))

            # We always have to rehash the directory as the user might have
            # changed the source code manually.
            oldCheckoutHash = BobState().getResultHash(prettySrcPath)
            checkoutHash = hashWorkspace(checkoutStep)
            BobState().setResultHash(prettySrcPath, checkoutHash)

            self._setAlreadyRun(checkoutStep)

            # Generate audit trail. Has to be done _after_ setResultHash()
            # because the result is needed to calculate the buildId.
            if checkoutHash != oldCheckoutHash:
                self._generateAudit(checkoutStep, depth, checkoutHash)

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
            self.cook(buildStep.getAllDepSteps(), buildStep.getPackage(),
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
                for i in buildStep.getArguments() if i.isValid() ]
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
                if self.__cleanBuild: emptyDirectory(prettyBuildPath)
                # Squash state because running the step will change the
                # content. If the execution fails we have nothing reliable
                # left and we _must_ run it again.
                BobState().delInputHashes(prettyBuildPath)
                BobState().setResultHash(prettyBuildPath, datetime.datetime.utcnow())
                # build it
                self._runShell(buildStep, "build")
                buildHash = hashWorkspace(buildStep)
                self._generateAudit(buildStep, depth, buildHash)
                BobState().setResultHash(prettyBuildPath, buildHash)
                BobState().setInputHashes(prettyBuildPath, buildInputHashes)
            self._setAlreadyRun(buildStep, checkoutOnly)

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

            # Can we theoretically download the result? Exclude packages that
            # provide host tools when not building in a sandbox. Try to
            # determine a build-id for all other artifacts.
            if packageStep.doesProvideTools() and (packageStep.getSandbox() is None):
                packageBuildId = None
            else:
                packageBuildId = self._getBuildId(packageStep, depth)

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
            if ( (not checkoutOnly) and packageBuildId and self.__archive.canDownloadLocal()
                 and (depth >= self.__downloadDepth) ):
                # prune directory if we previously downloaded/built something different
                if (oldInputBuildId is not None) and (oldInputBuildId != packageBuildId):
                    print(colorize("   PRUNE     {} (build-id changed)".format(prettyPackagePath), "33"))
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
                elif oldWasDownloaded:
                    self._info("   PACKAGE   skipped (deterministic output in {})".format(prettyPackagePath))
                    wasDownloaded = True

            # Run package step if we have not yet downloaded the package or if
            # downloads are not possible anymore. Even if the package was
            # previously downloaded the oldInputHashes will be None to trigger
            # an actual build.
            if not wasDownloaded:
                # depth first
                self.cook(packageStep.getAllDepSteps(), packageStep.getPackage(),
                          checkoutOnly, depth+1)

                packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                    for i in packageStep.getArguments() if i.isValid() ]
                if checkoutOnly:
                    self._info("   PACKAGE   skipped due to --checkout-only ({})".format(prettyPackagePath))
                elif (not self.__force) and (oldInputHashes == packageInputHashes):
                    self._info("   PACKAGE   skipped (unchanged input for {})".format(prettyPackagePath))
                else:
                    print(colorize("   PACKAGE   {}".format(prettyPackagePath), "32"))
                    emptyDirectory(prettyPackagePath)
                    # invalidate result because folder was cleared
                    BobState().delInputHashes(prettyPackagePath)
                    BobState().setResultHash(prettyPackagePath, datetime.datetime.utcnow())
                    self._runShell(packageStep, "package")
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
            self._setAlreadyRun(packageStep, checkoutOnly)

    def _getBuildId(self, step, depth):
        """Calculate build-id and cache result.

        The cache uses the workspace path as index because there might be
        multiple directories with the same variant-id.
        """
        path = step.getWorkspacePath()
        ret = self.__buildIds.get(path)
        if ret is None:
            if step.isCheckoutStep():
                # do checkout
                self.cook([step], step.getPackage(), depth)
                # return directory hash
                ret = BobState().getResultHash(step.getWorkspacePath())
            else:
                ret = step.getDigest(lambda s: self._getBuildId(s, depth+1), True)
            self.__buildIds[path] = ret

        return ret


def touch(rootPackages):
    done = set()
    def touchStep(step):
        if step in done: return
        done.add(step)
        for d in step.getAllDepSteps():
            if d.isValid(): touchStep(d)
        step.getWorkspacePath()

    for p in sorted(rootPackages.values(), key=lambda p: p.getName()):
        touchStep(p.getPackageStep())


def commonBuildDevelop(parser, argv, bobRoot, develop):
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to build")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of build result (will be overwritten!)")
    parser.add_argument('-f', '--force', default=False, action='store_true',
        help="Force execution of all build steps")
    parser.add_argument('-n', '--no-deps', default=False, action='store_true',
        help="Don't build dependencies")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-b', '--build-only', default=False, action='store_true',
        help="Don't checkout, just build and package")
    group.add_argument('-B', '--checkout-only', default=False, action='store_true',
        help="Don't build, just check out sources")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--clean', action='store_true', default=not develop,
        help="Do clean builds (clear build directory)")
    group.add_argument('--incremental', action='store_false', dest='clean',
        help="Reuse build directory for incremental builds")
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-e', dest="white_list", default=[], action='append', metavar="NAME",
        help="Preserve environment variable")
    parser.add_argument('-E', dest="preserve_env", default=False, action='store_true',
        help="Preserve whole environment")
    parser.add_argument('--upload', default=False, action='store_true',
        help="Upload to binary archive")
    parser.add_argument('--download', metavar="MODE", default="deps" if develop else "yes",
        help="Download from binary archive (yes, no, deps)", choices=['yes', 'no', 'deps'])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=not develop,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    parser.add_argument('--clean-checkout', action='store_true', default=False, dest='clean_checkout',
        help="Do a clean checkout if SCM state is dirty.")
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
    rootPackages = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if develop:
        touch(rootPackages)

    builder = LocalBuilder(recipes, args.verbose - args.quiet, args.force,
                           args.no_deps, args.build_only, args.preserve_env,
                           envWhiteList, bobRoot, args.clean)

    builder.setArchiveHandler(getArchiver(recipes))
    builder.setUploadMode(args.upload)
    builder.setDownloadMode(args.download)
    builder.setCleanCheckout(args.clean_checkout)
    if args.resume: builder.loadBuildState()

    backlog = []
    results = []
    for p in args.packages:
        packageStep = walkPackagePath(rootPackages, p).getPackageStep()
        backlog.append(packageStep)
        # automatically include provided deps when exporting
        if args.destination: backlog.extend(packageStep._getProvidedDeps())
    try:
        for p in backlog:
            builder.cook([p], p.getPackage(), args.checkout_only)
            resultPath = p.getWorkspacePath()
            if resultPath not in results:
                results.append(resultPath)
    finally:
        builder.saveBuildState()

    endTime = time.time()
    # tell the user
    if len(results) == 1:
        print("Build result is in", results[0])
    elif len(results) > 1:
        print("Build results are in:\n  ", "\n   ".join(results))

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
    rootPackages = recipes.generatePackages(nameFormatter, defines, sandboxEnabled=args.sandbox)
    touch(rootPackages)

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
            print("bob project: error: the following arguments are required: projectGenerator, package, args")
            return 1

    try:
        generator = generators[args.projectGenerator]
    except KeyError:
        print("Generator {} not found!".format(args.projectGenerator))
        return 1

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

    package = walkPackagePath(rootPackages, args.package)

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

    roots = recipes.generatePackages(nameFormatter, defines, not args.develop)
    if args.develop:
       touch(roots)

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
                    status, shortStatus, longStatus = stats[scmDir].status(checkoutStep.getWorkspacePath(), scmDir)
                    if (status == 'clean') or (status == 'empty'):
                        if (verbose >= 3):
                            print(colorize("   STATUS      {0}".format(os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "32"))
                    elif (status == 'dirty'):
                        print(colorize("   STATUS {0: <4} {1}".format(shortStatus, os.path.join(checkoutStep.getWorkspacePath(), scmDir)), "33"))
                        if (verbose >= 2) and (longStatus != ""):
                            for line in longStatus.splitlines():
                                print('   ' + line)
                    if args.show_overrides:
                        overridden, shortStatus, longStatus = stats[scmDir].statusOverrides(checkoutStep.getWorkspacePath(), scmDir)
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
        package = walkPackagePath(roots, p)
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
    rootPackages = recipes.generatePackages(nameFormatter,
                                            sandboxEnabled=True).values()
    for root in rootPackages:
        usedPaths |= collectPaths(root)
    rootPackages = recipes.generatePackages(nameFormatter,
                                            sandboxEnabled=False).values()
    for root in rootPackages:
        usedPaths |= collectPaths(root)

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
    roots = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if args.dev:
        touch(roots)

    # Loop through packages
    for p in args.packages:
        # Format this package.
        # Only show the package if all of the requested directory names are present
        package = walkPackagePath(roots, p)
        state = State()
        for (text, var, spec, conversion) in Formatter().parse(args.f):
            state.appendText(text)
            if var is None:
                pass
            elif var == 'name':
                state.appendText(p)
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
