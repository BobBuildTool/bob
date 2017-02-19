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
from ..archive import getArchiver
from ..errors import ParseError, BuildError
from ..input import RecipeSet, walkPackagePath
from ..state import BobState
from ..tty import WarnOnce
from ..utils import asHexStr
from pipes import quote
import argparse
import ast
import base64
import datetime
import getpass
import hashlib
import http.client
import os.path
import re
import ssl
import sys
import textwrap
import urllib.parse
import xml.etree.ElementTree

warnCertificate = WarnOnce("Using HTTPS without certificate check.")

requiredPlugins = {
    "conditional-buildstep" : "Conditional BuildStep",
    "copyartifact" : "Copy Artifact Plugin",
    "git" : "Jenkins Git plugin",
    "multiple-scms" : "Jenkins Multiple SCMs plugin",
    "subversion" : "Jenkins Subversion Plug-in",
    "ws-cleanup" : "Jenkins Workspace Cleanup Plugin",
}


def genHexSlice(data, i = 0):
    r = data[i:i+96]
    while len(r) > 0:
        yield ("=" + r)
        i += 96
        r = data[i:i+96]

def wrapCommandArguments(cmd, arguments):
    ret = []
    line = cmd
    for arg in arguments:
        if len(line) + len(arg) > 96:
            ret.append(line + " \\")
            line = "  "
        line = line + " " + arg
    ret.append(line)
    return ret

class SpecHasher:
    """Track digest calculation and output as spec for bob-hash-engine"""

    def __init__(self):
        self.lines = ["{md5"]

    def update(self, data):
        if isinstance(data, bytes):
            self.lines.extend(iter(genHexSlice(asHexStr(data))))
        else:
            self.lines.append("<" + JenkinsJob._buildIdName(data))

    def digest(self):
        return "\n".join(self.lines + ["}"])


def getBuildIdSpec(step):
    """Return bob-hash-engine spec to calculate build-id of step"""
    if step.isCheckoutStep():
        return "#" + step.getWorkspacePath()
    else:
        return step.getDigest(lambda s: s, True, SpecHasher)


