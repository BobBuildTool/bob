# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ... import BOB_VERSION, BOB_INPUT_HASH
from ...archive import getArchiver, JenkinsArchive
from ...errors import ParseError, BuildError
from ...input import RecipeSet
from ...languages import StepSpec
from ...state import BobState, JenkinsConfig
from ...tty import WarnOnce
from ...utils import processDefines, runInEventLoop, sslNoVerifyContext, quoteCmdExe, \
    getPlatformString
from .intermediate import getJenkinsVariantId, PartialIR
from pathlib import PurePosixPath
from shlex import quote
import argparse
import ast
import base64
import datetime
import getpass
import hashlib
import http.cookiejar
import json
import lzma
import os.path
import random
import re
import ssl
import sys
import urllib
import urllib.parse
import urllib.request
import urllib.response
import xml.etree.ElementTree

warnCertificate = WarnOnce("Using HTTPS without certificate check.")
warnNonRelocatable = WarnOnce("Non-relocatable package used outside of a sandbox. Your build may fail!",
    help="Jenkins builds need to copy dependenies between workspaces. At least one package does not seem to support this!")

requiredPlugins = {
    "conditional-buildstep" : "Conditional BuildStep",
    "copyartifact" : "Copy Artifact Plugin",
    "git" : "Jenkins Git plugin",
    "multiple-scms" : "Jenkins Multiple SCMs plugin",
    "subversion" : "Jenkins Subversion Plug-in",
    "ws-cleanup" : "Jenkins Workspace Cleanup Plugin",
}

class Wiper:
    """Remove the [audit] and [recipes-audit] sections from specs"""
    def __init__(self):
        self.keep = True
 
    def check(self, l):
        if l in ("[audit]", "[recipes-audit]"):
            self.keep = False
        elif l.startswith('[') and l.endswith(']'):
            self.keep = True

        return self.keep

def cleanJobConfig(tree):
    """Remove audit related information from shell steps"""
    for node in tree.findall(".//hudson.tasks.Shell/command"):
        lines = node.text.splitlines()
        mrproper = Wiper()
        node.text = "\n".join(l for l in lines if mrproper.check(l))

def genUuid():
    ret = "".join(random.sample("0123456789abcdef", 8))
    return ret[:4] + '-' + ret[4:]

