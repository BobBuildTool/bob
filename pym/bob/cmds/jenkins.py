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

from ..errors import ParseError, BuildError
from ..input import walkPackagePath
from ..state import BobState
from ..utils import asHexStr
from pipes import quote
import argparse
import base64
import getpass
import hashlib
import http.client
import os.path
import re
import sys
import textwrap
import urllib.parse
import xml.etree.ElementTree

regexJobName = re.compile(r'[^a-zA-Z0-9-_]', re.DOTALL)

def escapeJobName(name):
    return regexJobName.sub('_', name)

class JenkinsJob:
    def __init__(self, name, displayName, prefix, root):
        self.__name = name
        self.__displayName = displayName
        self.__prefix = prefix
        self.__isRoot = root
        self.__checkoutSteps = {}
        self.__buildSteps = {}
        self.__packageSteps = {}
        self.__deps = {}

    def __getJobName(self, p):
        return escapeJobName(self.__prefix + p.getRecipe().getBaseName())

    def getName(self):
        return self.__name

    def addDependencies(self, deps):
        for dep in deps:
            self.__deps[dep.getDigest()] = dep

    def addCheckoutStep(self, step):
        self.__checkoutSteps[step.getDigest()] = step

    def addBuildStep(self, step):
        self.__buildSteps[step.getDigest()] = step

    def addPackageStep(self, step):
        self.__packageSteps[step.getDigest()] = step

    def getDependentJobs(self):
        deps = set()
        for d in self.__deps.values():
            deps.add(self.__getJobName(d.getPackage()))
        return deps

    def dumpStep(self, d):
        cmds = []
        cmds.append("#!/bin/bash -ex")
        cmds.append("mkdir -p {}".format(d.getExecPath()))
        cmds.append("cd {}".format(d.getExecPath()))
        cmds.append("")
        cmds.append("declare -A BOB_ALL_PATHS=(\n{}\n)".format("\n".join(sorted(
            [ "    [{}]={}".format(quote(a.getPackage().getName()),
                                   "$WORKSPACE/"+quote(a.getExecPath()))
                for a in d.getAllDepSteps() ] ))))
        cmds.append("declare -A BOB_DEP_PATHS=(\n{}\n)".format("\n".join(sorted(
            [ "    [{}]={}".format(quote(a.getPackage().getName()),
                                   "$WORKSPACE/"+quote(a.getExecPath()))
                for a in d.getArguments() ] ))))
        cmds.append("declare -A BOB_TOOL_PATHS=(\n{}\n)".format("\n".join(sorted(
            [ "    [{}]=$WORKSPACE/{}".format(quote(t), quote(p))
                for (t,p) in d.getTools().items()] ))))
        env = { key: quote(value) for (key, value) in d.getEnv().items() }
        env.update({
            "PATH": ":".join(
                [ "$WORKSPACE/"+quote(d) for d in d.getPaths() ] + ["$PATH"]),
            "LD_LIBRARY_PATH": ":".join(
                [ "$WORKSPACE/"+quote(p) for p in d.getLibraryPaths() ]),
            "BOB_CWD": "$WORKSPACE/" + quote(d.getExecPath()),
        })
        for (k,v) in sorted(env.items()):
            cmds.append("export {}={}".format(k, v))

        cmds.append("set -- {}".format(" ".join(
            [ "$WORKSPACE/"+quote(a.getExecPath()) for a in d.getArguments() ])))
        cmds.append("")

        cmds.append("set -o errtrace")
        cmds.append("set -o nounset")
        cmds.append("set -o pipefail")
        cmds.append("trap 'RET=$? ; echo \"Step failed on line ${LINENO}: Exit status ${RET}; Command: ${BASH_COMMAND}\" >&2 ; exit $RET' ERR")
        cmds.append("trap 'for i in \"${_BOB_TMP_CLEANUP[@]-}\" ; do rm -f \"$i\" ; done' EXIT")
        cmds.append("")
        cmds.append("# BEGIN BUILD SCRIPT")
        cmds.append(d.getJenkinsScript())
        cmds.append("# END BUILD SCRIPT")
        if d.isShared():
            bid = asHexStr(d.getBuildId())
            cmds.append("")
            cmds.append(textwrap.dedent("""\
            # install shared package atomically
            if [ ! -d ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}/{BID2} ] ; then
                T=$(mktemp -d -p ${{SLAVE_HOME:-$JENKINS_HOME}})
                rsync -a $WORKSPACE/{EXEC_PATH}/ $T
                mkdir -p ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}
                mv -T $T ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}/{BID2} || rm -rf $T
            fi""".format(EXEC_PATH=d.getExecPath(), BID1=bid[0:2], BID2=bid[2:])))
        return "\n".join(cmds)

    @staticmethod
    def __tgzName(d):
        return d.getExecPath().replace('/', '_') + ".tgz"

    def dumpXML(self, orig=None, nodes=""):
        if orig:
            root = xml.etree.ElementTree.fromstring(orig)
            builders = root.find("builders")
            builders.clear()
            triggers = root.find("triggers")
            revBuild = triggers.find("jenkins.triggers.ReverseBuildTrigger")
            if revBuild is not None: triggers.remove(revBuild)
            publishers = root.find("publishers")
            archiver = publishers.find("hudson.tasks.ArtifactArchiver")
            if archiver is None:
                archiver = xml.etree.ElementTree.SubElement(
                    publishers, "hudson.tasks.ArtifactArchiver")
            else:
                archiver.clear()
            for scm in root.findall("scm"):
                root.remove(scm)
        else:
            root = xml.etree.ElementTree.Element("project")
            xml.etree.ElementTree.SubElement(root, "actions")
            xml.etree.ElementTree.SubElement(root, "description").text = ""
            if self.__name != self.__displayName:
                xml.etree.ElementTree.SubElement(
                    root, "displayName").text = self.__displayName
            xml.etree.ElementTree.SubElement(root, "keepDependencies").text = "false"
            xml.etree.ElementTree.SubElement(root, "properties")
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
            scmTrigger = xml.etree.ElementTree.SubElement(
                triggers, "hudson.triggers.SCMTrigger")
            xml.etree.ElementTree.SubElement(scmTrigger, "spec").text = ""
            xml.etree.ElementTree.SubElement(
                scmTrigger, "ignorePostCommitHooks").text = "false"
            publishers = xml.etree.ElementTree.SubElement(root, "publishers")
            archiver = xml.etree.ElementTree.SubElement(
                publishers, "hudson.tasks.ArtifactArchiver")
            xml.etree.ElementTree.SubElement(root, "buildWrappers")

        prepareCmds = []
        prepareCmds.append("#!/bin/bash -ex")
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
        prepareCmds.append("          else")
        prepareCmds.append("              echo \"Remove $PWD/$i\"")
        prepareCmds.append("              rm -rf \"$i\"")
        prepareCmds.append("          fi")
        prepareCmds.append("   done")
        prepareCmds.append("}")
        prepareCmds.append("")
        whiteList = []
        whiteList.extend([ JenkinsJob.__tgzName(d) for d in self.__deps.values()])
        whiteList.extend([ d.getExecPath() for d in self.__checkoutSteps.values() ])
        whiteList.extend([ d.getExecPath() for d in self.__buildSteps.values() ])
        prepareCmds.append("pruneUnused " + " ".join(sorted(whiteList)))
        prepareCmds.append("set -x")

        deps = sorted(self.__deps.values(), key=lambda d: d.getExecPath())
        if deps:
            revBuild = xml.etree.ElementTree.SubElement(
                triggers, "jenkins.triggers.ReverseBuildTrigger")
            xml.etree.ElementTree.SubElement(revBuild, "spec").text = ""
            xml.etree.ElementTree.SubElement(
                revBuild, "upstreamProjects").text = ", ".join(
                    [ self.__getJobName(d.getPackage()) for d in deps ])
            threshold = xml.etree.ElementTree.SubElement(revBuild, "threshold")
            xml.etree.ElementTree.SubElement(threshold, "name").text = "SUCCESS"
            xml.etree.ElementTree.SubElement(threshold, "ordinal").text = "0"
            xml.etree.ElementTree.SubElement(threshold, "color").text = "BLUE"
            xml.etree.ElementTree.SubElement(threshold, "completeBuild").text = "true"

            # copy deps into workspace
            for d in deps:
                if d.isShared():
                    bid = asHexStr(d.getBuildId())
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
                        fileCond, "file").text = "bob/"+bid[0:2]+"/"+bid[2:]
                    xml.etree.ElementTree.SubElement(
                        fileCond, "baseDir", attrib={
                            "class" : "org.jenkins_ci.plugins.run_condition.common.BaseDirectory$JenkinsHome"
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
                    cp, "project").text = self.__getJobName(d.getPackage())
                xml.etree.ElementTree.SubElement(
                    cp, "filter").text = JenkinsJob.__tgzName(d)
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
                    bid = asHexStr(d.getBuildId())
                    prepareCmds.append(textwrap.dedent("""\
                        if [ ! -d ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}/{BID2} ] ; then
                            T=$(mktemp -d -p ${{SLAVE_HOME:-$JENKINS_HOME}})
                            tar xf {TGZ} -C $T
                            mkdir -p ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}
                            mv -T $T ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}/{BID2} || rm -rf $T
                        fi
                        mkdir -p {EXEC_DIR}
                        ln -sfT ${{SLAVE_HOME:-$JENKINS_HOME}}/bob/{BID1}/{BID2} {EXEC_PATH}
                        """.format(BID1=bid[0:2], BID2=bid[2:], TGZ=JenkinsJob.__tgzName(d),
                                   EXEC_DIR=os.path.dirname(d.getExecPath()),
                                   EXEC_PATH=d.getExecPath())))
                else:
                    prepareCmds.append("mkdir -p " + d.getExecPath())
                    prepareCmds.append("tar zxf {} -C {}".format(
                        JenkinsJob.__tgzName(d), d.getExecPath()))

        prepare = xml.etree.ElementTree.SubElement(builders, "hudson.tasks.Shell")
        xml.etree.ElementTree.SubElement(prepare, "command").text = "\n".join(
            prepareCmds)

        # checkout steps
        checkoutSCMs = []
        for d in sorted(self.__checkoutSteps.values(), key=lambda s: s.getExecPath()):
            if d.getJenkinsScript():
                checkout = xml.etree.ElementTree.SubElement(
                    builders, "hudson.tasks.Shell")
                xml.etree.ElementTree.SubElement(
                    checkout, "command").text = self.dumpStep(d)
            checkoutSCMs.extend(d.getJenkinsXml())

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

        # build steps
        for d in sorted(self.__buildSteps.values(), key=lambda s: s.getExecPath()):
            build = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(
                build, "command").text = self.dumpStep(d)

        # package steps
        publish = []
        for d in sorted(self.__packageSteps.values(), key=lambda s: s.getExecPath()):
            package = xml.etree.ElementTree.SubElement(
                builders, "hudson.tasks.Shell")
            xml.etree.ElementTree.SubElement(package, "command").text = "\n".join([
                self.dumpStep(d),
                "", "# pack result for archive and inter-job exchange",
                "cd $WORKSPACE",
                "tar zcfv {} -C {} .".format(JenkinsJob.__tgzName(d), d.getExecPath())
            ])
            publish.append(JenkinsJob.__tgzName(d))

        xml.etree.ElementTree.SubElement(
            archiver, "artifacts").text = ",".join(publish)
        xml.etree.ElementTree.SubElement(
            archiver, "latestOnly").text = "false" if self.__isRoot else "true"
        xml.etree.ElementTree.SubElement(
            archiver, "allowEmptyArchive").text = "false"

        return xml.etree.ElementTree.tostring(root, encoding="UTF-8")


    def dumpGraph(self, done):
        for d in self.__deps.values():
            depName = self.__getJobName(d.getPackage())
            key = (self.__name, depName)
            if key not in done:
                print(" \"{}\" -> \"{}\";".format(self.__name, depName))
                done.add(key)


def _genJenkinsJobs(p, jobs, prefix):
    displayName = prefix + p.getRecipe().getBaseName()
    name = escapeJobName(displayName)
    if name in jobs:
        jj = jobs[name]
    else:
        jj = JenkinsJob(name, displayName, prefix, p.getRecipe().isRoot())
        jobs[name] = jj

    checkout = p.getCheckoutStep()
    if checkout.isValid():
        jj.addCheckoutStep(checkout)
    build = p.getBuildStep()
    if build.isValid():
        jj.addBuildStep(build)
    jj.addPackageStep(p.getPackageStep())

    allDeps = p.getAllDepSteps()
    jj.addDependencies(allDeps)
    for d in allDeps:
        _genJenkinsJobs(d.getPackage(), jobs, prefix)

def checkRecipeCycles(p, stack=[]):
    name = p.getRecipe().getBaseName()
    if name in stack:
        ParseError("Job cycle found in '{}': {}".format(name, stack))
    else:
        stack = [name] + stack
        for d in p.getAllDepSteps():
            checkRecipeCycles(d.getPackage(), stack)

def genJenkinsJobs(recipes, jenkins):
    jobs = {}
    config = BobState().getJenkinsConfig(jenkins)
    prefix = config["prefix"]
    rootPackages = recipes.generatePackages(
        lambda step, mode: BobState().getJenkinsByNameDirectory(
            jenkins, step.getPackage().getPath()+"/"+step.getLabel(),
            step.getDigest()),
        config.get('defines', {}))

    for root in [ walkPackagePath(rootPackages, r) for r in config["roots"] ]:
        checkRecipeCycles(root)
        _genJenkinsJobs(root, jobs, prefix)

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
    parser.add_argument("-p", "--prefix", default="", help="Prefix for jobs")
    parser.add_argument("-r", "--root", default=[], action='append',
                        help="Root package (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
                        help="Override default environment variable")
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

    if args.name in BobState().getAllJenkins():
        print("Jenkins '{}' already added.".format(args.name), file=sys.stderr)
        sys.exit(1)

    roots = args.root
    if not roots:
        print("Must specify at least one root package.", file=sys.stderr)
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
        "roots" : roots,
        "prefix" : args.prefix,
        "nodes" : args.nodes,
        "defines" : defines
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

    jobs = genJenkinsJobs(recipes, args.name)
    for j in sorted(jobs.keys()):
        with open(os.path.join(args.dir, jobs[j].getName()+".xml"), "wb") as f:
            f.write(jobs[j].dumpXML())

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

    for j in BobState().getAllJenkins():
        print(j)
        cfg = BobState().getJenkinsConfig(j)
        if args.verbose >= 1:
            url = cfg["url"]
            if url.get('username'):
                userPass = url['username']
                if url.get('password'):
                    userPass += ":" + url['password']
                userPass += "@"
            else:
                userPass = ""
            print(" URL:", "{}://{}{}{}{}".format(url['scheme'], userPass, url['server'],
                ":{}".format(url['port']) if url.get('port') else "", url['path']))
            if cfg.get('prefix'):
                print(" Prefix:", cfg['prefix'])
            if cfg.get('nodes'):
                print(" Nodes:", cfg['nodes'])
            if cfg.get('defines'):
                print(" Defines:", ", ".join([ k+"="+v for (k,v) in cfg['defines'].items() ]))
            print(" Roots:", ", ".join(cfg['roots']))
        if args.verbose >= 2:
            print(" Jobs:", ", ".join(sorted(BobState().getJenkinsAllJobs(j))))

def doJenkinsPrune(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins prune")
    parser.add_argument("name", help="Prune jobs from Jenkins server")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)

    # connect to server
    if config["url"]["scheme"] == 'http':
        connection = http.client.HTTPConnection(config["url"]["server"],
                                                config["url"].get("port"))
    else:
        raise BuildError("Unsupported Jenkins URL scheme: '{}'".format(
            config["url"]["scheme"]))
    urlPath = config["url"]["path"]

    # construct headers
    headers = { "Content-Type": "application/xml" }
    if config["url"].get("username"):
        passwd = config["url"].get("password")
        if passwd is None:
            passwd = getpass.getpass()
        userPass = config["url"]["username"] + ":" + passwd
        headers['Authorization'] = 'Basic ' + base64.b64encode(
            userPass.encode("utf-8")).decode("ascii")

    try:
        for name in existingJobs:
            print("Delete", name, "...")
            connection.request("POST", urlPath + "job/" + name + "/doDelete",
                               headers=headers)
            response = connection.getresponse()
            if response.status != 302 and response.status != 404:
                raise BuildError("Error deleting '{}': HTTP error: {} {}".format(
                    name, response.status, response.reason))
            response.read()
            BobState().delJenkinsJob(args.name, name)

    finally:
        connection.close()

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

def doJenkinsPush(recipes, argv):
    parser = argparse.ArgumentParser(prog="bob jenkins push")
    parser.add_argument("name", help="Push jobs to Jenkins server")
    parser.add_argument("-f", "--force", action="store_true", default=False,
                        help="Overwrite existing jobs")
    parser.add_argument("--no-trigger", action="store_true", default=False,
                        help="Do not trigger build for updated jobs")
    args = parser.parse_args(argv)

    if args.name not in BobState().getAllJenkins():
        print("Jenkins '{}' not known.".format(args.name), file=sys.stderr)
        sys.exit(1)

    config = BobState().getJenkinsConfig(args.name)
    existingJobs = BobState().getJenkinsAllJobs(args.name)
    jobs = genJenkinsJobs(recipes, args.name)
    buildOrder = genJenkinsBuildOrder(jobs)

    # connect to server
    if config["url"]["scheme"] == 'http':
        connection = http.client.HTTPConnection(config["url"]["server"],
                                                config["url"].get("port"))
    else:
        raise BuildError("Unsupported Jenkins URL scheme: '{}'".format(
            config["url"]["scheme"]))
    urlPath = config["url"]["path"]

    # construct headers
    headers = { "Content-Type": "application/xml" }
    if config["url"].get("username"):
        passwd = config["url"].get("password")
        if passwd is None:
            passwd = getpass.getpass()
        userPass = config["url"]["username"] + ":" + passwd
        headers['Authorization'] = 'Basic ' + base64.b64encode(
            userPass.encode("utf-8")).decode("ascii")

    nodes = config.get("nodes")
    changedJobs = set([])

    try:
        # push new jobs / reconfigure existing ones
        for (name, job) in jobs.items():
            # get original XML if it exists
            origXML = None
            if name in existingJobs:
                connection.request("GET", urlPath + "job/" + name + "/config.xml",
                                   headers=headers)
                response = connection.getresponse()
                if response.status != 200:
                    print("Warning: could not download '{}' job config: HTTP error: {} {}"
                            .format(name, response.status, response.reason),
                        file=sys.stderr)
                    response.read()

                    if response.status == 404:
                        # Job was deleted
                        existingJobs.remove(name)
                        BobState().delJenkinsJob(args.name, name)

                else:
                    origXML = response.read()

            try:
                jobXML = job.dumpXML(origXML, nodes)
            except xml.etree.ElementTree.ParseError as e:
                raise BuildError("Cannot parse XML of job '{}': {}".format(
                    name, str(e)))
            jobConfig = {
                # hash is based on unmerged config to detect just our changes
                'hash' : hashlib.sha1(job.dumpXML()).digest()
            }

            if name in existingJobs:
                if (BobState().getJenkinsJobConfig(args.name, name)['hash'] ==
                        jobConfig['hash']):
                    # skip job if unchanged
                    continue

                connection.request("POST", urlPath + "job/" + name + "/config.xml",
                    body=jobXML, headers=headers)
                response = connection.getresponse()
                if response.status != 200:
                    raise BuildError("Error updating '{}': HTTP error: {} {}"
                        .format(name, response.status, response.reason))
                BobState().setJenkinsJobConfig(args.name, name, jobConfig)
                changedJobs.add(name)
            else:
                connection.request("POST", urlPath + "createItem?name=" + name,
                    body=jobXML, headers=headers)
                response = connection.getresponse()
                if response.status == 400 and args.force:
                    response.read()
                    connection.request("POST", urlPath + "job/" + name + "/config.xml",
                        body=jobXML, headers=headers)
                    response = connection.getresponse()
                    if response.status != 200:
                        raise BuildError("Error overwriting '{}': HTTP error: {} {}"
                            .format(name, response.status, response.reason))
                    BobState().addJenkinsJob(args.name, name, jobConfig)
                elif response.status != 200:
                    raise BuildError("Error creating '{}': HTTP error: {} {}"
                        .format(name, response.status, response.reason))
                else:
                    BobState().addJenkinsJob(args.name, name, jobConfig)
                changedJobs.add(name)
            response.read()

        # delete obsolete jobs
        for name in BobState().getJenkinsAllJobs(args.name) - set(jobs.keys()):
            print("Delete", name, "...")
            connection.request("POST", urlPath + "job/" + name + "/doDelete",
                               headers=headers)
            response = connection.getresponse()
            if response.status != 302 and response.status != 404:
                raise BuildError("Error deleting '{}': HTTP error: {} {}"
                    .format(name, response.status, response.reason))
            response.read()
            BobState().delJenkinsJob(args.name, name)

        # sort changed jobs and trigger them in leaf-to-root order
        if not args.no_trigger:
            for name in [ j for j in buildOrder if j in changedJobs ]:
                print("Schedule {}...".format(name))
                connection.request("POST", urlPath + "job/" + name + "/build",
                                   headers=headers)
                response = connection.getresponse()
                if response.status != 201:
                    print("Error scheduling '{}': HTTP error: {} {}"
                            .format(name, response.status, response.reason),
                        file=sys.stderr)
                response.read()

    finally:
        connection.close()

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

availableJenkinsCmds = {
    "add"        : (doJenkinsAdd, "[-p <prefix>] [-r <package>] NAME URL"),
    "export"  : (doJenkinsExport, "NAME DIR"),
    "graph"  : (doJenkinsGraph, "NAME"),
    "ls"         : (doJenkinsLs, "[-v]"),
    "prune"  : (doJenkinsPrune, "NAME"),
    "push"   : (doJenkinsPush, "NAME"),
    "rm"         : (doJenkinsRm, "[-f] NAME"),
    "set-url" : (doJenkinsSetUrl, "NAME URL"),
}

def doJenkins(recipes, argv, bobRoot):
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
    args = parser.parse_args(argv)

    if args.subcommand in availableJenkinsCmds:
        availableJenkinsCmds[args.subcommand][0](recipes, args.args)
    else:
        parser.error("Unknown subcommand '{}'".format(args.subcommand))