class JenkinsJob:
    def __init__(self, name, displayName, nameCalculator, recipe, archiveBackend):
        self.__name = name
        self.__displayName = displayName
        self.__nameCalculator = nameCalculator
        self.__recipe = recipe
        self.__isRoot = recipe.isRoot()
        self.__archive = archiveBackend
        self.__checkoutSteps = {}
        self.__buildSteps = {}
        self.__packageSteps = {}
        self.__steps = set()
        self.__deps = {}
        self.__namesPerVariant = {}
        self.__packagesPerVariant = {}

    def __getJobName(self, step):
        return self.__nameCalculator.getJobInternalName(step)

    def getName(self):
        return self.__name

    def isRoot(self):
        return self.__isRoot

    def getDescription(self, date):
        description = [
            "<h2>Recipe</h2>",
            "<p>Name: " + self.__recipe.getName()
                + "<br/>Source: " + self.__recipe.getRecipeSet().getScmStatus()
                + "<br/>Configured: " + date
                + "<br/>Bob version: " + BOB_VERSION + "</p>",
            "<h2>Packages</h2>", "<ul>"
        ]
        namesPerVariant = { vid : ", ".join(sorted(names)) for (vid, names)
            in self.__namesPerVariant.items() }
        for (vid, names) in sorted(namesPerVariant.items(), key=lambda x: x[1]):
            description.append("<li>" + names + "<ul>")
            i = 0
            allPackages = self.__packagesPerVariant[vid]
            for p in sorted(allPackages):
                if i > 5:
                    description.append("<li>... ({} more)</li>".format(len(allPackages)-i))
                    break
                description.append("<li>" + p + "</li>")
                i += 1
            description.append("</ul></li>")
        description.append("</ul>")

        return "\n".join(description)

    def addStep(self, step):
        vid = step.getVariantId()
        if step.isCheckoutStep():
            self.__checkoutSteps.setdefault(vid, step)
        elif step.isBuildStep():
            self.__buildSteps.setdefault(vid, step)
        else:
            assert step.isPackageStep()
            package = step.getPackage()
            self.__packageSteps.setdefault(vid, step)
            self.__namesPerVariant.setdefault(vid, set()).add(package.getName())
            self.__packagesPerVariant.setdefault(vid, set()).add("/".join(package.getStack()))

        if vid not in self.__steps:
            # filter dependencies that are built in this job
            self.__steps.add(vid)
            if vid in self.__deps: del self.__deps[vid]

            # add dependencies unless they are built by this job or invalid
            for dep in step.getAllDepSteps():
                if not dep.isValid(): continue
                vid = dep.getVariantId()
                if vid in self.__steps: continue
                self.__deps.setdefault(vid, dep)

    def getCheckoutSteps(self):
        return self.__checkoutSteps.values()

    def getBuildSteps(self):
        return self.__buildSteps.values()

    def getPackageSteps(self):
        return self.__packageSteps.values()

    def getDependentJobs(self):
        deps = set()
        for d in self.__deps.values():
            deps.add(self.__getJobName(d))
        return deps

    def getShebang(self, windows, errexit=True):
        if windows:
            ret = "#!bash -x"
        else:
            ret = "#!/bin/bash -x"
        if errexit:
            ret += "e"
        return ret

    def dumpStep(self, d, windows, checkIfSkip):
        cmds = []

        cmds.append(self.getShebang(windows))
        if checkIfSkip:
            cmds.append("if [[ -e {} ]] ; then"
                            .format(JenkinsJob._tgzName(d.getPackage().getPackageStep())))
            cmds.append("    echo \"Skip {} step. Artifact already downloaded...\""
                            .format(d.getLabel()))
            cmds.append("    exit 0")
            cmds.append("fi")

        if d.getJenkinsScript() is not None:
            cmds.append("mkdir -p {}".format(d.getWorkspacePath()))
            cmds.append("_sandbox=$(mktemp -d)")
            cmds.append("trap 'rm -rf $_sandbox' EXIT")
            if windows:
                cmds.append("if [ ! -z ${JENKINS_HOME} ]; then")
                cmds.append("    export JENKINS_HOME=$(echo ${JENKINS_HOME} | sed 's/\\\\/\\//g' | sed 's/://' | sed 's/^/\\//' )")
                cmds.append("fi")
                cmds.append("if [ ! -z ${WORKSPACE} ]; then")
                cmds.append("    export WORKSPACE=$(echo ${WORKSPACE} | sed 's/\\\\/\\//g' | sed 's/://' | sed 's/^/\\//')")
                cmds.append("fi")
            cmds.append("")
            cmds.append("cat >$_sandbox/.script <<'BOB_JENKINS_SANDBOXED_SCRIPT'")

            cmds.append("declare -A BOB_ALL_PATHS=(\n{}\n)".format("\n".join(sorted(
                [ "    [{}]={}".format(quote(a.getPackage().getName()),
                                       a.getExecPath())
                    for a in d.getAllDepSteps() ] ))))
            cmds.append("declare -A BOB_DEP_PATHS=(\n{}\n)".format("\n".join(sorted(
                [ "    [{}]={}".format(quote(a.getPackage().getName()),
                                       a.getExecPath())
                    for a in d.getArguments() if a.isValid() ] ))))
            cmds.append("declare -A BOB_TOOL_PATHS=(\n{}\n)".format("\n".join(sorted(
                [ "    [{}]={}".format(quote(n), os.path.join(t.getStep().getExecPath(), t.getPath()))
                    for (n,t) in d.getTools().items()] ))))
            env = { key: quote(value) for (key, value) in d.getEnv().items() }
            env.update({
                "PATH": ":".join(d.getPaths() + (
                    ["$PATH"] if d.getSandbox() is None else d.getSandbox().getPaths() )
                ),
                "LD_LIBRARY_PATH": ":".join(d.getLibraryPaths()),
                "BOB_CWD": d.getExecPath(),
            })
            for (k,v) in sorted(env.items()):
                cmds.append("export {}={}".format(k, v))

            cmds.append("set -- {}".format(" ".join(
                [ a.getExecPath() for a in d.getArguments() ])))
            cmds.append("")

            cmds.append("set -o errtrace")
            cmds.append("set -o nounset")
            cmds.append("set -o pipefail")
            cmds.append("trap 'RET=$? ; echo \"Step failed on line ${LINENO}: Exit status ${RET}; Command: ${BASH_COMMAND}\" >&2 ; exit $RET' ERR")
            cmds.append("trap 'for i in \"${_BOB_TMP_CLEANUP[@]-}\" ; do rm -f \"$i\" ; done' EXIT")
            cmds.append("cd \"${BOB_CWD}\"")
            cmds.append("")
            cmds.append("# BEGIN BUILD SCRIPT")
            cmds.append(d.getJenkinsScript())
            cmds.append("# END BUILD SCRIPT")
            cmds.append("BOB_JENKINS_SANDBOXED_SCRIPT")
            cmds.append("")

            # Add PATH into the environment whitelist so that env will still
            # find bash or bob-namespace-sandbox. PATH will always be set to
            # the correct value in the preamble.
            envWhiteList = d.getPackage().getRecipe().getRecipeSet().envWhiteList()
            envWhiteList |= set(['PATH'])
            sandbox = ["-i"]
            sandbox.extend(sorted("${{{V}+{V}=\"${V}\"}}".format(V=i) for i in envWhiteList))
            if d.getSandbox() is not None:
                cmds.append("# invoke above script through sandbox in controlled environment")
                cmds.append("mounts=( )")
                cmds.append("for i in {}/* ; do".format(d.getSandbox().getStep().getWorkspacePath()))
                cmds.append("    mounts+=( -M \"$PWD/$i\" -m \"/${i##*/}\" )")
                cmds.append("done")
                for (hostPath, sndbxPath, options) in d.getSandbox().getMounts():
                    if "nojenkins" in options: continue
                    line = "-M " + hostPath
                    if "rw" in options:
                        line += " -w " + sndbxPath
                    elif hostPath != sndbxPath:
                        line += " -m " + sndbxPath
                    line = "mounts+=( " + line + " )"
                    if "nofail" in options:
                        cmds.append(
                            """if [[ -e {HOST} ]] ; then {MOUNT} ; fi"""
                                .format(HOST=hostPath, MOUNT=line)
                            )
                    else:
                        cmds.append(line)
                cmds.append("mounts+=( -M \"$WORKSPACE/{}\" -w {} )".format(
                    d.getWorkspacePath(), d.getExecPath()))
                addDep = lambda s: (cmds.append("mounts+=( -M \"$WORKSPACE/{}\" -m {} )"
                    .format(s.getWorkspacePath(), s.getExecPath())) if s.isValid() else None)
                for s in d.getAllDepSteps(): addDep(s)
                # special handling to mount all previous steps of current package
                s = d
                while s.isValid():
                    if len(s.getArguments()) > 0:
                        s = s.getArguments()[0]
                        addDep(s)
                    else:
                        break
                sandbox.append("bob-namespace-sandbox")
                sandbox.extend(["-S", "\"$_sandbox\""])
                sandbox.extend(["-W", quote(d.getExecPath())])
                sandbox.extend(["-H", "bob"])
                sandbox.extend(["-d", "/tmp"])
                sandbox.append("\"${mounts[@]}\"")
                sandbox.append("--")
                sandbox.extend(["/bin/bash", "-x", "--", "/.script"])
            else:
                cmds.append("# invoke above script in controlled environment")
                sandbox.extend(["bash", "-x", "$_sandbox/.script"])
            cmds.extend(wrapCommandArguments("env", sandbox))

        return "\n".join(cmds)

    def dumpStepBuildIdGen(self, step):
        return [ "bob-hash-engine --state .state -o {} <<'EOF'".format(JenkinsJob._buildIdName(step)),
                 getBuildIdSpec(step),
                 "EOF" ]

    @staticmethod
    def _tgzName(d):
        return d.getWorkspacePath().replace('/', '_') + ".tgz"

    @staticmethod
    def _buildIdName(d):
        return d.getWorkspacePath().replace('/', '_') + ".buildid"

    def dumpXML(self, orig, nodes, windows, credentials, clean, options, date, authtoken):
        if orig:
            root = xml.etree.ElementTree.fromstring(orig)
            builders = root.find("builders")
            builders.clear()
            triggers = root.find("triggers")
            revBuild = triggers.find("jenkins.triggers.ReverseBuildTrigger")
            if revBuild is not None: triggers.remove(revBuild)
            scmTrigger = triggers.find("hudson.triggers.SCMTrigger")
            if scmTrigger is not None: triggers.remove(scmTrigger)
            publishers = root.find("publishers")
            archiver = publishers.find("hudson.tasks.ArtifactArchiver")
            if archiver is None:
                archiver = xml.etree.ElementTree.SubElement(
                    publishers, "hudson.tasks.ArtifactArchiver")
            else:
                archiver.clear()
            for scm in root.findall("scm"):
                root.remove(scm)
            buildWrappers = root.find("buildWrappers")
            if buildWrappers is None:
                buildWrappers = xml.etree.ElementTree.SubElement(root,
                    "buildWrappers")
            auth = root.find("authToken")
            if auth:
                if not authtoken:
                    xml.etree.ElementTree.remove(auth)
                else:
                    auth.clear()
            else:
                if authtoken:
                    xml.etree.ElementTree.SubElement(root, "authToken")
        else:
            root = xml.etree.ElementTree.Element("project")
            xml.etree.ElementTree.SubElement(root, "actions")
            xml.etree.ElementTree.SubElement(root, "description")
            if self.__name != self.__displayName:
                xml.etree.ElementTree.SubElement(
                    root, "displayName").text = self.__displayName
            xml.etree.ElementTree.SubElement(root, "keepDependencies").text = "false"
            properties = xml.etree.ElementTree.SubElement(root, "properties")
            if not self.__isRoot:
                # only retain one artifact per non-root job
                discard = xml.etree.ElementTree.fromstring("""
                    <jenkins.model.BuildDiscarderProperty>
                      <strategy class="hudson.tasks.LogRotator">
                        <daysToKeep>-1</daysToKeep>
                        <numToKeep>-1</numToKeep>
                        <artifactDaysToKeep>-1</artifactDaysToKeep>
                        <artifactNumToKeep>1</artifactNumToKeep>
                      </strategy>
                    </jenkins.model.BuildDiscarderProperty>""")
                properties.append(discard)
            if (nodes != ''):
                xml.etree.ElementTree.SubElement(root, "assignedNode").text = nodes
                xml.etree.ElementTree.SubElement(root, "canRoam").text = "false"
            else:
                xml.etree.ElementTree.SubElement(root, "canRoam").text = "true"
            xml.etree.ElementTree.SubElement(root, "disabled").text = "false"
            xml.etree.ElementTree.SubElement(
                root, "blockBuildWhenDownstreamBuilding").text = "false"
            xml.etree.ElementTree.SubElement(
                root, "blockBuildWhenUpstreamBuilding").text = "true"
            xml.etree.ElementTree.SubElement(
                root, "concurrentBuild").text = "false"
            builders = xml.etree.ElementTree.SubElement(root, "builders")
            triggers = xml.etree.ElementTree.SubElement(root, "triggers")
            publishers = xml.etree.ElementTree.SubElement(root, "publishers")
            archiver = xml.etree.ElementTree.SubElement(
                publishers, "hudson.tasks.ArtifactArchiver")
            buildWrappers = xml.etree.ElementTree.SubElement(root, "buildWrappers")
            if authtoken:
                auth = xml.etree.ElementTree.SubElement(root, "authToken")
            else:
                auth = None

        root.find("description").text = self.getDescription(date)
        scmTrigger = xml.etree.ElementTree.SubElement(
            triggers, "hudson.triggers.SCMTrigger")
        xml.etree.ElementTree.SubElement(scmTrigger, "spec").text = options.get("scm.poll")
        xml.etree.ElementTree.SubElement(
            scmTrigger, "ignorePostCommitHooks").text = "false"

        sharedDir = options.get("shared.dir", "${JENKINS_HOME}/bob")

        prepareCmds = []
        prepareCmds.append(self.getShebang(windows))
        prepareCmds.append("mkdir -p .state")
        if not windows:
            # Verify umask for predictable file modes. Can be set outside of
            # Jenkins but Bob requires that umask is everywhere the same for
            # stable Build-IDs. Mask 0022 is enforced on local builds and in
            # the sandbox. Check it and bail out if different.
            prepareCmds.append("[[ $(umask) == 0022 ]] || exit 1")

        prepareCmds.append("")
        prepareCmds.append("# delete unused files and directories from workspace")
        prepareCmds.append("pruneUnused()")
        prepareCmds.append("{")
        prepareCmds.append("   set +x")
        prepareCmds.append("   local i key value")
        prepareCmds.append("   declare -A allowed")
        prepareCmds.append("")
        prepareCmds.append("   for i in \"$@\" ; do")
        prepareCmds.append("          key=\"${i%%/*}\"")
        prepareCmds.append("          value=\"${i:$((${#key} + 1))}\"")
        prepareCmds.append("          allowed[\"$key\"]=\"${allowed[\"$key\"]+${allowed[\"$key\"]} }${value}\"")
        prepareCmds.append("   done")
        prepareCmds.append("")
        prepareCmds.append("   for i in * ; do")
        prepareCmds.append("          if [[ ${allowed[\"$i\"]+true} ]] ; then")
        prepareCmds.append("              if [[ ! -z ${allowed[\"$i\"]} && -d $i ]] ; then")
        prepareCmds.append("                  pushd \"$i\" > /dev/null")
        prepareCmds.append("                  pruneUnused ${allowed[\"$i\"]}")
        prepareCmds.append("                  popd > /dev/null")
        prepareCmds.append("              fi")
        prepareCmds.append("          elif [[ -e \"$i\" ]] ; then")
        prepareCmds.append("              echo \"Remove $PWD/$i\"")
        prepareCmds.append("              chmod -R u+rw \"$i\"")
        prepareCmds.append("              rm -rf \"$i\"")
        prepareCmds.append("          fi")
        prepareCmds.append("   done")
        prepareCmds.append("}")
        prepareCmds.append("")
        whiteList = []
        whiteList.extend([ JenkinsJob._tgzName(d) for d in self.__deps.values()])
        whiteList.extend([ JenkinsJob._buildIdName(d) for d in self.__deps.values()])
        whiteList.extend([ d.getWorkspacePath() for d in self.__checkoutSteps.values() ])
        whiteList.extend([ d.getWorkspacePath() for d in self.__buildSteps.values() ])
        prepareCmds.extend(wrapCommandArguments("pruneUnused", sorted(whiteList)))
        prepareCmds.append("set -x")

        deps = sorted(self.__deps.values())
        if deps:
            revBuild = xml.etree.ElementTree.SubElement(
                triggers, "jenkins.triggers.ReverseBuildTrigger")
            xml.etree.ElementTree.SubElement(revBuild, "spec").text = ""
            xml.etree.ElementTree.SubElement(
                revBuild, "upstreamProjects").text = ", ".join(
                    [ self.__getJobName(d) for d in deps ])
            threshold = xml.etree.ElementTree.SubElement(revBuild, "threshold")
            xml.etree.ElementTree.SubElement(threshold, "name").text = "SUCCESS"
            xml.etree.ElementTree.SubElement(threshold, "ordinal").text = "0"
            xml.etree.ElementTree.SubElement(threshold, "color").text = "BLUE"
            xml.etree.ElementTree.SubElement(threshold, "completeBuild").text = "true"

            # copy deps into workspace
            for d in deps:
                if d.isShared():
                    vid = asHexStr(d.getVariantId())
                    guard = xml.etree.ElementTree.SubElement(
                        builders, "org.jenkinsci.plugins.conditionalbuildstep.singlestep.SingleConditionalBuilder", attrib={
                            "plugin" : "conditional-buildstep@1.3.3",
                        })
                    notCond = xml.etree.ElementTree.SubElement(
                        guard, "condition", attrib={
                            "class" : "org.jenkins_ci.plugins.run_condition.logic.Not",
                            "plugin" : "run-condition@1.0",
                        })
                    fileCond = xml.etree.ElementTree.SubElement(
                        notCond, "condition", attrib={
                            "class" : "org.jenkins_ci.plugins.run_condition.core.FileExistsCondition"
                        })
                    xml.etree.ElementTree.SubElement(
                        fileCond, "file").text = sharedDir+"/"+vid[0:2]+"/"+vid[2:]
                    xml.etree.ElementTree.SubElement(
                        fileCond, "baseDir", attrib={
                            "class" : "org.jenkins_ci.plugins.run_condition.common.BaseDirectory$Workspace"
                        })
                    cp = xml.etree.ElementTree.SubElement(
                        guard, "buildStep", attrib={
                            "class" : "hudson.plugins.copyartifact.CopyArtifact",
                            "plugin" : "copyartifact@1.32.1",
                        })
                    xml.etree.ElementTree.SubElement(
                        guard, "runner", attrib={
                            "class" : "org.jenkins_ci.plugins.run_condition.BuildStepRunner$Fail",
                            "plugin" : "run-condition@1.0",
                        })
                else:
                    cp = xml.etree.ElementTree.SubElement(
                        builders, "hudson.plugins.copyartifact.CopyArtifact", attrib={
                            "plugin" : "copyartifact@1.32.1"
                        })
                xml.etree.ElementTree.SubElement(
                    cp, "project").text = self.__getJobName(d)
                xml.etree.ElementTree.SubElement(
                    cp, "filter").text = JenkinsJob._tgzName(d)+","+JenkinsJob._buildIdName(d)
                xml.etree.ElementTree.SubElement(
                    cp, "target").text = ""
                xml.etree.ElementTree.SubElement(
                    cp, "excludes").text = ""
                xml.etree.ElementTree.SubElement(
                    cp, "selector", attrib={
                        "class" : "hudson.plugins.copyartifact.StatusBuildSelector"
                    })
                xml.etree.ElementTree.SubElement(
                    cp, "doNotFingerprintArtifacts").text = "true"

            # extract deps
            prepareCmds.append("\n# extract deps")
            for d in deps:
                if d.isShared():
                    vid = asHexStr(d.getVariantId())
                    prepareCmds.append(textwrap.dedent("""\
                        if [ ! -d {SHARED}/{VID1}/{VID2} ] ; then
                            mkdir -p {SHARED}
                            T=$(mktemp -d -p {SHARED})
                            tar xf {TGZ} -C $T
                            mkdir -p {SHARED}/{VID1}
                            mv -T $T {SHARED}/{VID1}/{VID2} || rm -rf $T
                        fi
                        mkdir -p {WSP_DIR}
                        ln -sfT {SHARED}/{VID1}/{VID2} {WSP_PATH}
                        """.format(VID1=vid[0:2], VID2=vid[2:], TGZ=JenkinsJob._tgzName(d),
                                   WSP_DIR=os.path.dirname(d.getWorkspacePath()),
                                   WSP_PATH=d.getWorkspacePath(),
                                   SHARED=sharedDir)))
                else:
                    prepareCmds.append("mkdir -p " + d.getWorkspacePath())
                    prepareCmds.append("tar zxf {} -C {}".format(
                        JenkinsJob._tgzName(d), d.getWorkspacePath()))

        prepare = xml.etree.ElementTree.SubElement(builders, "hudson.tasks.Shell")
        xml.etree.ElementTree.SubElement(prepare, "command").text = "\n".join(
            prepareCmds)

        # checkout steps
        checkoutSCMs = []
        for d in sorted(self.__checkoutSteps.values()):
            checkout = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(
                checkout, "command").text = self.dumpStep(d, windows, False)
            checkoutSCMs.extend(d.getJenkinsXml(credentials, options))

        if len(checkoutSCMs) > 1:
            scm = xml.etree.ElementTree.SubElement(
                root, "scm", attrib={
                    "class"  : "org.jenkinsci.plugins.multiplescms.MultiSCM",
                    "plugin" : "multiple-scms@0.3"
                })
            scms = xml.etree.ElementTree.SubElement(scm, "scms")
            for scm in checkoutSCMs:
                scm.tag = scm.attrib["class"]
                del scm.attrib["class"]
                scms.append(scm)
        elif len(checkoutSCMs) == 1:
            root.append(checkoutSCMs[0])
        else:
            scm = xml.etree.ElementTree.SubElement(
                root, "scm", attrib={"class" : "hudson.scm.NullSCM"})

        # calculate Build-ID
        buildIdCalc = [
            self.getShebang(windows),
            "# create build-ids"
        ]
        for d in sorted(self.__checkoutSteps.values()):
            buildIdCalc.extend(self.dumpStepBuildIdGen(d))
        for d in sorted(self.__buildSteps.values()):
            buildIdCalc.extend(self.dumpStepBuildIdGen(d))
        for d in sorted(self.__packageSteps.values()):
            buildIdCalc.extend(self.dumpStepBuildIdGen(d))
        checkout = xml.etree.ElementTree.SubElement(
            builders, "hudson.tasks.Shell")
        xml.etree.ElementTree.SubElement(
            checkout, "command").text = "\n".join(buildIdCalc)

        # download if possible
        downloadCmds = []
        for d in sorted(self.__packageSteps.values()):
            # only download tools if built in sandbox
            if d.doesProvideTools() and (d.getSandbox() is None):
                continue
            cmd = self.__archive.download(d, JenkinsJob._buildIdName(d), JenkinsJob._tgzName(d))
            if not cmd: continue
            downloadCmds.append(cmd)
        if downloadCmds:
            downloadCmds.insert(0, self.getShebang(windows, False))
            downloadCmds.append("true # don't let downloads fail the build")
            download = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(
                download, "command").text = "\n".join(downloadCmds)

        # build steps
        for d in sorted(self.__buildSteps.values()):
            build = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(
                build, "command").text = self.dumpStep(d, windows, True)

        # package steps
        publish = []
        for d in sorted(self.__packageSteps.values()):
            package = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(package, "command").text = "\n".join([
                self.dumpStep(d, windows, True),
                "", "# pack result for archive and inter-job exchange",
                "cd $WORKSPACE",
                "tar zcfv {} -C {} .".format(JenkinsJob._tgzName(d), d.getWorkspacePath()),
                "" if d.doesProvideTools() and (d.getSandbox() is None)
                    else self.__archive.upload(d, JenkinsJob._buildIdName(d), JenkinsJob._tgzName(d))
            ])
            publish.append(JenkinsJob._tgzName(d))
            publish.append(JenkinsJob._buildIdName(d))

        # install shared packages
        installCmds = []
        for d in sorted(self.__packageSteps.values()):
            if d.isShared():
                vid = asHexStr(d.getVariantId())
                installCmds.append(textwrap.dedent("""\
                # install shared package atomically
                if [ ! -d {SHARED}/{VID1}/{VID2} ] ; then
                    mkdir -p {SHARED}
                    T=$(mktemp -d -p {SHARED})
                    tar xf {TGZ} -C $T
                    mkdir -p {SHARED}/{VID1}
                    mv -T $T {SHARED}/{VID1}/{VID2} || rm -rf $T
                fi""".format(TGZ=JenkinsJob._tgzName(d), VID1=vid[0:2], VID2=vid[2:],
                             SHARED=sharedDir)))
        if installCmds:
            installCmds.insert(0, self.getShebang(windows))
            install = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(
                install, "command").text = "\n".join(installCmds)

        xml.etree.ElementTree.SubElement(
            archiver, "artifacts").text = ",".join(publish)
        xml.etree.ElementTree.SubElement(
            archiver, "allowEmptyArchive").text = "false"

        # clean build wrapper
        preBuildClean = buildWrappers.find("hudson.plugins.ws__cleanup.PreBuildCleanup")
        if preBuildClean is not None: buildWrappers.remove(preBuildClean)
        if clean:
            preBuildClean = xml.etree.ElementTree.SubElement(buildWrappers,
                "hudson.plugins.ws__cleanup.PreBuildCleanup",
                attrib={"plugin" : "ws-cleanup@0.30"})
            xml.etree.ElementTree.SubElement(preBuildClean, "deleteDirs").text = "true"
            xml.etree.ElementTree.SubElement(preBuildClean, "cleanupParameter")
            xml.etree.ElementTree.SubElement(preBuildClean, "externalDelete")

        # add authtoken if set in options
        if auth:
            auth.text = authtoken

        return xml.etree.ElementTree.tostring(root, encoding="UTF-8")


    def dumpGraph(self, done):
        for d in self.__deps.values():
            depName = self.__getJobName(d)
            key = (self.__name, depName)
            if key not in done:
                print(" \"{}\" -> \"{}\";".format(self.__name, depName))
                done.add(key)