class JenkinsJob:
    def __init__(self, name, displayName, nameCalculator, recipe, upload, download):
        self.__name = name
        self.__displayName = displayName
        self.__nameCalculator = nameCalculator
        self.__recipe = recipe
        self.__isRoot = False
        self.__upload = upload
        self.__download = download
        self.__checkoutSteps = {}
        self.__buildSteps = {}
        self.__packageSteps = {}
        self.__steps = set()
        self.__deps = {}
        self.__namesPerVariant = {}
        self.__packagesPerVariant = {}
        self.__downstreamJobs = set()
        self.__partialGraph = PartialIR()

    def __getJobName(self, step):
        return self.__nameCalculator.getJobInternalName(step)

    def getName(self):
        return self.__name

    def isRoot(self):
        return self.__isRoot

    def makeRoot(self):
        self.__isRoot = True

    def getDescription(self, date, warnLazy):
        description = []
        if warnLazy:
            description.extend([
                "<em><h2>Warning: lazily updated!</h2></em>",
                "<p>The description of the jobs is updated lazily by Bob. It remains unchanged unless the job must be updated. Changes of the recipes that do not affect this job won't be reflected in the description.</p>",
            ])
        description.extend([
            "<h2>Recipe</h2>",
            "<p>Name: " + self.__recipe.getName()
                + "<br/>Source: " + runInEventLoop(self.__recipe.getRecipeSet().getScmStatus())
                + "<br/>Configured: " + date
                + "<br/>Bob version: " + BOB_VERSION + "</p>",
            "<h2>Packages</h2>", "<ul>"
        ])
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
        vid = getJenkinsVariantId(step)
        if step.isCheckoutStep():
            self.__checkoutSteps.setdefault(vid, step)
        elif step.isBuildStep():
            self.__buildSteps.setdefault(vid, step)
        else:
            assert step.isPackageStep()
            self.__partialGraph.add(step)
            package = step.getPackage()
            self.__packageSteps.setdefault(vid, step)
            self.__namesPerVariant.setdefault(vid, set()).add(package.getName())
            self.__packagesPerVariant.setdefault(vid, set()).add("/".join(package.getStack()))

        if vid not in self.__steps:
            # filter dependencies that are built in this job
            self.__steps.add(vid)
            if vid in self.__deps: del self.__deps[vid]

            # add dependencies unless they are built by this job or invalid
            for dep in step.getAllDepSteps(True):
                if not dep.isValid(): continue
                vid = getJenkinsVariantId(dep)
                if vid in self.__steps: continue
                self.__deps.setdefault(vid, dep)

    def addDownstreamJob(self, job):
        self.__downstreamJobs.add(job)

    def getCheckoutSteps(self):
        return self.__checkoutSteps.values()

    def getBuildSteps(self):
        return self.__buildSteps.values()

    def getPackageSteps(self):
        return self.__packageSteps.values()

    def getUpstreamJobs(self):
        deps = set()
        for d in self.__deps.values():
            deps.add(self.__getJobName(d))
        return deps

    def dumpJobSpec(self):
        spec = self.__partialGraph.toData()
        spec = json.dumps(self.__partialGraph.toData(), sort_keys=True).encode('ascii')
        spec = lzma.compress(spec)
        spec = base64.a85encode(spec, wrapcol=120).decode('ascii')
        return spec

    def __copyArtifact(self, builders, policy, project, artifact, sharedDir=None,
                       condition=None, windows=False):
        if condition is None:
            cp = xml.etree.ElementTree.SubElement(
                builders, "hudson.plugins.copyartifact.CopyArtifact", attrib={
                    "plugin" : "copyartifact@1.32.1"
                })
        else:
            cmd = "bob _jexec --version " + BOB_INPUT_HASH.hex() +  " check-shared "
            if windows:
                cmd += quoteCmdExe(sharedDir) + " " + quoteCmdExe(condition)
            else:
                cmd += quote(sharedDir) + " " + quote(condition)

            guard = xml.etree.ElementTree.SubElement(
                builders, "org.jenkinsci.plugins.conditionalbuildstep.singlestep.SingleConditionalBuilder", attrib={
                    "plugin" : "conditional-buildstep@1.3.3",
                })
            shellCond = xml.etree.ElementTree.SubElement(
                guard, "condition", attrib={
                    "class" : ("org.jenkins_ci.plugins.run_condition.contributed.BatchFileCondition"
                               if windows else "org.jenkins_ci.plugins.run_condition.contributed.ShellCondition")
                })
            xml.etree.ElementTree.SubElement(shellCond, "command").text = cmd
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

        xml.etree.ElementTree.SubElement(cp, "project").text = project
        xml.etree.ElementTree.SubElement(cp, "filter").text = artifact
        xml.etree.ElementTree.SubElement(cp, "target").text = ""
        xml.etree.ElementTree.SubElement(cp, "excludes").text = ""
        if policy in ["stable", "unstable"]:
            selector = xml.etree.ElementTree.SubElement(
                cp, "selector", attrib={
                    "class" : "hudson.plugins.copyartifact.StatusBuildSelector"
                })
            if policy == "stable":
                xml.etree.ElementTree.SubElement(selector, "stable").text = "true"
        else:
            xml.etree.ElementTree.SubElement(
                cp, "selector", attrib={
                    "class" : "hudson.plugins.copyartifact.LastCompletedBuildSelector"
                })
        xml.etree.ElementTree.SubElement(
            cp, "doNotFingerprintArtifacts").text = "true"

    def dumpXML(self, orig, config, date, recipesAudit=None):
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
                if not config.authtoken:
                    xml.etree.ElementTree.remove(auth)
                else:
                    auth.clear()
            else:
                if config.authtoken:
                    xml.etree.ElementTree.SubElement(root, "authToken")
        else:
            root = xml.etree.ElementTree.Element("project")
            xml.etree.ElementTree.SubElement(root, "actions")
            xml.etree.ElementTree.SubElement(root, "description")
            if self.__name != self.__displayName:
                xml.etree.ElementTree.SubElement(
                    root, "displayName").text = self.__displayName
            xml.etree.ElementTree.SubElement(root, "keepDependencies").text = "false"
            xml.etree.ElementTree.SubElement(root, "properties")
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
            if config.authtoken:
                auth = xml.etree.ElementTree.SubElement(root, "authToken")
            else:
                auth = None

        root.find("description").text = self.getDescription(date, config.jobsUpdate == "lazy")
        scmTrigger = xml.etree.ElementTree.SubElement(
            triggers, "hudson.triggers.SCMTrigger")
        xml.etree.ElementTree.SubElement(scmTrigger, "spec").text = config.scmPoll
        xml.etree.ElementTree.SubElement(
            scmTrigger, "ignorePostCommitHooks").text = ("true" if config.scmIgnoreHooks else "false")

        numToKeep = config.getGcNum(self.__isRoot, "builds")
        artifactNumToKeep = config.getGcNum(self.__isRoot, "artifacts")
        discard = root.find("./properties/jenkins.model.BuildDiscarderProperty/strategy[@class='hudson.tasks.LogRotator']")
        if numToKeep != "-1" or artifactNumToKeep != "-1":
            if discard is None:
                discard = xml.etree.ElementTree.fromstring("""
                      <strategy class="hudson.tasks.LogRotator">
                        <daysToKeep>-1</daysToKeep>
                        <numToKeep>-1</numToKeep>
                        <artifactDaysToKeep>-1</artifactDaysToKeep>
                        <artifactNumToKeep>1</artifactNumToKeep>
                      </strategy>""")
                xml.etree.ElementTree.SubElement(
                    root.find("properties"),
                    "jenkins.model.BuildDiscarderProperty").append(discard)
            discard.find("numToKeep").text = numToKeep
            discard.find("artifactNumToKeep").text = artifactNumToKeep
        elif discard is not None:
            properties = root.find("properties")
            properties.remove(properties.find("jenkins.model.BuildDiscarderProperty"))

        copyartifact = root.find("./properties/hudson.plugins.copyartifact.CopyArtifactPermissionProperty/projectNameList")
        if self.__downstreamJobs:
            if copyartifact is None:
                copyartifact = xml.etree.ElementTree.SubElement(
                    xml.etree.ElementTree.SubElement(
                        root.find("properties"),
                        "hudson.plugins.copyartifact.CopyArtifactPermissionProperty"),
                    "projectNameList")
            copyartifact.clear()
            for d in sorted(self.__downstreamJobs):
                xml.etree.ElementTree.SubElement(copyartifact, "string").text = d
        elif copyartifact is not None:
            properties = root.find("properties")
            properties.remove(properties.find("hudson.plugins.copyartifact.CopyArtifactPermissionProperty"))

        if config.nodes:
            assignedNode = root.find("assignedNode")
            if assignedNode is None:
                assignedNode = xml.etree.ElementTree.SubElement(root, "assignedNode")
            assignedNode.text = config.nodes
            root.find("canRoam").text = "false"
        else:
            root.find("canRoam").text = "true"

        deps = sorted(self.__deps.values())
        if deps:
            policy = config.jobsPolicy
            revBuild = xml.etree.ElementTree.SubElement(
                triggers, "jenkins.triggers.ReverseBuildTrigger")
            xml.etree.ElementTree.SubElement(revBuild, "spec").text = ""
            xml.etree.ElementTree.SubElement(
                revBuild, "upstreamProjects").text = ", ".join(
                    [ self.__getJobName(d) for d in deps ])
            threshold = xml.etree.ElementTree.SubElement(revBuild, "threshold")
            if policy == "stable":
                xml.etree.ElementTree.SubElement(threshold, "name").text = "SUCCESS"
                xml.etree.ElementTree.SubElement(threshold, "ordinal").text = "0"
                xml.etree.ElementTree.SubElement(threshold, "color").text = "BLUE"
            elif policy == "unstable":
                xml.etree.ElementTree.SubElement(threshold, "name").text = "UNSTABLE"
                xml.etree.ElementTree.SubElement(threshold, "ordinal").text = "1"
                xml.etree.ElementTree.SubElement(threshold, "color").text = "YELLOW"
            elif policy == "always":
                xml.etree.ElementTree.SubElement(threshold, "name").text = "FAILURE"
                xml.etree.ElementTree.SubElement(threshold, "ordinal").text = "2"
                xml.etree.ElementTree.SubElement(threshold, "color").text = "RED"
            else:
                raise ParseError("Invalid value of extended option jobs.policy: " + policy)
            xml.etree.ElementTree.SubElement(threshold, "completeBuild").text = "true"

            # copy deps into workspace
            for d in deps:
                if not d.isRelocatable() and (d.getSandbox() is None):
                    warnNonRelocatable.warn(d.getPackage().getName())
                # always copy build-id
                self.__copyArtifact(builders, policy, self.__getJobName(d),
                    JenkinsArchive.buildIdName(d))
                # Copy artifact only if we rely on Jenkins directly. Otherwise
                # the regular up-/download will take care of the transfer.
                if config.artifactsCopy == "jenkins":
                    self.__copyArtifact(builders, policy, self.__getJobName(d),
                        JenkinsArchive.tgzName(d), config.sharedDir,
                        JenkinsArchive.buildIdName(d) if d.isShared() else None,
                        config.hostPlatform in ("msys", "win32"))

        # checkout SCMs supported by Jenkins
        checkoutSCMs = []
        for d in sorted(self.__checkoutSteps.values()):
            checkoutSCMs.extend(d.getJenkinsXml(config))

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

        # publish built artifacts
        publish = []
        for d in sorted(self.__packageSteps.values()):
            if config.artifactsCopy == "jenkins":
                publish.append(JenkinsArchive.tgzName(d))
            publish.append(JenkinsArchive.buildIdName(d))

        xml.etree.ElementTree.SubElement(
            archiver, "artifacts").text = ",".join(publish)
        xml.etree.ElementTree.SubElement(
            archiver, "allowEmptyArchive").text = "false"

        # Dump partial graph and synthesize execute command
        if config.hostPlatform in ("msys", "win32"):
            cmdTemplate = "#!cmd /c bob {}"
        else:
            cmdTemplate = "#!bob {}"

        execCmds = [ cmdTemplate.format("_jexec --version "
                                        + BOB_INPUT_HASH.hex() + " run") ]
        execCmds.extend([
            "[cfg]",
            "platform=" + config.hostPlatform,
            "download=" + ("1" if self.__download else "0"),
            "upload=" + ("1" if self.__upload else "0"),
            "copy=" + config.artifactsCopy,
            "share=" + config.sharedDir,
        ])
        if config.sharedQuota:
            execCmds.append("quota=" + config.sharedQuota)
        if recipesAudit:
            execCmds.extend(["[recipes-audit]", recipesAudit])
        auditMeta = config.getAuditMeta()
        if auditMeta:
            execCmds.append("[audit]")
            execCmds.extend("{}={}".format(k, v) for k, v in auditMeta.items())
        execCmds.extend(["[exec]", self.dumpJobSpec()])
        execute = xml.etree.ElementTree.SubElement(
            builders, "hudson.tasks.Shell")
        xml.etree.ElementTree.SubElement(execute, "command").text = "\n".join(execCmds)

        # clean build wrapper
        preBuildClean = buildWrappers.find("hudson.plugins.ws__cleanup.PreBuildCleanup")
        if preBuildClean is not None: buildWrappers.remove(preBuildClean)
        if config.clean:
            preBuildClean = xml.etree.ElementTree.SubElement(buildWrappers,
                "hudson.plugins.ws__cleanup.PreBuildCleanup",
                attrib={"plugin" : "ws-cleanup@0.30"})
            xml.etree.ElementTree.SubElement(preBuildClean, "deleteDirs").text = "true"
            xml.etree.ElementTree.SubElement(preBuildClean, "cleanupParameter")
            xml.etree.ElementTree.SubElement(preBuildClean, "externalDelete")

        # add authtoken if set in options
        if auth:
            auth.text = config.authtoken

        return xml.etree.ElementTree.tostring(root, encoding="UTF-8")


    def dumpGraph(self, done):
        for d in self.__deps.values():
            depName = self.__getJobName(d)
            key = (self.__name, depName)
            if key not in done:
                print(" \"{}\" -> \"{}\";".format(self.__name, depName))
                done.add(key)


