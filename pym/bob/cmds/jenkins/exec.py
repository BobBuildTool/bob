# Bob build tool
# Copyright (C) 2022  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ... import BOB_INPUT_HASH
from ...archive import getArchiver, JenkinsArchive
from ...builder import LocalBuilder
from ...errors import BuildError
from ...share import getShare
from ...stringparser import Env, isTrue
from ...tty import setVerbosity, TRACE
from ...utils import asHexStr, EventLoopWrapper, removePath, isWindows, \
        getPlatformString
from .intermediate import PartialIR
import argparse
import base64
import json
import lzma
import os.path

class Spec:
    def __init__(self, specFile):
        self.download = False
        self.upload = False
        self.artifactsCopy = "jenkins"
        self.shareDir = None
        self.shareQuota = None
        self.auditMeta = {}
        self.platform = None
        self.__recipesAudit = []
        self.__execIR = []

        with open(specFile, "r") as f:
            f.readline() # skip shebang
            for l in f:
                l = l.rstrip('\n')
                if l.startswith('[') and l.endswith(']'):
                    if l == "[cfg]":
                        handler = self.__handleCfg
                    elif l == "[recipes-audit]":
                        handler = self.__handleRecipesAudit
                    elif l == "[audit]":
                        handler = self.__handleAuditMeta
                    elif l == "[exec]":
                        handler = self.__handleExecIR
                    else:
                        handler(l)
                else:
                    handler(l)

    def __handleCfg(self, line):
        k, _, v = line.partition("=")
        if k == "download":
            self.download = isTrue(v)
        elif k == "upload":
            self.upload = isTrue(v)
        elif k == "copy":
            self.artifactsCopy = v
        elif k == "share":
            self.shareDir = v
        elif k == "quota":
            self.shareQuota = v
        elif k == "platform":
            self.platform = v
        else:
            raise AssertionError(line)

    def __handleRecipesAudit(self, line):
        self.__recipesAudit.append(line)

    def __handleAuditMeta(self, line):
        k, _, v = line.partition("=")
        self.auditMeta[k] = v

    def __handleExecIR(self, line):
        self.__execIR.append(line)

    @property
    def recipesAudit(self):
        if self.__recipesAudit:
            return json.loads("".join(self.__recipesAudit))
        else:
            return None

    @property
    def execIR(self):
        ir = base64.a85decode("".join(self.__execIR))
        ir = lzma.decompress(ir)
        ir = PartialIR.fromData(json.loads(ir))
        ir.scmAudit = self.recipesAudit
        return ir


def readBuildId(step):
    try:
        with open(JenkinsArchive.buildIdName(step), 'rb') as f:
            return f.read()
    except OSError as e:
        raise BuildError("Could not read build-id: " + str(e),
                         help="This may happend if the job was modified while being queued.")

def cleanRecurse(d, whiteList, keep = lambda n: False):
    with os.scandir(d) as it:
        for entry in it:
            if entry.name in whiteList:
                subEntry = whiteList[entry.name]
                if entry.is_dir(follow_symlinks=False) and isinstance(subEntry, dict):
                    cleanRecurse(entry.path, subEntry)
            elif keep(entry.name):
                pass
            else:
                print("Remove", entry.path)
                removePath(entry.path)

def cleanWorkspace(spec):
    # gather all files/directories that should be kept
    whiteList = { }
    # Chop off the trailing "/workspace" from the allowed paths because the
    # files next to them must be kept too.
    allowed = [ os.path.dirname(workspace) for workspace in spec.getAllWorkspaces() ]
    allowed.extend(spec.getTransferFiles())
    for workspace in allowed:
        w = whiteList
        for i in workspace.split('/'):
            prevDir = w
            w = w.setdefault(i, {})
        prevDir[i] = True

    # Special hack to retain coverage data in tests
    if "COVERAGE_SOURCES" in os.environ:
        keep = lambda n: n.startswith(".bob-") or n.startswith(".coverage")
    else:
        keep = lambda n: n.startswith(".bob-") # pragma: no cover
    cleanRecurse(".", whiteList, keep)

    # remove @tmp directories created by jenkins git plugins
    for step in spec.getBuiltCheckoutSteps():
        workspace = step.getWorkspacePath()
        for scm in step.getScmDirectories():
            path = os.path.join(workspace, scm+"@tmp")
            if os.path.lexists(path):
                print("Remove", path)
                removePath(path)