class JobNameCalculator:
    """Utility class to calculate job names for packages.

    By default the name of the recipe is used for the job name. Depending on
    the package structure this may lead to cyclic job dependencies. If such
    cycles are found the package name is used for affected jobs. If this still
    leads to cycles the package name with an incrementing suffix is used.
    """

    def __init__(self, prefix):
        self.__prefix = prefix
        self.__packages = {} # map all known packages to their job names
        self.__names = {} # map job names to a list of held packages
        self.__roots = [] # list of root packages
        self.__regexJobName = re.compile(r'[^a-zA-Z0-9-_]', re.DOTALL)
        self.__splits = set() # names that have been split already

    def addPackage(self, package):
        step = package.getPackageStep()
        self.__roots.append(step)
        self.__addStep(step)

    def __addStep(self, step):
        variantId = step.getVariantId()
        if variantId not in self.__packages:
            if step.isPackageStep():
                name = step.getPackage().getRecipe().getName()
                self.__packages[variantId] = (step, name)
                self.__names.setdefault(name, []).append(step)
            for d in step.getAllDepSteps():
                self.__addStep(d)

    def sanitize(self):
        """Make sure jobs are not cyclic.

        We first build a dependency graph of the jobs. As long as we find
        cycles we split all jobs that were the first node in a given cycle.
        """
        toSplit = True
        while toSplit:
            rootNames = [ self.__packages[p.getVariantId()][1] for p in self.__roots ]
            depGraph = {}
            for r in rootNames: self.__buildDepGraph(r, depGraph)
            toSplit = set()
            for r in rootNames: toSplit |= self.__findCycle(r, depGraph)
            for r in toSplit: self.__split(r)

    def __buildDepGraph(self, name, depGraph):
        """Build a dependency graph.

        Traverse on step level build a graph that holds only the job names.
        """
        if name in depGraph: return
        depGraph[name] = subDeps = set()
        for packageStep in self.__names[name]:
            backlog = list(packageStep.getAllDepSteps())
            while backlog:
                d = backlog.pop(0)
                if d.isPackageStep():
                    vid = d.getPackage().getPackageStep().getVariantId()
                    subName = self.__packages[vid][1]
                    subDeps.add(subName)
                    self.__buildDepGraph(subName, depGraph)
                else:
                    backlog.extend(d.getAllDepSteps())

    def __findCycle(self, name, depGraph):
        """Find cycles in 'depGraph' starting at 'name'.

        This does a depth first traversal of the tree while optimizing for the
        usual case where no or only a few cycles are found. For that, the set
        of all reachable nodes from a fully traversed node is cached. This can
        only be done for sub-trees without cycles, though.
        """
        split = set()
        processed = {}
        stack = set()

        def walk(n):
            if n in stack:
                # Found first node in cycle. Add to split set and prevent
                # caching of trees above this node.
                split.add(n)
                return False, set()
            else:
                ret = set()
                cache = True
                stack.add(n)
                if n in processed:
                    subNodes = processed[n]
                    if subNodes.isdisjoint(stack):
                        # Found cached sub-tree. We're done...
                        stack.remove(n)
                        return True, subNodes
                # Descend to sub-nodes, trying to build a cache
                for d in sorted(depGraph[n]):
                    ret.add(d)
                    subCache, subNodes = walk(d)
                    cache = cache and subCache
                    ret |= subNodes
                if cache: processed.setdefault(n, ret)
                stack.remove(n)
                return cache, ret

        # I'm walking...
        walk(name)
        return split

    def __split(self, name):
        """Split a job.

        As a first measure we use the package names trying to separate packages
        into their own jobs but keep the different variants of each package
        together.  If that doesn't help we will start adding suffixes.
        """
        packageSteps = self.__names[name].copy()

        # Does it make sense to split the job?
        if len(packageSteps) <= 1:
            return

        # Do we have to add a counting suffix?
        if name in self.__splits:
            newNames = [ (p, name+"-"+str(n)) for (p, n) in zip(packageSteps, range(1, 1000)) ]
        else:
            newNames = [ (p, p.getPackage().getName()) for p in packageSteps ]
            self.__splits.add(name)

        # re-arrange the naming graph
        del self.__names[name]
        for (p, n) in newNames:
            self.__splits.add(n)
            self.__packages[p.getVariantId()] = (p, n)
            self.__names.setdefault(n, []).append(p)

    def getJobDisplayName(self, step):
        if step.isPackageStep():
            vid = step.getVariantId()
        else:
            vid = step.getPackage().getPackageStep().getVariantId()
        (_p, name) = self.__packages[vid]
        return self.__prefix + name

    def getJobInternalName(self, step):
        return self.__regexJobName.sub('_', self.getJobDisplayName(step))