class AbstractJob:
    __slots__ = ['pkgs', 'parents', 'childs']
    def __init__(self, pkgs=[], parents=[]):
        self.pkgs = set(pkgs)
        self.parents = set(parents)
        self.childs = set()

class JobNameCalculator:
    """Utility class to calculate job names for packages.

    By default the name of the recipe is used for the job name. Depending on
    the package structure this may lead to cyclic job dependencies. If such
    cycles are found the longes common prefix of the  package names is used for
    affected jobs. If this still leads to cycles an incrementing suffix is
    added to the affected job names.
    """

    def __init__(self, prefix):
        self.__regexJobName = re.compile(r'[^a-zA-Z0-9-_]', re.DOTALL)
        self.__prefix = prefix
        self.__isolate = lambda name: False
        self.__packageName = {}
        self.__roots = []

    def addPackage(self, package):
        self.__roots.append(package.getPackageStep())

    def isolate(self, regex):
        """Isolate matching packages into separate jobs.

        Any package that is matched is put into a dedicated job based on the
        package name. Multiple variants of the same package are still kept in
        the same job, though.
        """
        if regex:
            r = re.compile(regex)
            self.__isolate = lambda name, r=r: r.search(name) is not None

    def sanitize(self):
        """Calculate job names and make sure jobs are not cyclic.

        First span the whole graph and put every distinct package into a
        separate job. The job name is usually the recipe name unless the
        package is matched by the "isolate" regex in which case the package
        name is used. This leads to many jobs sharing the same name.

        As the first optimization as many jobs as possible are merged that
        share the same name. A merge may not be possible if this would make the
        resulting graph cyclic. This can be easily tested by comparing the sets
        of reachable jobs of the candidates.

        If multiple jobs still share the same name the algorithm goes on to
        calculate new names. They are based on the longest common prefix of all
        package names in such jobs. This usually gives unique names based on
        the mulitPackages in the affected recipes.

        If there are still jobs that share the same name a countig number is
        added as suffix.
        """
        vidToJob = {}   # variant-id -> AbstractJob
        vidToName = {}  # variant-id -> package-name
        nameToJobs = {} # (job)name -> [ AbstractJob ]

        # Helper function that recursively adds the packages to the graph.
        # Recursion stops at already known packages.
        def addStep(step, parentJob):
            sbxVariantId = getJenkinsVariantId(step)
            job = vidToJob.get(sbxVariantId)
            if job is None:
                if step.isPackageStep():
                    pkg = step.getPackage()
                    pkgName = pkg.getName()
                    job = AbstractJob([sbxVariantId], parentJob.pkgs)
                    vidToJob[sbxVariantId] = job
                    vidToName[sbxVariantId] = pkgName
                    name = pkgName if self.__isolate(pkgName) else pkg.getRecipe().getName()
                    nameToJobs.setdefault(name, []).append(job)
                else:
                    job = parentJob

                # recurse on dependencies
                for d in step.getAllDepSteps(True):
                    job.childs |= addStep(d, job)
            else:
                job.parents |= parentJob.pkgs

            return job.pkgs | job.childs

        # Start recursing from the roots and fill the graph. The resulting
        # graph maps as many jobs to the same name as there are variants of the
        # same recipe.
        for r in self.__roots:
            addStep(r, AbstractJob())

        # Helper function to amend childs on parents if jobs are collapsed.
        def addChilds(pkgs, childs):
            for i in pkgs:
                j = vidToJob[i]
                if not childs.issubset(j.childs):
                    j.childs |= childs
                    addChilds(j.parents, childs)

        # Try to collapse jobs with same name. The greedy algorithm collapses
        # all jobs that are not fully reachable wrt. each other. IOW all jobs
        # are merged that do not provoke a cycle.
        for name in sorted(nameToJobs.keys()):
            todo = nameToJobs[name]
            jobs = []
            while todo:
                i = todo.pop(0)
                remaining = todo
                todo = []
                for j in remaining:
                    if (i.childs >= (j.pkgs|j.childs)) or (j.childs >= (i.pkgs|i.childs)):
                        todo.append(j)
                    else:
                        i.parents |= j.parents
                        i.pkgs |= j.pkgs
                        i.childs |= j.childs
                        addChilds(i.parents, i.pkgs|i.childs)
                        for k in j.pkgs: vidToJob[k] = i
                jobs.append(i)
            nameToJobs[name] = jobs

        # Helper function to find longest prefix of a number of packages. If
        # there is more than one package then all names will be split at the
        # dash. Then we iterate every step as tuple in parallel as long as all
        # elements of the tuple are the same.
        def longestPrefix(pkgs):
            if len(pkgs) == 1:
                [vid] = pkgs # unpack this way because 'pkgs' is a set()
                return vidToName[vid]
            else:
                common = []
                for step in zip(*(vidToName[p].split('-') for p in pkgs)):
                    if len(set(step)) == 1:
                        common.append(step[0])
                    else:
                        break
                return "-".join(common)

        # If multiple jobs for the same name remain we try to find the longest
        # common prefix (until a dash) from the packages of each job. This
        # hopefully gives a unique name for each job.
        finalNames = {}
        for (name, jobs) in sorted(nameToJobs.items()):
            if len(jobs) > 1:
                for j in jobs:
                    finalNames.setdefault(longestPrefix(j.pkgs), []).append(j)
            else:
                finalNames.setdefault(name, []).extend(jobs)

        # Create unique job names for all jobs by adding a counting number as
        # last resort.
        for (name, jobs) in sorted(finalNames.items()):
            if len(jobs) == 1:
                for vid in jobs[0].pkgs:
                    self.__packageName[vid] = name
            else:
                for i, j in zip(range(len(jobs)), jobs):
                    for vid in j.pkgs:
                        self.__packageName[vid] = "{}-{}".format(name, i+1)

    def getJobDisplayName(self, step):
        if step.isPackageStep():
            vid = getJenkinsVariantId(step)
        else:
            vid = getJenkinsVariantId(step.getPackage().getPackageStep())
        return self.__prefix + self.__packageName[vid]

    def getJobInternalName(self, step):
        return self.__regexJobName.sub('_', self.getJobDisplayName(step)).lower()