def getDependencies(ir):
    """Gather all package steps that are build dependencies of the built
    packages."""
    ret = set()
    for package in (s.getPackage() for s in ir.getRoots()):
        ret.update(package.getPackageStep().getAllDepSteps())
        buildStep = package.getBuildStep()
        if buildStep.isValid():
            ret.update(buildStep.getAllDepSteps())
        checkoutStep = package.getCheckoutStep()
        if checkoutStep.isValid():
            ret.update(checkoutStep.getAllDepSteps())
    ret.difference_update(ir.getRoots())
    return [s for s in ret if s.isPackageStep()]

def doJenkinsExecute(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob _jexec")
    parser.add_argument('subcommand', help="Subcommand")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for subcommand")
    parser.add_argument('--version')
    args = parser.parse_args(argv)

    if args.version and (args.version != BOB_INPUT_HASH.hex()):
        raise BuildError("Local Bob version incompatible to the one that created the Job!")

    if args.subcommand == "run":
        return doJenkinsExecuteRun(args.args, bobRoot)
    elif args.subcommand == "check-shared":
        return doJenkinsExecuteCheckShared(args.args, bobRoot)
    else:
        parser.error("Invalid sub-command")

    return 3

def doJenkinsExecuteRun(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob _jexec run")
    parser.add_argument('spec')
    args = parser.parse_args(argv)

    # Verify umask for predictable file modes. Can be set outside of Jenkins
    # but Bob requires that umask is everywhere the same for stable Build-IDs.
    # Mask 0022 is enforced on local builds and in the sandbox. But at this
    # stage the SCMs provided by Jenkins have already run. Check it and bail
    # out if different.
    # TODO: check if MSYS2 should have this check
    if not isWindows():
        if os.umask(0o0022) != 0o0022:
            raise BuildError("The umask is not 022.")

    spec = Spec(args.spec)
    ir = spec.execIR

    if spec.platform != getPlatformString():
        raise BuildError("Wrong execution environment! Configured: {} Actual: {}"
                .format(spec.platform, getPlatformString()))

    envWhiteList = ir.getRecipeSet().envWhiteList()
    meta = spec.auditMeta.copy()
    meta.update({
        "jenkins-build-tag" : os.environ.get('BUILD_TAG', ""),
        "jenkins-node" : os.environ.get('NODE_NAME', ""),
        "jenkins-build-url" : os.environ.get('BUILD_URL', ""),
    })

    dependencyBuildIds = {
        step.getWorkspacePath() : readBuildId(step)
        for step in getDependencies(ir)
    }

    cleanWorkspace(ir)

    with EventLoopWrapper() as (loop, executor):
        setVerbosity(TRACE)
        builder = LocalBuilder(TRACE, False, False, False, False, envWhiteList,
                bobRoot, False, True)
        builder.setBuildDistBuildIds(dependencyBuildIds)
        builder.setExecutor(executor)
        builder.setArchiveHandler(getArchiver(
                ir.getRecipeSet(),
                { "xfer" : spec.artifactsCopy == "jenkins" }))
        builder.setLinkDependencies(False)
        builder.setAuditMeta(meta)
        builder.setJenkinsDownloadMode(spec.download)
        builder.setJenkinsUploadMode(spec.upload)
        if spec.shareDir:
            path = Env(os.environ).substitute(spec.shareDir, "shared.dir")
            builder.setShareHandler(getShare({ 'path' : path,
                                                'quota' : spec.shareQuota }))
            builder.setShareMode(True, True)
        builder.cook(ir.getRoots(), False, loop)

    return 0

def doJenkinsExecuteCheckShared(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob _jexec check-shared")
    parser.add_argument('share')
    parser.add_argument('buildid')
    args = parser.parse_args(argv)

    path = Env(os.environ).substitute(args.share, "shared.dir")
    share = getShare({ 'path' : path })
    try:
        with open(args.buildid, 'rb') as f:
            buildId = f.read()
    except OSError as e:
        raise BuildError("Could not read build-id: " + str(e),
                         help="This may happend if the job was modified while being queued.")

    ret = 1 if share.contains(buildId) else 0
    print("{} in {}: {}".format(asHexStr(buildId), path, "found" if ret else "NOT FOUND"))
    return ret