def _genJenkinsJobs(step, jobs, nameCalculator, archiveBackend, seenPackages):
    name = nameCalculator.getJobInternalName(step)
    if name in jobs:
        jj = jobs[name]
    else:
        recipe = step.getPackage().getRecipe()
        jj = JenkinsJob(name, nameCalculator.getJobDisplayName(step), nameCalculator,
                        recipe, archiveBackend)
        jobs[name] = jj

    # add step to job
    jj.addStep(step)

    # always recurse on arguments
    for d in sorted(step.getArguments(), key=lambda d: d.getPackage().getName()):
        if d.isValid(): _genJenkinsJobs(d, jobs, nameCalculator, archiveBackend,
                                            seenPackages)

    # Recurse on tools and sandbox only for package steps. Also do an early
    # reject if the particular package stack was already seen. This is safe as
    # the same package stack cannot have different variant-ids.
    if step.isPackageStep():
        for (name, tool) in sorted(step.getTools().items()):
            toolStep = tool.getStep()
            stack = "/".join(toolStep.getPackage().getStack())
            if stack not in seenPackages:
                seenPackages.add(stack)
                _genJenkinsJobs(toolStep, jobs, nameCalculator, archiveBackend,
                                seenPackages)

        sandbox = step.getSandbox()
        if sandbox is not None:
            sandboxStep = sandbox.getStep()
            stack = "/".join(sandboxStep.getPackage().getStack())
            if stack not in seenPackages:
                seenPackages.add(stack)
                _genJenkinsJobs(sandboxStep, jobs, nameCalculator, archiveBackend,
                                seenPackages)