def _genJenkinsJobs(step, jobs, nameCalculator, upload, download, seenPackages, allVariantIds,
                    shortdescription):

    if step.isPackageStep() and shortdescription:
        if getJenkinsVariantId(step) in allVariantIds:
            name = nameCalculator.getJobInternalName(step)
            return jobs[name]
        else:
            allVariantIds.add(getJenkinsVariantId(step))

    name = nameCalculator.getJobInternalName(step)
    if name in jobs:
        jj = jobs[name]
    else:
        recipe = step.getPackage().getRecipe()
        jj = JenkinsJob(name, nameCalculator.getJobDisplayName(step), nameCalculator,
                        recipe, upload, download)
        jobs[name] = jj

    # add step to job
    jj.addStep(step)

    # always recurse on arguments
    for d in sorted(step.getArguments(), key=lambda d: d.getPackage().getName()):
        if d.isValid(): _genJenkinsJobs(d, jobs, nameCalculator, upload, download,
                                            seenPackages, allVariantIds, shortdescription)

    # Recurse on tools and sandbox only for package steps. Also do an early
    # reject if the particular package stack was already seen. This is safe as
    # the same package stack cannot have different variant-ids.
    if step.isPackageStep():
        for (name, tool) in sorted(step.getTools().items()):
            toolStep = tool.getStep()
            stack = "/".join(toolStep.getPackage().getStack())
            if stack not in seenPackages:
                seenPackages.add(stack)
                _genJenkinsJobs(toolStep, jobs, nameCalculator, upload, download,
                                seenPackages, allVariantIds, shortdescription)

        sandbox = step.getSandbox(True)
        if sandbox is not None:
            sandboxStep = sandbox.getStep()
            stack = "/".join(sandboxStep.getPackage().getStack())
            if stack not in seenPackages:
                seenPackages.add(stack)
                _genJenkinsJobs(sandboxStep, jobs, nameCalculator, upload, download,
                                seenPackages, allVariantIds, shortdescription)

    return jj

