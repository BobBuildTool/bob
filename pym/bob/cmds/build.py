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

from ..errors import BuildError
from ..input import RecipeSet, walkPackagePath
from ..state import BobState
from ..tty import colorize
from ..utils import asHexStr, hashDirectory, hashFile, removePath, emptyDirectory
from datetime import datetime
from glob import glob
from pipes import quote
from tempfile import TemporaryFile
import argparse
import datetime
import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request, urllib.error

# Output verbosity:
#    <= -2: package name
#    == -1: package name, package steps
#    ==  0: package name, package steps, stderr
#    ==  1: package name, package steps, stderr, stdout
#    ==  2: package name, package steps, stderr, stdout, set -x

class Bijection(dict):
    """Bijective dict that silently removes offending mappings"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__rev = {}
        for (key, val) in self.copy().items():
            if val in self.__rev: del self[self.__rev[val]]
            self.__rev[val] = key

    def __setitem__(self, key, val):
        if val in self.__rev: del self[self.__rev[val]]
        self.__rev[val] = key
        super().__setitem__(key, val)

    def __delitem__(self, key):
        del self.__rev[self[key]]
        super().__delitem__(key)

def hashWorkspace(step):
    return hashDirectory(step.getWorkspacePath(),
        os.path.join(step.getWorkspacePath(), "..", "cache.bin"))

class DummyArchive:
    def uploadPackage(self, buildId, path):
        pass

    def downloadPackage(self, buildId, path):
        return False

class LocalArchive:
    def __init__(self, spec):
        self.__basePath = os.path.abspath(spec["path"])

    def uploadPackage(self, buildId, path):
        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            print("   UPLOAD    skipped ({} exists in archive)".format(path))
            return

        print(colorize("   UPLOAD    {}".format(path), "32"))
        if not os.path.isdir(packageResultPath): os.makedirs(packageResultPath)
        with tarfile.open(packageResultFile, "w:gz") as tar:
            tar.add(path, arcname=".")

    def downloadPackage(self, buildId, path):
        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        packageResultId = asHexStr(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        if os.path.isfile(packageResultFile):
            removePath(path)
            os.makedirs(path)
            with tarfile.open(packageResultFile, "r:gz") as tar:
                tar.extractall(path)
            print(colorize("ok", "32"))
            return True
        else:
            print(colorize("not found", "33"))
            return False


class SimpleHttpArchive:
    def __init__(self, spec):
        self.__url = spec["url"]

    def _makeUrl(self, buildId):
        packageResultId = asHexStr(buildId)
        return "/".join([self.__url, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + ".tgz"])

    def uploadPackage(self, buildId, path):
        url = self._makeUrl(buildId)

        # check if already there
        try:
            try:
                req = urllib.request.Request(url=url, method='HEAD')
                f = urllib.request.urlopen(req)
                print("   UPLOAD    skipped ({} exists in archive)".format(path))
                return
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise BuildError("Error for HEAD on "+url+": "+e.reason)

            print(colorize("   UPLOAD    {}".format(path), "32"))
            with TemporaryFile() as tmpFile:
                with tarfile.open(fileobj=tmpFile, mode="w:gz") as tar:
                    tar.add(path, arcname=".")
                tmpFile.seek(0)
                req = urllib.request.Request(url=url, data=tmpFile.read(),
                                             method='PUT')
                f = urllib.request.urlopen(req)
        except urllib.error.URLError as e:
            raise BuildError("Error uploading package: "+str(e.reason))

    def downloadPackage(self, buildId, path):
        ret = False
        print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
        url = self._makeUrl(buildId)
        try:
            (localFilename, headers) = urllib.request.urlretrieve(url)
            removePath(path)
            os.makedirs(path)
            with tarfile.open(localFilename, "r:gz", errorlevel=1) as tar:
                tar.extractall(path)
            ret = True
            print(colorize("ok", "32"))
        except urllib.error.URLError as e:
            print(colorize(str(e.reason), "33"))
        except OSError as e:
            raise BuildError("Error: " + str(e))
        finally:
            urllib.request.urlcleanup()

        return ret

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
        return os.path.join("work", step.getPackage().getName().replace('::', os.sep),
                            step.getLabel())

    @staticmethod
    def releaseNamePersister(wrapFmt, persistent=True):

        def fmt(step, props):
            return BobState().getByNameDirectory(
                wrapFmt(step, props),
                asHexStr(step.getVariantId()),
                persistent)

        return fmt

    @staticmethod
    def developNameFormatter(step, props):
        return os.path.join("dev", step.getLabel(),
                            step.getPackage().getName().replace('::', os.sep))

    @staticmethod
    def developNamePersister(wrapFmt):
        dirs = {}

        def fmt(step, props):
            baseDir = wrapFmt(step, props)
            digest = step.getVariantId()
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
            return os.path.join(ret, "workspace")

        return fmt

    def __init__(self, recipes, verbose, force, skipDeps, buildOnly, preserveEnv,
                 envWhiteList, bobRoot, cleanBuild):
        self.__recipes = recipes
        self.__wasRun= Bijection()
        self.__verbose = max(-2, min(2, verbose))
        self.__force = force
        self.__skipDeps = skipDeps
        self.__buildOnly = buildOnly
        self.__preserveEnv = preserveEnv
        self.__envWhiteList = envWhiteList
        self.__currentPackage = None
        self.__archive = DummyArchive()
        self.__doDownload = False
        self.__doUpload = False
        self.__downloadDepth = 0xffff
        self.__bobRoot = bobRoot
        self.__cleanBuild = cleanBuild

    def setArchiveHandler(self, archive):
        self.__doDownload = True
        self.__archive = archive

    def setDownloadMode(self, mode):
        if mode == 'yes':
            self.__downloadDepth = 0
        elif mode == 'deps':
            self.__downloadDepth = 1
        else:
            assert mode == 'no'
            self.__downloadDepth = 0xffff

    def setUploadMode(self, mode):
        self.__doUpload = mode

    def saveBuildState(self):
        # save as plain dict
        BobState().setBuildState(dict(self.__wasRun))

    def loadBuildState(self):
        self.__wasRun = Bijection(BobState().getBuildState())

    def _wasAlreadyRun(self, step):
        digest = step.getVariantId()
        if digest in self.__wasRun:
            path = self.__wasRun[digest]
            # invalidate invalid cached entries
            if path != step.getWorkspacePath():
                del self.__wasRun[digest]
                return False
            else:
                return True
        else:
            return False

    def _getAlreadyRun(self, step):
        return self.__wasRun[step.getVariantId()]

    def _setAlreadyRun(self, step):
        self.__wasRun[step.getVariantId()] = step.getWorkspacePath()

    def _constructDir(self, step, label):
        created = False
        workDir = step.getWorkspacePath()
        if not os.path.isdir(workDir):
            os.makedirs(workDir)
            created = True
        return (workDir, created)

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
        stepEnv["BOB_STEP_NAME"] = "/".join(self.__currentPackage.getStack())

        # filter runtime environment
        if self.__preserveEnv:
            runEnv = os.environ.copy()
        else:
            runEnv = { k:v for (k,v) in os.environ.items()
                                     if k in self.__envWhiteList }
        runEnv.update(stepEnv)

        # sandbox
        sandbox = []
        sandboxSetup = ""
        if step.getSandbox() is not None:
            sandboxSetup = "\"$(mktemp -d)\""
            sandbox.append(quote(os.path.join(self.__bobRoot,
                                              "bin", "namespace-sandbox")))
            sandbox.extend(["-S", "\"$_sandbox\""])
            sandbox.extend(["-W", quote(step.getExecPath())])
            sandbox.extend(["-H", "bob"])
            sandbox.extend(["-d", "/tmp"])
            sandboxRootFs = os.path.abspath(
                step.getSandbox().getStep().getWorkspacePath())
            for f in os.listdir(sandboxRootFs):
                sandbox.extend(["-M", os.path.join(sandboxRootFs, f), "-m", "/"+f])
            for (hostPath, sndbxPath) in step.getSandbox().getMounts():
                sandbox.extend(["-M", hostPath ])
                if hostPath != sndbxPath: sandbox.extend(["-m", sndbxPath])
            sandbox.extend([
                "-M", quote(os.path.abspath(os.path.join(
                    step.getWorkspacePath(), ".."))),
                "-w", quote(os.path.normpath(os.path.join(
                    step.getExecPath(), ".."))) ])
            addDep = lambda s: (sandbox.extend([
                    "-M", quote(os.path.abspath(s.getWorkspacePath())),
                    "-m", quote(s.getExecPath()) ]) if s.isValid() else None)
            for s in step.getAllDepSteps():
                if s != step.getSandbox().getStep(): addDep(s)
            # special handling to mount all previous steps of current package
            s = step
            while s.isValid():
                if len(s.getArguments()) > 0:
                    s = s.getArguments()[0]
                    addDep(s)
                else:
                    break
            sandbox.append("--")

        # write scripts
        runFile = os.path.join("..", scriptName+".sh")
        absRunFile = os.path.normpath(os.path.join(workspacePath, runFile))
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
                    SANDBOX_CMD=" ".join(sandbox),
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
                    for a in step.getArguments() ] ))), file=f)
            print("declare -A BOB_TOOL_PATHS=( {} )".format(" ".join(sorted(
                [ "[{}]={}".format(quote(t), quote(p))
                    for (t,p) in step.getTools().items()] ))), file=f)
            print("# Environment:", file=f)
            for (k,v) in sorted(stepEnv.items()):
                print("export {}={}".format(k, quote(v)), file=f)
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

        proc = subprocess.Popen(cmdLine, cwd=step.getWorkspacePath(), env=runEnv)
        try:
            if proc.wait() != 0:
                raise BuildError("Build script {} returned with {}"
                                    .format(absRunFile, proc.returncode),
                                 help="You may resume at this point with '--resume' after fixing the error.")
        except KeyboardInterrupt:
            raise BuildError("User aborted while running {}".format(absRunFile),
                             help = "Run again with '--resume' to skip already built packages.")

    def _info(self, *args, **kwargs):
        if self.__verbose >= -1:
            print(*args, **kwargs)

    def cook(self, steps, parentPackage, done=set(), depth=0):
        currentPackage = self.__currentPackage
        ret = None

        # skip everything except the current package
        if self.__skipDeps:
            steps = [ s for s in steps if s.getPackage() == parentPackage ]

        for step in reversed(steps):
            # skip if already processed steps
            if step in done:
                continue

            # update if package changes
            if step.getPackage() != self.__currentPackage:
                self.__currentPackage = step.getPackage()
                print(">>", colorize("/".join(self.__currentPackage.getStack()), "32;1"))

            # execute step
            ret = None
            try:
                if step.isCheckoutStep():
                    if step.isValid():
                        self._cookCheckoutStep(step, done, depth)
                elif step.isBuildStep():
                    if step.isValid():
                        self._cookBuildStep(step, done, depth)
                else:
                    assert step.isPackageStep() and step.isValid()
                    ret = self._cookPackageStep(step, done, depth)
            except BuildError as e:
                e.pushFrame(step.getPackage().getName())
                raise e

            # mark as done
            done.add(step)

        # back to original package
        if currentPackage != self.__currentPackage:
            self.__currentPackage = currentPackage
            if currentPackage:
                print(">>", colorize("/".join(self.__currentPackage.getStack()), "32;1"))
        return ret

    def _cookCheckoutStep(self, checkoutStep, done, depth):
        checkoutDigest = checkoutStep.getVariantId()
        if self._wasAlreadyRun(checkoutStep):
            prettySrcPath = self._getAlreadyRun(checkoutStep)
            self._info("   CHECKOUT  skipped (reuse {})".format(prettySrcPath))
        else:
            # depth first
            self.cook(checkoutStep.getAllDepSteps(), checkoutStep.getPackage(),
                      done, depth+1)

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
                self._info("   CHECKOUT  skipped due to --build-only ({})".format(prettySrcPath))
            elif (self.__force or (not checkoutStep.isDeterministic()) or
                    (BobState().getResultHash(prettySrcPath) is None) or
                    (checkoutState != oldCheckoutState)):
                # move away old or changed source directories
                for (scmDir, scmDigest) in oldCheckoutState.copy().items():
                    if (scmDir is not None) and (scmDigest != checkoutState.get(scmDir)):
                        scmPath = os.path.normpath(os.path.join(prettySrcPath, scmDir))
                        atticName = datetime.datetime.now().isoformat()+"_"+os.path.basename(scmPath)
                        print(colorize("   ATTIC     {} (move to ../attic/{})".format(scmPath, atticName), "33"))
                        atticPath = os.path.join(prettySrcPath, "..", "attic")
                        if not os.path.isdir(atticPath):
                            os.makedirs(atticPath)
                        os.rename(scmPath, os.path.join(atticPath, atticName))
                        del oldCheckoutState[scmDir]
                        BobState().setDirectoryState(prettySrcPath, oldCheckoutState)

                print(colorize("   CHECKOUT  {}".format(prettySrcPath), "32"))
                self._runShell(checkoutStep, "checkout")

                # reflect new checkout state
                BobState().setDirectoryState(prettySrcPath, checkoutState)
            else:
                self._info("   CHECKOUT  skipped (fixed package {})".format(prettySrcPath))

            # We always have to rehash the directory as the user might have
            # changed the source code manually.
            BobState().setResultHash(prettySrcPath, hashWorkspace(checkoutStep))
            self._setAlreadyRun(checkoutStep)

    def _cookBuildStep(self, buildStep, done, depth):
        buildDigest = buildStep.getVariantId()
        if self._wasAlreadyRun(buildStep):
            prettyBuildPath = self._getAlreadyRun(buildStep)
            self._info("   BUILD     skipped (reuse {})".format(prettyBuildPath))
        else:
            # depth first
            self.cook(buildStep.getAllDepSteps(), buildStep.getPackage(), done, depth+1)

            # get directory into shape
            (prettyBuildPath, created) = self._constructDir(buildStep, "build")
            oldBuildDigest = BobState().getDirectoryState(prettyBuildPath)
            if created or (buildDigest != oldBuildDigest):
                if (oldBuildDigest is not None) and (buildDigest != oldBuildDigest):
                    # build something different -> prune workspace
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
            if (not self.__force) and (BobState().getInputHashes(prettyBuildPath) == buildInputHashes):
                self._info("   BUILD     skipped (unchanged input for {})".format(prettyBuildPath))
                # We always rehash the directory in development mode as the
                # user might have compiled the package manually.
                if not self.__cleanBuild:
                    BobState().setResultHash(prettyBuildPath, hashWorkspace(buildStep))
            else:
                print(colorize("   BUILD     {}".format(prettyBuildPath), "32"))
                if self.__cleanBuild: emptyDirectory(prettyBuildPath)
                self._runShell(buildStep, "build")
                # Use timestamp in release mode and only hash in development mode
                BobState().setResultHash(prettyBuildPath,
                                         datetime.datetime.utcnow()
                                             if self.__cleanBuild
                                             else hashWorkspace(buildStep))
                BobState().setInputHashes(prettyBuildPath, buildInputHashes)
            self._setAlreadyRun(buildStep)

    def _cookPackageStep(self, packageStep, done, depth):
        packageDigest = packageStep.getVariantId()
        if self._wasAlreadyRun(packageStep):
            prettyPackagePath = self._getAlreadyRun(packageStep)
            self._info("   PACKAGE   skipped (reuse {})".format(prettyPackagePath))
        else:
            # get directory into shape
            (prettyPackagePath, created) = self._constructDir(packageStep, "dist")
            oldPackageDigest = BobState().getDirectoryState(prettyPackagePath)
            if created or (packageDigest != oldPackageDigest):
                if (oldPackageDigest is not None) and (packageDigest != oldPackageDigest):
                    # package something different -> prune workspace
                    print(colorize("   PRUNE     {} (recipe changed)".format(prettyPackagePath), "33"))
                    emptyDirectory(prettyPackagePath)
                # invalidate result if folder was created
                BobState().delInputHashes(prettyPackagePath)
                BobState().delResultHash(prettyPackagePath)

            if packageDigest != oldPackageDigest:
                BobState().setDirectoryState(prettyPackagePath, packageDigest)

            # Can we just download the result? If we download a package the
            # Build-Id is stored as input hash. In this case we have to make
            # sure that the Build-Id is still the same. If the input hash is
            # not a bytes object we have apparently not downloaded the result.
            # Dont' mess with it and fall back to regular build machinery.
            packageDone = False
            packageExecuted = False
            if packageStep.doesProvideTools() and (packageStep.getSandbox() is None):
                # Exclude packages that provide host tools when not building in a sandbox
                packageBuildId = None
            else:
                packageBuildId = self._getBuildId(packageStep, done, depth) \
                    if (self.__doDownload or self.__doUpload) else None
            if packageBuildId and (depth >= self.__downloadDepth):
                oldInputHashes = BobState().getInputHashes(prettyPackagePath)
                # prune directory if we previously downloaded something different
                if isinstance(oldInputHashes, bytes) and (oldInputHashes != packageBuildId):
                    print(colorize("   PRUNE     {} (build-id changed)".format(prettyPackagePath), "33"))
                    emptyDirectory(prettyPackagePath)
                    BobState().delInputHashes(prettyPackagePath)
                    BobState().delResultHash(prettyPackagePath)

                # Try to download the package if the directory is currently
                # empty. If the directory holds a result and was downloaded it
                # we're done.
                if BobState().getResultHash(prettyPackagePath) is None:
                    if self.__archive.downloadPackage(packageBuildId, prettyPackagePath):
                        BobState().setInputHashes(prettyPackagePath, packageBuildId)
                        packageDone = True
                        packageExecuted = True
                elif isinstance(oldInputHashes, bytes):
                    self._info("   PACKAGE   skipped (deterministic output in {})".format(prettyPackagePath))
                    packageDone = True

            # package it if needed
            if not packageDone:
                # depth first
                self.cook(packageStep.getAllDepSteps(), packageStep.getPackage(), done, depth+1)

                packageInputHashes = [ BobState().getResultHash(i.getWorkspacePath())
                    for i in packageStep.getArguments() if i.isValid() ]
                if (not self.__force) and (BobState().getInputHashes(prettyPackagePath) == packageInputHashes):
                    self._info("   PACKAGE   skipped (unchanged input for {})".format(prettyPackagePath))
                else:
                    print(colorize("   PACKAGE   {}".format(prettyPackagePath), "32"))
                    emptyDirectory(prettyPackagePath)
                    self._runShell(packageStep, "package")
                    packageExecuted = True
                    if packageBuildId and self.__doUpload:
                        self.__archive.uploadPackage(packageBuildId, prettyPackagePath)
            else:
                # do not change input hashes
                packageInputHashes = BobState().getInputHashes(prettyPackagePath)

            # Rehash directory if content was changed
            if packageExecuted:
                BobState().setResultHash(prettyPackagePath, hashWorkspace(packageStep))
                BobState().setInputHashes(prettyPackagePath, packageInputHashes)
            self._setAlreadyRun(packageStep)

        return prettyPackagePath

    def _getBuildId(self, step, done, depth):
        if step.isCheckoutStep():
            bid = step.getBuildId()
            if bid is None:
                # do checkout
                self.cook([step], step.getPackage(), done, depth)
                # return directory hash
                bid = BobState().getResultHash(step.getWorkspacePath())
            return bid
        else:
            return step.getDigest(lambda s: self._getBuildId(s, done, depth+1), True)


def touch(packages):
    for p in packages:
        touch([s.getPackage() for s in p.getAllDepSteps()])
        p.getCheckoutStep().getWorkspacePath()
        p.getBuildStep().getWorkspacePath()
        p.getPackageStep().getWorkspacePath()


def commonBuildDevelop(parser, argv, bobRoot, develop):
    parser.add_argument('packages', metavar='PACKAGE', type=str, nargs='+',
        help="(Sub-)package to build")
    parser.add_argument('--destination', metavar="DEST",
        help="Destination of build result (will be cleaned!)")
    parser.add_argument('-f', '--force', default=False, action='store_true',
        help="Force execution of all build steps")
    parser.add_argument('-n', '--no-deps', default=False, action='store_true',
        help="Don't build dependencies")
    parser.add_argument('-b', '--build-only', default=False, action='store_true',
        help="Don't checkout, just build and package")
    parser.add_argument('--resume', default=False, action='store_true',
        help="Resume build where it was previously interrupted")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
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
    recipes.parse()

    envWhiteList = recipes.envWhiteList()
    envWhiteList |= set(args.white_list)

    cleanBuild = not develop

    if develop:
        nameFormatter = recipes.getHook('developNameFormatter')
        nameFormatter = LocalBuilder.developNamePersister(nameFormatter)
    else:
        nameFormatter = recipes.getHook('releaseNameFormatter')
        nameFormatter = LocalBuilder.releaseNamePersister(nameFormatter)
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)
    rootPackages = recipes.generatePackages(nameFormatter, defines, args.sandbox)
    if develop:
        touch(sorted(rootPackages.values(), key=lambda p: p.getName()))

    if (len(args.packages) > 1) and args.destination:
        raise BuildError("Destination may only be specified when building a single package")

    builder = LocalBuilder(recipes, args.verbose - args.quiet, args.force,
                           args.no_deps, args.build_only, args.preserve_env,
                           envWhiteList, bobRoot, cleanBuild)

    archiveSpec = recipes.archiveSpec()
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        builder.setArchiveHandler(LocalArchive(archiveSpec))
    elif archiveBackend == "http":
        builder.setArchiveHandler(SimpleHttpArchive(archiveSpec))
    elif archiveBackend != "none":
        raise BuildError("Invalid archive backend: "+archiveBackend)
    builder.setUploadMode(args.upload)
    builder.setDownloadMode(args.download)
    if args.resume: builder.loadBuildState()

    try:
        for p in args.packages:
            package = walkPackagePath(rootPackages, p)
            prettyResultPath = builder.cook([package.getPackageStep()], package)
            print("Build result is in", prettyResultPath)
    finally:
        builder.saveBuildState()

    # copy build result if requested
    if args.destination:
        if os.path.exists(args.destination):
            removePath(args.destination)
        shutil.copytree(prettyResultPath, args.destination, symlinks=True)

def doBuild(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob build", description='Build packages in release mode.')
    commonBuildDevelop(parser, argv, bobRoot, False)

def doDevelop(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob dev", description='Build packages in development mode.')
    commonBuildDevelop(parser, argv, bobRoot, True)

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
    parser = argparse.ArgumentParser(prog="bob clean", description='Clean unused directories.')
    parser.add_argument('--dry-run', default=False, action='store_true',
        help="Don't delete, just print what would be deleted")
    parser.add_argument('-v', '--verbose', default=False, action='store_true',
        help="Print what is done")
    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.defineHook('releaseNameFormatter', LocalBuilder.releaseNameFormatter)
    recipes.parse()

    nameFormatter = recipes.getHook('releaseNameFormatter')
    nameFormatter = LocalBuilder.releaseNamePersister(nameFormatter, False)
    nameFormatter = LocalBuilder.makeRunnable(nameFormatter)

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
    allPaths = BobState().getAllNameDirectores()
    allPaths = set([ d for d in allPaths if os.path.exists(d) ])

    # delete unused directories
    for d in allPaths - usedPaths:
        if args.verbose or args.dry_run:
            print("rm", d)
        if not args.dry_run:
            removePath(d)