def jenkinsNameFormatter(step, props):
    return step.getPackage().getName().replace('::', "/") + "/" + step.getLabel()

def jenkinsNamePersister(jenkins, wrapFmt):

    def persist(step, props):
        return BobState().getJenkinsByNameDirectory(
            jenkins, wrapFmt(step, props), step.getVariantId())

    def fmt(step, mode, props):
        if mode == 'workspace':
            return persist(step, props)
        else:
            assert mode == 'exec'
            if step.getSandbox() is None:
                return os.path.join("$PWD", quote(persist(step, props)))
            else:
                return os.path.join("/bob", asHexStr(step.getVariantId()), "workspace")

    return fmt

def genJenkinsJobs(recipes, jenkins):
    jobs = {}
    config = BobState().getJenkinsConfig(jenkins)
    prefix = config["prefix"]
    archiveHandler = getArchiver(recipes)
    archiveHandler.wantUpload(config.get("upload", False))
    archiveHandler.wantDownload(config.get("download", False))
    nameFormatter = recipes.getHook('jenkinsNameFormatter')
    rootPackages = recipes.generatePackages(
        jenkinsNamePersister(jenkins, nameFormatter),
        config.get('defines', {}),
        config.get('sandbox', False))

    nameCalculator = JobNameCalculator(prefix)
    rootPackages = [ walkPackagePath(rootPackages, r) for r in config["roots"] ]
    for root in rootPackages:
        nameCalculator.addPackage(root)

    nameCalculator.sanitize()
    for root in sorted(rootPackages, key=lambda root: root.getName()):
        _genJenkinsJobs(root.getPackageStep(), jobs, nameCalculator, archiveHandler, set())

    return jobs

def genJenkinsBuildOrder(jobs):
    def visit(j, pending, processing, order):
        if j in processing:
            raise ParseError("Jobs are cyclic")
        if j in pending:
            processing.add(j)
            for d in jobs[j].getDependentJobs():
                visit(d, pending, processing, order)
            pending.remove(j)
            processing.remove(j)
            order.append(j)

    order = []
    pending = set(jobs.keys())
    processing = set()
    while pending:
        j = pending.pop()
        pending.add(j)
        visit(j, pending, processing, order)

    return order