def jenkinsNameFormatter(step, props):
    return step.getPackage().getName().replace('::', "/") + "/" + step.getLabel()

def jenkinsNamePersister(jenkins, wrapFmt, uuid):

    def persist(step, props):
        # We must never mix checkout steps and other steps. The checkout step
        # workspace state is fundamentally different. If a checkoutStep and a
        # packageStep have the same variant-id things will go south...
        digest = getJenkinsVariantId(step)
        digest += b'\1' if step.isCheckoutStep() else b'\0'
        ret = BobState().getJenkinsByNameDirectory(
            jenkins, wrapFmt(step, props), digest)
        if uuid: ret = ret + "-" + uuid
        ret += "/workspace"
        return ret

    return persist

def genJenkinsJobs(recipes, jenkins):
    jobs = {}
    config = BobState().getJenkinsConfig(jenkins)
    recipes.parse(config.defines, config.hostPlatform)

    if config.artifactsCopy == "archive":
        archiveHandler = getArchiver(recipes)
        archiveHandler.wantUploadJenkins(config.upload)
        archiveHandler.wantDownloadJenkins(config.download)
        if not archiveHandler.canUpload() or not archiveHandler.canDownload():
            raise ParseError("No archive for up and download found but artifacts.copy using archive enabled!")
    nameFormatter = recipes.getHook('jenkinsNameFormatter')

    packages = recipes.generatePackages(
        jenkinsNamePersister(jenkins, nameFormatter, config.uuid),
        config.sandbox)
    nameCalculator = JobNameCalculator(config.prefix)
    rootPackages = []
    for r in config.roots: rootPackages.extend(packages.queryPackagePath(r))
    for root in rootPackages:
        nameCalculator.addPackage(root)
    nameCalculator.isolate(config.jobsIsolate)
    nameCalculator.sanitize()
    for root in sorted(rootPackages, key=lambda root: root.getName()):
        rootJenkinsJob = _genJenkinsJobs(root.getPackageStep(), jobs, nameCalculator,
                config.upload, config.download, set(), set(), config.shortdescription)
        rootJenkinsJob.makeRoot()

    packages.close()

    # Add reverse dependencies
    for (name, job) in jobs.items():
        for dep in job.getUpstreamJobs():
            jobs[dep].addDownstreamJob(name)

    return jobs

def genJenkinsBuildOrder(jobs):
    def visit(j, pending, processing, order, stack):
        if j in processing:
            raise ParseError("Jobs are cyclic: " + " -> ".join(stack))
        if j in pending:
            processing.add(j)
            for d in jobs[j].getUpstreamJobs():
                visit(d, pending, processing, order, stack + [d])
            pending.remove(j)
            processing.remove(j)
            order.append(j)

    order = []
    pending = set(jobs.keys())
    processing = set()
    while pending:
        j = pending.pop()
        pending.add(j)
        visit(j, pending, processing, order, [j])

    return order

def doJenkinsAdd(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins add")
    parser.add_argument("-n", "--nodes", default="", help="Label for Jenkins Slave")
    parser.add_argument("-o", default=[], action='append', dest='options',
                        help="Set extended Jenkins options")
    parser.add_argument("--host-platform", default=getPlatformString(),
            choices=["linux", "msys", "win32"],
            help="Jenkins host platform type. (default: current platform)")
    parser.add_argument("-w", "--windows", action='store_const', const='msys',
                        dest='host_platform', help="Jenkins is running on Windows.")
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--shortdescription', action='store_true', default=None,
                        help='Don\'t calculate all paths for description')
    group.add_argument('--longdescription', action='store_false', dest='shortdescription',
                        help='Calculate all paths for description')
    args = parser.parse_args(argv)

    if args.name in BobState().getAllJenkins():
        print("Jenkins '{}' already added.".format(args.name), file=sys.stderr)
        sys.exit(1)

    config = JenkinsConfig(args.url, genUuid())
    config.hostPlatform = args.host_platform
    config.roots = args.root
    config.prefix = args.prefix
    config.nodes = args.nodes
    config.defines = processDefines(args.defines)
    config.download = args.download
    config.upload = args.upload
    config.sandbox = args.sandbox
    config.credentials = args.credentials
    config.clean = args.clean
    config.keep = args.keep
    config.shortdescription = args.shortdescription
    for i in args.options:
        (opt, sep, val) = i.partition("=")
        if sep != "=":
            parser.error("Malformed extended option: "+i)
        if val != "":
            config.setOption(opt, val, parser.error)

    if config.artifactsCopy == "archive":
        if not config.upload:
            parser.error("Archive sharing can not be used without upload enabled! Exiting..")
        if not config.download:
            parser.error("Archive sharing can not be used without download enabled! Exiting..")

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

    jobs = genJenkinsJobs(recipes, args.name)
    buildOrder = genJenkinsBuildOrder(jobs)
    config = BobState().getJenkinsConfig(args.name)

    jenkinsJobCreate = recipes.getHookStack('jenkinsJobCreate')
    for j in buildOrder:
        job = jobs[j]
        info = {
            'alias' : args.name,
            'name' : job.getName(),
            'url' : config.url,
            'prefix' : config.prefix,
            'nodes' : config.nodes,
            'sandbox' : config.sandbox,
            'windows' : config.windows,
            'hostPlatform' : config.hostPlatform,
            'checkoutSteps' : job.getCheckoutSteps(),
            'buildSteps' : job.getBuildSteps(),
            'packageSteps' : job.getPackageSteps()
        }
        xml = applyHooks(jenkinsJobCreate, job.dumpXML(None, config, "now"), info)
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
            print("    URL:", cfg.url)
            print("    Roots:", ", ".join(cfg.roots))
            if cfg.prefix:
                print("    Prefix:", cfg.prefix)
            if cfg.nodes:
                print("    Nodes:", cfg.nodes)
            if cfg.defines:
                print("    Defines:", ", ".join([ k+"="+v for (k,v) in cfg.defines.items() ]))
            print("    Obsolete jobs:", "keep" if cfg.keep else "delete")
            print("    Download:", "enabled" if cfg.download else "disabled")
            print("    Upload:", "enabled" if cfg.upload else "disabled")
            print("    Clean builds:", "enabled" if cfg.clean else "disabled")
            print("    Sandbox:", "enabled" if cfg.sandbox else "disabled")
            if cfg.credentials:
                print("    Credentials:", cfg.credentials)
            options = cfg.getOptions()
            if options:
                print("    Extended options:", ", ".join([ k+"="+v for (k,v) in options.items() ]))
            print("    Host platform:", cfg.hostPlatform)
        if args.verbose >= 2:
            print("    Jobs:", ", ".join(sorted(BobState().getJenkinsAllJobs(j))))