def doJenkinsAdd(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins add")
    parser.add_argument("-n", "--nodes", default="", help="Label for Jenkins Slave")
    parser.add_argument("-o", default=[], action='append', dest='options',
                        help="Set extended Jenkins options")
    parser.add_argument("-w", "--windows", default=False, action='store_true', help="Jenkins is running on Windows. Produce cygwin compatible scripts.")
    parser.add_argument("-p", "--prefix", default="", help="Prefix for jobs")
    parser.add_argument("-r", "--root", default=[], action='append',
                        help="Root package (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
                        help="Override default environment variable")
    parser.add_argument('--keep', action='store_true', default=False,
        help="Keep obsolete jobs by disabling them")
    parser.add_argument('--download', default=False, action='store_true',
        help="Download from binary archive")
    parser.add_argument('--upload', default=False, action='store_true',
        help="Upload to binary archive")
    parser.add_argument('--no-sandbox', action='store_false', dest='sandbox', default=True,
        help="Disable sandboxing")
    parser.add_argument("--credentials", help="Credentials UUID for SCM checkouts")
    parser.add_argument('--clean', action='store_true', default=False,
        help="Do clean builds (clear workspace)")
    parser.add_argument("name", help="Symbolic name for server")
    parser.add_argument("url", help="Server URL")
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

    options = {}
    for i in args.options:
        (opt, sep, val) = i.partition("=")
        if sep != "=":
            parser.error("Malformed plugin option: "+i)
        if val != "":
            options[opt] = val

    if args.name in BobState().getAllJenkins():
        print("Jenkins '{}' already added.".format(args.name), file=sys.stderr)
        sys.exit(1)

    url = urllib.parse.urlparse(args.url)
    urlPath = url.path
    if not urlPath.endswith("/"): urlPath = urlPath + "/"

    config = {
        "url" : {
            "scheme" : url.scheme,
            "server" : url.hostname,
            "port" : url.port,
            "path" : urlPath,
            "username" : url.username,
            "password" : url.password,
        },
        "roots" : args.root,
        "prefix" : args.prefix,
        "nodes" : args.nodes,
        "defines" : defines,
        "download" : args.download,
        "upload" : args.upload,
        "sandbox" : args.sandbox,
        "windows" : args.windows,
        "credentials" : args.credentials,
        "clean" : args.clean,
        "keep" : args.keep,
        "options" : options
    }
    BobState().addJenkins(args.name, config)

def doJenkinsExport(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins export")
    parser.add_argument("name", help="Jenkins server to export")
    parser.add_argument("dir", help="Directory where job XMLs should be stored")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.dir):
        print("Export path '{}' is not a directory!".format(args.dir),
              file=sys.stderr)
        sys.exit(1)

    jenkinsJobCreate = recipes.getHookStack('jenkinsJobCreate')

    jobs = genJenkinsJobs(recipes, args.name)
    buildOrder = genJenkinsBuildOrder(jobs)
    config = BobState().getJenkinsConfig(args.name)
    windows = config.get("windows", False)
    nodes = config.get("nodes", "")
    credentials = config.get("credentials")
    clean = config.get("clean", False)
    options = config.get("options", {})
    authtoken = config.get("authtoken")

    for j in buildOrder:
        job = jobs[j]
        info = {
            'alias' : args.name,
            'name' : job.getName(),
            'url' : getUrl(config),
            'prefix' : config.get('prefix'),
            'nodes' : nodes,
            'sandbox' : config['sandbox'],
            'windows' : windows,
            'checkoutSteps' : job.getCheckoutSteps(),
            'buildSteps' : job.getBuildSteps(),
            'packageSteps' : job.getPackageSteps()
        }
        xml = applyHooks(jenkinsJobCreate, job.dumpXML(None, nodes, windows,
            credentials, clean, options, "now", authtoken), info)
        with open(os.path.join(args.dir, job.getName()+".xml"), "wb") as f:
            f.write(xml)

def doJenkinsGraph(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins graph")
    parser.add_argument("name", help="Jenkins server to generate GraphViz digraph")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    jobs = genJenkinsJobs(recipes, args.name)
    print("digraph jobs {")
    done = set()
    for j in jobs.values():
        j.dumpGraph(done)
    print("}")

def doJenkinsLs(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins ls")
    parser.add_argument("-v", "--verbose", default=0, action='count',
        help="Show additional information")
    args = parser.parse_args(argv)

    for j in sorted(BobState().getAllJenkins()):
        print(j)
        cfg = BobState().getJenkinsConfig(j)
        if args.verbose >= 1:
            print("    URL:", getUrl(cfg))
            print("    Roots:", ", ".join(cfg['roots']))
            if cfg.get('prefix'):
                print("    Prefix:", cfg['prefix'])
            if cfg.get('nodes'):
                print("    Nodes:", cfg['nodes'])
            if cfg.get('defines'):
                print("    Defines:", ", ".join([ k+"="+v for (k,v) in cfg['defines'].items() ]))
            print("    Obsolete jobs:", "keep" if cfg.get('keep', False) else "delete")
            print("    Download:", "enabled" if cfg.get('download', False) else "disabled")
            print("    Upload:", "enabled" if cfg.get('upload', False) else "disabled")
            print("    Clean builds:", "enabled" if cfg.get('clean', False) else "disabled")
            print("    Sandbox:", "enabled" if cfg.get("sandbox", False) else "disabled")
            if cfg.get('credentials'):
                print("    Credentials:", cfg['credentials'])
            options = cfg.get('options')
            if options:
                print("    Extended options:", ", ".join([ k+"="+v for (k,v) in options.items() ]))
        if args.verbose >= 2:
            print("    Jobs:", ", ".join(sorted(BobState().getJenkinsAllJobs(j))))

def getUrl(config):
    url = config["url"]
    if url.get('username'):
        userPass = url['username']
        if url.get('password'):
            userPass += ":" + url['password']
        userPass += "@"
    else:
        userPass = ""
    return "{}://{}{}{}{}".format(url['scheme'], userPass, url['server'],
        ":{}".format(url['port']) if url.get('port') else "", url['path'])

class JenkinsConnection:
    """Connection to a Jenkins server abstracting the REST API"""

    def __init__(self, config):
        self.__config = config
        self.__init()

    def __init(self):
        # create connection
        url = self.__config["url"]
        if url["scheme"] == 'http':
            connection = http.client.HTTPConnection(url["server"],
                                                    url.get("port"))
        elif url["scheme"] == 'https':
            ctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            warnCertificate.warn()
            connection = http.client.HTTPSConnection(url["server"],
                                                     url.get("port"), context=ctx)
        else:
            raise BuildError("Unsupported Jenkins URL scheme: '{}'".format(
                url["scheme"]))

        # remember basic settings
        self.__connection = connection
        self.__headers = { "Content-Type": "application/xml" }

        # handle authorization
        if url.get("username"):
            passwd = url.get("password")
            if passwd is None:
                passwd = getpass.getpass()
            userPass = url["username"] + ":" + passwd
            self.__headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")

        # get CSRF token
        connection.request("GET", url["path"] + "crumbIssuer/api/xml",
                           headers=self.__headers)
        response = connection.getresponse()
        if response.status == 200:
            resp = xml.etree.ElementTree.fromstring(response.read())
            crumb = resp.find("crumb").text
            field = resp.find("crumbRequestField").text
            self.__headers[field] = crumb
        else:
            # dump response
            response.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.__connection.close()
        return False

    def _send(self, method, path, body=None, headers=None):
        if headers is None:
            headers = self.__headers

        # Retry in case of BadStatusLine or OSError (broken pipe). This happens
        # sometimes if the server is under load. Running Bob again essentially
        # does that anyway...
        retries = 3
        while True:
            try:
                self.__connection.request(method, self.__config["url"]["path"] + path, body,
                                          headers)
                return self.__connection.getresponse()
            except (http.client.BadStatusLine, OSError) as e:
                retries -= 1
                if retries <= 0: raise
                print("Jenkins connection dropped ({}). Retrying...".format(str(e)),
                      file=sys.stderr)
                self.__init()

    def checkPlugins(self):
        response = self._send("GET", "pluginManager/api/python?depth=1")
        if response.status != 200:
            print("Warning: could not verify plugins: HTTP error: {} {}"
                    .format(response.status, response.reason),
                file=sys.stderr)
            response.read()
        else:
            try:
                plugins =  ast.literal_eval(response.read().decode("utf8"))["plugins"]
                required = set(requiredPlugins.keys())
                for p in plugins:
                    if p["shortName"] not in required: continue
                    if not p["active"] or not p["enabled"]:
                        raise BuildError("Plugin not enabled: " + requiredPlugins[p["shortName"]])
                    required.remove(p["shortName"])
                if required:
                    raise BuildError("Missing plugin(s): " + ", ".join(
                        requiredPlugins[p] for p in required))
            except BuildError:
                raise
            except:
                raise BuildError("Malformed Jenkins response while checking plugins!")

    def createJob(self, name, jobXML):
        response = self._send("POST", "createItem?name=" + name, jobXML)
        if response.status == 200:
            response.read()
            return True
        if response.status == 400:
            response.read()
            return False
        else:
            raise BuildError("Error creating '{}': HTTP error: {} {}"
                .format(name, response.status, response.reason))

    def deleteJob(self, name):
        response = self._send("POST", "job/" + name + "/doDelete")
        if response.status != 302 and response.status != 404:
            raise BuildError("Error deleting '{}': HTTP error: {} {}".format(
                name, response.status, response.reason))
        response.read()

    def fetchConfig(self, name):
        response = self._send("GET", "job/" + name + "/config.xml")
        if response.status == 200:
            return response.read()
        elif response.status == 404:
            response.read()
            return None
        else:
            raise BuildError("Warning: could not download '{}' job config: HTTP error: {} {}"
                    .format(name, response.status, response.reason))

    def updateConfig(self, name, jobXML):
        response = self._send("POST", "job/" + name + "/config.xml", jobXML)
        if response.status != 200:
            raise BuildError("Error updating '{}': HTTP error: {} {}"
                .format(name, response.status, response.reason))
        response.read()

    def schedule(self, name):
        ret = True
        response = self._send("POST", "job/" + name + "/build")
        if response.status != 201:
            print("Error scheduling '{}': HTTP error: {} {}"
                    .format(name, response.status, response.reason),
                file=sys.stderr)
            ret = False
        response.read()
        return ret

    def enableJob(self, name):
        response = self._send("POST", "job/" + name + "/enable")
        if response.status != 200 and response.status != 302:
            raise BuildError("Error enabling '{}': HTTP error: {} {}"
                .format(name, response.status, response.reason))
        response.read()

    def disableJob(self, name):
        response = self._send("POST", "job/" + name + "/disable")
        if response.status != 200 and response.status != 302:
            raise BuildError("Error disabling '{}': HTTP error: {} {}"
                .format(name, response.status, response.reason))
        response.read()

    def setDescription(self, name, description):
        body = urllib.parse.urlencode({"description" : description})
        headers = self.__headers.copy()
        headers.update({
            "Content-Type" : "application/x-www-form-urlencoded",
            "Accept" : "text/plain"
        })
        response = self._send("POST", "job/" + name + "/description", body,
                              headers)
        if response.status >= 400:
            print(name, description, headers, body)
            raise BuildError("Error setting description of '{}': HTTP error: {} {}"
                .format(name, response.status, response.reason))
        response.read()


def doJenkinsPrune(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins prune",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""Prune jobs from Jenkins server.

By default all jobs managed by the Jenkins alias will be deleted. If the 'keep'
option is enabled for this alias you may use the '--obsolete' option to delete
only currently disabled (obsolete) jobs. Alternatively you may delete all
intermediate jobs and keep only the root jobs by using '--intermediate'. This
will disable the root jobs because they cannot run anyawy without failing.
""")
    parser.add_argument("name", help="Prune jobs from Jenkins server")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--obsolete', action='store_true', default=False,
        help="Delete only obsolete jobs")
    group.add_argument('--intermediate', action='store_true', default=False,
        help="Delete everything except root jobs")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    verbose = args.verbose - args.quiet

    def printLine(level, job, *args):
        if level <= verbose:
            if job:
                print(job + ":", *args)
            else:
                print(*args)

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)

    # connect to server
    with JenkinsConnection(config) as connection:
        if args.obsolete:
            # delete all disabled jobs
            for name in existingJobs:
                jobConfig = BobState().getJenkinsJobConfig(args.name, name)
                if jobConfig.get('enabled', True): continue
                printLine(0, name, "Delete job...")
                connection.deleteJob(name)
                BobState().delJenkinsJob(args.name, name)
        elif args.intermediate:
            jobs = genJenkinsJobs(recipes, args.name)
            roots = [ name for (name, job) in jobs.items() if job.isRoot() ]
            # disable root jobs
            for name in roots:
                if name not in existingJobs: continue
                jobConfig = BobState().getJenkinsJobConfig(args.name, name)
                if not jobConfig.get('enabled', True): continue
                printLine(0, name, "Disable root job...")
                connection.disableJob(name)
                jobConfig['enabled'] = False
                BobState().setJenkinsJobConfig(args.name, name, jobConfig)
            # delete everything except root jobs
            for name in existingJobs:
                if name in roots: continue
                printLine(0, name, "Delete job...")
                connection.deleteJob(name)
                BobState().delJenkinsJob(args.name, name)
        else:
            # nuke all jobs
            for name in existingJobs:
                printLine(0, name, "Delete job...")
                connection.deleteJob(name)
                BobState().delJenkinsJob(args.name, name)

def doJenkinsRm(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins rm")
    parser.add_argument("name", help="Removed Jenkins server")
    parser.add_argument('-f', '--force', default=False, action='store_true',
                        help="Remove even if there are configured jobs")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)
    if existingJobs and not args.force:
        print("Jenkins '{}' still has configured jobs.".format(args.name),
              file=sys.stderr)
        print("Either do a 'bob jenins prune ...' first or re-run the command with '-f'",
              file=sys.stderr)
        sys.exit(1)

    BobState().delJenkins(args.name)

def applyHooks(hooks, job, info, reverse=False):
    for h in (reversed(hooks) if reverse else hooks):
        job = h(job, **info)
    return job

def doJenkinsPush(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins push")
    parser.add_argument("name", help="Push jobs to Jenkins server")
    parser.add_argument("-f", "--force", action="store_true", default=False,
                        help="Overwrite existing jobs")
    parser.add_argument("--no-trigger", action="store_true", default=False,
                        help="Do not trigger build for updated jobs")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)
    jobs = genJenkinsJobs(recipes, args.name)
    buildOrder = genJenkinsBuildOrder(jobs)

    # get hooks
    jenkinsJobCreate = recipes.getHookStack('jenkinsJobCreate')
    jenkinsJobPreUpdate = recipes.getHookStack('jenkinsJobPreUpdate')
    jenkinsJobPostUpdate = recipes.getHookStack('jenkinsJobPostUpdate')

    windows = config.get("windows", False)
    nodes = config.get("nodes", "")
    credentials = config.get("credentials")
    clean = config.get("clean", False)
    keep = config.get("keep", False)
    options = config.get("options", {})
    authtoken = config.get("authtoken")
    updatedJobs = {}
    verbose = args.verbose - args.quiet
    date = str(datetime.datetime.now())

    def printLine(level, job, *args):
        if level <= verbose:
            if job:
                print(job + ":", *args)
            else:
                print(*args)

    def printNormal(job, *args): printLine(0, job, *args)
    def printInfo(job, *args): printLine(1, job, *args)
    def printDebug(job, *args): printLine(2, job, *args)

    # connect to server
    with JenkinsConnection(config) as connection:
        # verify plugin state
        printDebug(None, "Check available plugins...")
        connection.checkPlugins()

        # push new jobs / reconfigure existing ones
        for (name, job) in jobs.items():
            info = {
                'alias' : args.name,
                'name' : name,
                'url' : getUrl(config),
                'prefix' : config.get('prefix'),
                'nodes' : nodes,
                'sandbox' : config['sandbox'],
                'windows' : windows,
                'checkoutSteps' : job.getCheckoutSteps(),
                'buildSteps' : job.getBuildSteps(),
                'packageSteps' : job.getPackageSteps(),
                'authtoken': authtoken
            }

            # get original XML if it exists
            if name in existingJobs:
                printDebug(name, "Retrieve configuration...")
                origXML = connection.fetchConfig(name)
                if origXML is None:
                    # Job was deleted
                    printDebug(name, "Forget job. Has been deleted on the server!")
                    existingJobs.remove(name)
                    BobState().delJenkinsJob(args.name, name)
                else:
                    oldJobConfig = BobState().getJenkinsJobConfig(args.name, name)
            else:
                origXML = None

            # calculate new job configuration
            try:
                if origXML is not None:
                    jobXML = applyHooks(jenkinsJobPreUpdate, origXML, info, True)
                else:
                    jobXML = None

                jobXML = job.dumpXML(jobXML, nodes, windows, credentials, clean, options, date, authtoken)

                if origXML is not None:
                    jobXML = applyHooks(jenkinsJobPostUpdate, jobXML, info)
                    # job hash is based on unmerged config to detect just our changes
                    hashXML = applyHooks(jenkinsJobCreate,
                        job.dumpXML(None, nodes, windows, credentials, clean, options, date, authtoken),
                        info)
                else:
                    jobXML = applyHooks(jenkinsJobCreate, jobXML, info)
                    hashXML = jobXML

                # remove description from job hash
                root = xml.etree.ElementTree.fromstring(hashXML)
                description = root.find("description").text
                newDescrHash = hashlib.sha1(description.encode('utf8')).digest()
                root.find("description").text = ""
                hashXML = xml.etree.ElementTree.tostring(root, encoding="UTF-8")
                newJobHash = hashlib.sha1(hashXML).digest()
                newJobConfig = {
                    'hash' : newJobHash,
                    'scheduledHash' : newJobHash,
                    'descrHash' : newDescrHash,
                    'enabled' : True,
                }
            except xml.etree.ElementTree.ParseError as e:
                raise BuildError("Cannot parse XML of job '{}': {}".format(
                    name, str(e)))

            # configure or create job
            if name in existingJobs:
                # skip job if completely unchanged
                if oldJobConfig == newJobConfig:
                    printInfo(name, "Unchanged. Skipping...")
                    continue

                # updated config.xml?
                if oldJobConfig.get('hash') != newJobConfig['hash']:
                    printNormal(name, "Set new configuration...")
                    connection.updateConfig(name, jobXML)
                    oldJobConfig['hash'] = newJobConfig['hash']
                    oldJobConfig['descrHash'] = newJobConfig['descrHash']
                    BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                elif oldJobConfig.get('descrHash') != newJobConfig['descrHash']:
                    # just set description
                    printInfo(name, "Update description...")
                    connection.setDescription(name, description)
                    oldJobConfig['descrHash'] = newJobConfig['descrHash']
                    BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                else:
                    printDebug(name, "Not reconfigured. Unchanged configuration.")
            else:
                printNormal(name, "Initial creation...")
                oldJobConfig = {
                    'hash' : newJobConfig['hash'],
                    'descrHash' : newJobConfig['descrHash'],
                    'enabled' : True
                }
                if not connection.createJob(name, jobXML):
                    if args.force:
                        connection.updateConfig(name, jobXML)
                    else:
                        raise BuildError("Error creating '{}': already exists"
                            .format(name))
                BobState().addJenkinsJob(args.name, name, oldJobConfig)
            updatedJobs[name] = (oldJobConfig, newJobConfig)

        # process obsolete jobs
        for name in BobState().getJenkinsAllJobs(args.name) - set(jobs.keys()):
            if keep:
                oldJobConfig = BobState().getJenkinsJobConfig(args.name, name)
                if oldJobConfig.get('enabled', True):
                    # disable obsolete jobs
                    printNormal(name, "Disabling job...")
                    connection.disableJob(name)
                    oldJobConfig['enabled'] = False
                    BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                else:
                    printDebug(name, "Already disabled.")
            else:
                # delete obsolete jobs
                printNormal(name, "Delete job...")
                connection.deleteJob(name)
                BobState().delJenkinsJob(args.name, name)

        # enable previously disabled jobs in root-to-leaf order
        for name in reversed([ j for j in buildOrder if j in updatedJobs ]):
            oldJobConfig = updatedJobs[name][0]
            if oldJobConfig.get('enabled', True): continue # already enabled
            printNormal(name, "Enabling job...")
            connection.enableJob(name)
            oldJobConfig['enabled'] = True
            BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)

        # trigger changed jobs them in leaf-to-root order
        if not args.no_trigger:
            printNormal(None, "Schedule all modified/created jobs...")
            for name in [ j for j in buildOrder if j in updatedJobs ]:
                (oldJobConfig, newJobConfig) = updatedJobs[name]
                if oldJobConfig.get('scheduledHash') == newJobConfig['scheduledHash']:
                    printDebug(name, "Not scheduled. Last triggered with same configuation.")
                    continue # no need to reschedule
                printInfo(name, "Scheduling...")
                connection.schedule(name)
                BobState().setJenkinsJobConfig(args.name, name, newJobConfig)

def doJenkinsSetUrl(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins set-url")
    parser.add_argument("name", help="Jenkins server alias")
    parser.add_argument("url", help="New URL")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    url = urllib.parse.urlparse(args.url)
    urlPath = url.path
    if not urlPath.endswith("/"): urlPath = urlPath + "/"

    config = BobState().getJenkinsConfig(args.name)
    config["url"] = {
        "scheme" : url.scheme,
        "server" : url.hostname,
        "port" : url.port,
        "path" : urlPath,
        "username" : url.username,
        "password" : url.password,
    }
    BobState().setJenkinsConfig(args.name, config)

def doJenkinsSetOptions(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins set-options")
    parser.add_argument("name", help="Jenkins server alias")
    parser.add_argument("--reset", action='store_true', default=False,
                        help="Reset all options to their default")
    parser.add_argument("-n", "--nodes", help="Set label for Jenkins Slave")
    parser.add_argument("-o", default=[], action='append', dest='options',
                        help="Set extended Jenkins options")
    parser.add_argument("-p", "--prefix", help="Set prefix for jobs")
    parser.add_argument("--add-root", default=[], action='append',
                        help="Add new root package")
    parser.add_argument("--del-root", default=[], action='append',
                        help="Remove existing root package")
    parser.add_argument('-D', default=[], action='append', dest="defines",
                        help="Override default environment variable")
    parser.add_argument('-U', default=[], action='append', dest="undefines",
                        help="Undefine environment variable override")
    parser.add_argument("--credentials", help="Credentials UUID for SCM checkouts")
    parser.add_argument('--authtoken', help='AuthToken for remote triggering jobs')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--keep', action='store_true', default=None,
        help="Keep obsolete jobs by disabling them")
    group.add_argument('--no-keep', action='store_false', dest='keep',
        help="Delete obsolete jobs")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--download', action='store_true', default=None,
        help="Enable binary archive download")
    group.add_argument('--no-download', action='store_false', dest='download',
        help="Disable binary archive download")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--upload', action='store_true', default=None,
        help="Enable binary archive upload")
    group.add_argument('--no-upload', action='store_false', dest='upload',
        help="Disable binary archive upload")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=None,
        help="Enable sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--clean', action='store_true', default=None,
        help="Do clean builds (clear workspace)")
    group.add_argument('--incremental', action='store_false', dest='clean',
        help="Reuse workspace for incremental builds")
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

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)
    config = BobState().getJenkinsConfig(args.name)

    if args.reset:
        config.update({
            "roots" : [],
            "prefix" : "",
            "nodes" : "",
            "defines" : {},
            "download" : False,
            "upload" : False,
            "sandbox" : True,
            "windows" : False,
            "credentials" : None,
            "clean" : False,
            "keep" : False,
            "options" : {},
            "authtoken": None,
        })

    if args.nodes is not None:
        config["nodes"] = args.nodes
    if args.prefix is not None:
        config["prefix"] = args.prefix
    if args.add_root:
        config["roots"].extend(args.add_root)
    for r in args.del_root:
        try:
            config["roots"].remove(r)
        except ValueError:
            print("Cannot remove root '{}': not found".format(r), file=sys.stderr)
    if args.download is not None:
        config["download"] = args.download
    if args.upload is not None:
        config["upload"] = args.upload
    if args.sandbox is not None:
        config["sandbox"] = args.sandbox
    if defines:
        config["defines"].update(defines)
    for d in args.undefines:
        try:
            del config["defines"][d]
        except KeyError:
            print("Cannot undefine '{}': not defined".format(d), file=sys.stderr)
    if args.credentials is not None:
        config['credentials'] = args.credentials
    if args.authtoken is not None:
        config['authtoken'] = args.authtoken
    if args.clean is not None:
        config['clean'] = args.clean
    if args.keep is not None:
        config['keep'] = args.keep
    options = config.setdefault('options', {})
    for i in args.options:
        (opt, sep, val) = i.partition("=")
        if sep != "=":
            parser.error("Malformed plugin option: "+i)
        if val == "":
            if opt in options: del options[opt]
        else:
            options[opt] = val

    BobState().setJenkinsConfig(args.name, config)

availableJenkinsCmds = {
    "add"        : (doJenkinsAdd, "[-p <prefix>] [-r <package>] NAME URL"),
    "export"  : (doJenkinsExport, "NAME DIR"),
    "graph"  : (doJenkinsGraph, "NAME"),
    "ls"         : (doJenkinsLs, "[-v]"),
    "prune"  : (doJenkinsPrune, "NAME"),
    "push"   : (doJenkinsPush, "NAME"),
    "rm"         : (doJenkinsRm, "[-f] NAME"),
    "set-url" : (doJenkinsSetUrl, "NAME URL"),
    "set-options" : (doJenkinsSetOptions, "NAME [--{add,del}-root <package>] ...")
}

def doJenkins(argv, bobRoot):
    subHelp = "\n             ... ".join(sorted(
        [ "{} {}".format(c, d[1]) for (c, d) in availableJenkinsCmds.items() ]))
    parser = argparse.ArgumentParser(prog="bob jenkins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""Configure jenkins. The following subcommands are available:

  bob jenkins {}
""".format(subHelp))
    parser.add_argument('subcommand', help="Subcommand")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for subcommand")
    parser.add_argument('-c', dest="configFile", default=[], action='append', metavar="NAME",
        help="Use additional config File.")

    args = parser.parse_args(argv)

    recipes = RecipeSet()
    recipes.defineHook('jenkinsNameFormatter', jenkinsNameFormatter)
    recipes.setConfigFiles(args.configFile)
    recipes.parse()

    if args.subcommand in availableJenkinsCmds:
        BobState().setAsynchronous()
        try:
            availableJenkinsCmds[args.subcommand][0](recipes, args.args)
        except http.client.HTTPException as e:
            raise BuildError("HTTP error: " + str(e))
        except OSError as e:
            raise BuildError("OS error: " + str(e))
        finally:
            BobState().setSynchronous()
    else:
        parser.error("Unknown subcommand '{}'".format(args.subcommand))