class JenkinsConnection:
    """Connection to a Jenkins server abstracting the REST API"""

    def __init__(self, config, sslVerify):
        self.__headers = { "Content-Type": "application/xml" }
        self.__root = config.urlWithoutCredentials

        handlers = []

        # Handle cookies
        cookies = http.cookiejar.CookieJar()
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))

        # Optionally disable SSL certificate checks
        if not sslVerify:
            handlers.append(urllib.request.HTTPSHandler(
                context=sslNoVerifyContext()))

        # handle authorization
        if config.urlUsername is not None:
            username = urllib.parse.unquote(config.urlUsername)
            passwd = config.urlPassword
            if passwd is None:
                passwd = getpass.getpass()
            else:
                passwd = urllib.parse.unquote(passwd)
            userPass = username + ":" + passwd
            self.__headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")

        # remember basic settings
        self.__opener = urllib.request.build_opener(*handlers)

        # get CSRF token
        try:
            with self._send("GET", "crumbIssuer/api/xml") as response:
                resp = xml.etree.ElementTree.fromstring(response.read())
                crumb = resp.find("crumb").text
                field = resp.find("crumbRequestField").text
                self.__headers[field] = crumb
        except urllib.error.HTTPError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def _send(self, method, path, body=None, additionalHeaders={}):
        headers = self.__headers.copy()
        headers.update(additionalHeaders)
        req = urllib.request.Request(self.__root + path, data=body,
            method=method, headers=headers)
        return self.__opener.open(req)

    def checkPlugins(self):
        try:
            with self._send("GET", "pluginManager/api/python?depth=1") as response:
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
        except urllib.error.HTTPError as e:
            print("Warning: could not verify plugins: HTTP error: {} {}"
                    .format(e.code, e.reason),
                file=sys.stderr)
        except:
            raise BuildError("Malformed Jenkins response while checking plugins!")

    def createJob(self, name, jobXML):
        try:
            with self._send("POST", "createItem?name=" + name, jobXML):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return False
            raise BuildError("Error creating '{}': HTTP error: {} {}"
                .format(name, e.code, e.reason))

    def deleteJob(self, name):
        try:
            with self._send("POST", "job/" + name + "/doDelete"):
                pass
        except urllib.error.HTTPError as e:
            if e.code not in [302, 404]:
                raise BuildError("Error deleting '{}': HTTP error: {} {}".format(
                    name, e.code, e.reason))

    def fetchConfig(self, name):
        try:
            with self._send("GET", "job/" + name + "/config.xml") as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise BuildError("Warning: could not download '{}' job config: HTTP error: {} {}"
                    .format(name, e.code, e.reason))

    def updateConfig(self, name, jobXML):
        try:
            with self._send("POST", "job/" + name + "/config.xml", jobXML):
                pass
        except urllib.error.HTTPError as e:
            raise BuildError("Error updating '{}': HTTP error: {} {}"
                .format(name, e.code, e.reason))

    def schedule(self, name):
        ret = True
        try:
            with self._send("POST", "job/" + name + "/build"):
                pass
        except urllib.error.HTTPError as e:
            print("Error scheduling '{}': HTTP error: {} {}"
                    .format(name, e.code, e.reason),
                file=sys.stderr)
            ret = False
        return ret

    def enableJob(self, name):
        try:
            with self._send("POST", "job/" + name + "/enable"):
                pass
        except urllib.error.HTTPError as e:
            raise BuildError("Error enabling '{}': HTTP error: {} {}"
                .format(name, e.code, e.reason))

    def disableJob(self, name):
        try:
            with self._send("POST", "job/" + name + "/disable"):
                return True
        except urllib.error.HTTPError as e:
            if e.code not in [302, 404]:
                raise BuildError("Error disabling '{}': HTTP error: {} {}"
                    .format(name, e.code, e.reason))
            return e.code != 404

    def setDescription(self, name, description):
        body = urllib.parse.urlencode({"description" : description}).encode('ascii')
        headers = {
            "Content-Type" : "application/x-www-form-urlencoded",
            "Accept" : "text/plain"
        }
        try:
            with self._send("POST", "job/" + name + "/description", body, headers):
                pass
        except urllib.error.HTTPError as e:
            raise BuildError("Error setting description of '{}': HTTP error: {} {}"
                .format(name, e.code, e.reason))


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
    parser.add_argument('--no-ssl-verify', dest='ssl_verify', default=True,
        action='store_false', help="Disable SSL certificate verification.")
    parser.add_argument('--user', help="Set username for server authentication")
    parser.add_argument('--password', help="Set password for server authorization")
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

    if args.user is not None: config.urlUsername = args.user
    if args.password is not None: config.urlPassword = args.password

    # connect to server
    with JenkinsConnection(config, args.ssl_verify) as connection:
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
    parser.add_argument('--no-ssl-verify', dest='ssl_verify', default=True,
        action='store_false', help="Disable SSL certificate verification.")
    parser.add_argument("--no-trigger", action="store_true", default=False,
                        help="Do not trigger build for updated jobs")
    parser.add_argument('--user', help="Set username for server authentication")
    parser.add_argument('--password', help="Set password for server authorization")
    parser.add_argument('-q', '--quiet', default=0, action='count',
        help="Decrease verbosity (may be specified multiple times)")
    parser.add_argument('-v', '--verbose', default=0, action='count',
        help="Increase verbosity (may be specified multiple times)")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    # Make sure bob-namespace-sandbox is up-to-date
    try:
        from ..develop.make import makeSandboxHelper
        makeSandboxHelper()
    except ImportError:
        pass

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)
    jobs = genJenkinsJobs(recipes, args.name)
    buildOrder = genJenkinsBuildOrder(jobs)

    if args.user is not None: config.urlUsername = args.user
    if args.password is not None: config.urlPassword = args.password

    # get hooks
    jenkinsJobCreate = recipes.getHookStack('jenkinsJobCreate')
    jenkinsJobPreUpdate = recipes.getHookStack('jenkinsJobPreUpdate')
    jenkinsJobPostUpdate = recipes.getHookStack('jenkinsJobPostUpdate')

    updatedJobs = {}
    verbose = args.verbose - args.quiet
    date = str(datetime.datetime.now())

    recipesAudit = runInEventLoop(recipes.getScmAudit())
    if recipesAudit is not None:
        recipesAudit = json.dumps(recipesAudit.dump(), sort_keys=True)

    def printLine(level, job, *args):
        if level <= verbose:
            if job:
                print(job + ":", *args)
            else:
                print(*args)

    def printNormal(job, *args): printLine(0, job, *args)
    def printInfo(job, *args): printLine(1, job, *args)
    def printDebug(job, *args): printLine(2, job, *args)

    updatePolicy = config.jobsUpdate
    updateAlways = updatePolicy == "always"
    updateDescription = updatePolicy in ("always", "description")

    # connect to server
    with JenkinsConnection(config, args.ssl_verify) as connection:
        # verify plugin state
        printDebug(None, "Check available plugins...")
        connection.checkPlugins()

        # push new jobs / reconfigure existing ones
        for (name, job) in jobs.items():
            info = {
                'alias' : args.name,
                'name' : name,
                'url' : config.url,
                'prefix' : config.prefix,
                'nodes' : config.nodes,
                'sandbox' : config.sandbox,
                'windows' : config.windows,
                'hostPlatform' : config.hostPlatform,
                'checkoutSteps' : job.getCheckoutSteps(),
                'buildSteps' : job.getBuildSteps(),
                'packageSteps' : job.getPackageSteps(),
                'authtoken': config.authtoken
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
                    # Amend possibly missing fields
                    oldJobConfig.setdefault('lazyHash', oldJobConfig.get('hash'))
                    oldJobConfig.setdefault('lazyWarning', False)
            else:
                origXML = None

            # calculate new job configuration
            try:
                if origXML is not None:
                    jobXML = applyHooks(jenkinsJobPreUpdate, origXML, info, True)
                else:
                    jobXML = None

                jobXML = job.dumpXML(jobXML, config, date, recipesAudit)

                if origXML is not None:
                    jobXML = applyHooks(jenkinsJobPostUpdate, jobXML, info)
                    # job hash is based on unmerged config to detect just our changes
                    hashXML = applyHooks(jenkinsJobCreate,
                        job.dumpXML(None, config, date, recipesAudit),
                        info)
                else:
                    jobXML = applyHooks(jenkinsJobCreate, jobXML, info)
                    hashXML = jobXML

                # Remove description from job hash for comparisons. Additionally
                # wipe comments and audit-engine calls for schedule decisions.
                root = xml.etree.ElementTree.fromstring(hashXML)
                description = root.find("description").text
                newDescrHash = hashlib.sha1(description.encode('utf8')).digest()
                root.find("description").text = ""
                hashXML = xml.etree.ElementTree.tostring(root, encoding="UTF-8")
                newJobHash = hashlib.sha1(hashXML).digest()
                cleanJobConfig(root)
                scheduleHashXML = xml.etree.ElementTree.tostring(root, encoding="UTF-8")
                newScheduleHash = hashlib.sha1(scheduleHashXML).digest()
                newJobConfig = {
                    'hash' : newJobHash,
                    'lazyHash' : newScheduleHash,
                    'lazyWarning' : not updateDescription,
                    'scheduledHash' : newScheduleHash,
                    'descrHash' : newDescrHash,
                    'enabled' : True,
                }
            except xml.etree.ElementTree.ParseError as e:
                raise BuildError("Cannot parse XML of job '{}': {}".format(
                    name, str(e)))

            # configure or create job
            if name in existingJobs:
                # skip job if completely unchanged
                if oldJobConfig == newJobConfig and not args.force:
                    printInfo(name, "Unchanged. Skipping...")
                    continue

                # Updated config.xml? Depending on the update mode not all
                # changes are considered an acutal reconfiguration.
                if (oldJobConfig['lazyHash'] != newJobConfig['lazyHash']) or \
                   (updateAlways and (oldJobConfig.get('hash') != newJobConfig['hash'])) or \
                   args.force:
                    printNormal(name, "Set new configuration...")
                    connection.updateConfig(name, jobXML)
                    oldJobConfig['hash'] = newJobConfig['hash']
                    oldJobConfig['lazyHash'] = newJobConfig['lazyHash']
                    oldJobConfig['lazyWarning'] = newJobConfig['lazyWarning']
                    oldJobConfig['descrHash'] = newJobConfig['descrHash']
                    BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                elif (updateDescription and (oldJobConfig.get('descrHash') != newJobConfig['descrHash'])) or \
                     (oldJobConfig['lazyWarning'] != newJobConfig['lazyWarning']):
                    # just set description
                    printInfo(name, "Update description...")
                    connection.setDescription(name, description)
                    oldJobConfig['lazyWarning'] = newJobConfig['lazyWarning']
                    oldJobConfig['descrHash'] = newJobConfig['descrHash']
                    BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                else:
                    printDebug(name, "Not reconfigured. Unchanged configuration.")
            else:
                printNormal(name, "Initial creation...")
                oldJobConfig = {
                    'hash' : newJobConfig['hash'],
                    'lazyHash' : newJobConfig['lazyHash'],
                    'lazyWarning' : newJobConfig['lazyWarning'],
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
            if config.keep:
                oldJobConfig = BobState().getJenkinsJobConfig(args.name, name)
                if oldJobConfig.get('enabled', True):
                    # disable obsolete jobs
                    printNormal(name, "Disabling job...")
                    if connection.disableJob(name):
                        oldJobConfig['enabled'] = False
                        BobState().setJenkinsJobConfig(args.name, name, oldJobConfig)
                    else:
                        printNormal(name, "Forget job. Has been deleted on the server!")
                        BobState().delJenkinsJob(args.name, name)
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

    config = BobState().getJenkinsConfig(args.name)
    config.url = args.url
    BobState().setJenkinsConfig(args.name, config)

def doJenkinsSetOptions(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins set-options")
    parser.add_argument("name", help="Jenkins server alias")
    parser.add_argument("--reset", action='store_true', default=False,
                        help="Reset all options to their default")
    parser.add_argument("-n", "--nodes", help="Set label for Jenkins Slave")
    parser.add_argument("-o", default=[], action='append', dest='options',
                        help="Set extended Jenkins options")
    parser.add_argument("--host-platform", choices=["linux", "msys", "win32"],
            help="Jenkins host platform type.")
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
    group.add_argument('--shortdescription', action='store_true', default=None,
                        help='Don\'t calculate all paths for description')
    group.add_argument('--longdescription', action='store_false', dest='shortdescription',
                        help='Calculate all paths for description')
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

    defines = processDefines(args.defines)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)
    config = BobState().getJenkinsConfig(args.name)

    if args.reset:
        config.reset()

    if args.nodes is not None:
        config.nodes = args.nodes
    if args.prefix is not None:
        config.prefix = args.prefix
    if args.host_platform is not None:
        config.hostPlatform = args.host_platform
    for r in args.add_root:
        if r not in config.roots:
            config.roots.append(r)
        else:
            print("Not adding root '{}': already configured".format(r), file=sys.stderr)
    for r in args.del_root:
        try:
            config.roots.remove(r)
        except ValueError:
            print("Cannot remove root '{}': not found".format(r), file=sys.stderr)
    if args.download is not None:
        config.download = args.download
    if args.upload is not None:
        config.upload = args.upload
    if args.sandbox is not None:
        config.sandbox = args.sandbox
    if defines:
        config.defines.update(defines)
    for d in args.undefines:
        try:
            del config.defines[d]
        except KeyError:
            print("Cannot undefine '{}': not defined".format(d), file=sys.stderr)
    if args.credentials is not None:
        config.credentials = args.credentials
    if args.authtoken is not None:
        config.authtoken = args.authtoken
    if args.shortdescription is not None:
        config.shortdescription = args.shortdescription
    if args.clean is not None:
        config.clean = args.clean
    if args.keep is not None:
        config.keep = args.keep

    for i in args.options:
        (opt, sep, val) = i.partition("=")
        if sep != "=":
            parser.error("Malformed extended option: "+i)
        if val == "":
            config.delOption(opt)
        else:
            config.setOption(opt, val, parser.error)

    if config.artifactsCopy == "archive":
        if not config.upload:
            parser.error("Archive sharing can not be used without upload enabled! Exiting..")
        if not config.download:
            parser.error("Archive sharing can not be used without download enabled! Exiting..")

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
        [ "{} {}".format(c, d[1]) for (c, d) in availableJenkinsCmds.items()
                                   if not c.startswith("_")
        ]))
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

    if args.subcommand in availableJenkinsCmds:
        BobState().setAsynchronous()
        try:
            availableJenkinsCmds[args.subcommand][0](recipes, args.args)
        except urllib.error.HTTPError as e:
            raise BuildError("HTTP error: " + str(e))
        except OSError as e:
            raise BuildError("OS error: " + str(e))
        finally:
            BobState().setSynchronous()
    else:
        parser.error("Unknown subcommand '{}'".format(args.subcommand))

