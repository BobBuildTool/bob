# Bob build tool
# Copyright (C) 2016  Jan Klötzke
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
from ..tty import colorize, WarnOnce
from ..utils import hashString
from .scm import Scm, ScmAudit
from xml.etree import ElementTree
import hashlib
import os, os.path
import re
import schema
import subprocess

class GitScm(Scm):

    SCHEMA = schema.Schema({
        'scm' : 'git',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('branch') : str,
        schema.Optional('tag') : str,
        schema.Optional('commit') : str,
        schema.Optional('rev') : str,
    })

    def __init__(self, spec, overrides=[]):
        super().__init__(overrides)
        self.__recipe = spec['recipe']
        self.__url = spec["url"]
        self.__branch = None
        self.__tag = None
        self.__commit = None
        if "rev" in spec:
            rev = spec["rev"]
            if rev.startswith("refs/heads/"):
                self.__branch = rev[11:]
            elif rev.startswith("refs/tags/"):
                self.__tag = rev[10:]
            elif len(rev) == 40:
                self.__commit = rev
            else:
                raise ParseError("Invalid rev format: " + rev)
        self.__branch = spec.get("branch", self.__branch)
        self.__tag = spec.get("tag", self.__tag)
        self.__commit = spec.get("commit", self.__commit)
        if self.__commit:
            # validate commit
            if re.match("^[0-9a-f]{40}$", self.__commit) is None:
                raise ParseError("Invalid commit id: " + str(self.__commit))
        elif not self.__branch and not self.__tag:
            # nothing secified at all -> master branch
            self.__branch = "master"
        self.__dir = spec.get("dir", ".")

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'git',
            'url' : self.__url,
            'branch' : self.__branch,
            'tag' : self.__tag,
            'commit' : self.__commit,
            'dir' : self.__dir,
            'rev' : ( self.__commit if self.__commit else
                (("refs/tags/" + self.__tag) if self.__tag else
                    ("refs/heads/" + self.__branch))
            )
        }]

    def asScript(self):
        if self.__tag or self.__commit:
            return """
export GIT_SSL_NO_VERIFY=true
if [ ! -d {DIR}/.git ] ; then
    git init {DIR}
fi
cd {DIR}
# see if we have a remote
if [[ -z $(git remote) ]] ; then
    git remote add origin {URL}
fi
# checkout only if HEAD is invalid
if ! git rev-parse --verify -q HEAD >/dev/null ; then
    git fetch -t origin '+refs/heads/*:refs/remotes/origin/*'
    git checkout -q {REF}
fi
""".format(URL=self.__url, REF=self.__commit if self.__commit else "tags/"+self.__tag, DIR=self.__dir)
        else:
            return """
export GIT_SSL_NO_VERIFY=true
if [ -d {DIR}/.git ] ; then
    cd {DIR}
    if [[ $(git rev-parse --abbrev-ref HEAD) == "{BRANCH}" ]] ; then
        git pull --ff-only
    else
        echo "Warning: not updating {DIR} because branch was changed manually..." >&2
    fi
else
    if ! git clone -b {BRANCH} {URL} {DIR} ; then
        rm -rf {DIR}/.git {DIR}/*
        exit 1
    fi
fi
""".format(URL=self.__url, BRANCH=self.__branch, DIR=self.__dir)

    def asDigestScript(self):
        """Return forward compatible stable string describing this git module.

        The format is "url rev-spec dir" where rev-spec depends on the given reference.
        """
        if self.__commit:
            return self.__commit + " " + self.__dir
        elif self.__tag:
            return self.__url + " refs/tags/" + self.__tag + " " + self.__dir
        else:
            return self.__url + " refs/heads/" + self.__branch + " " + self.__dir

    def asJenkins(self, workPath, credentials, options):
        scm = ElementTree.Element("scm", attrib={
            "class" : "hudson.plugins.git.GitSCM",
            "plugin" : "git@2.2.7",
        })
        ElementTree.SubElement(scm, "configVersion").text = "2"

        userconfigs =  ElementTree.SubElement(
                ElementTree.SubElement(scm, "userRemoteConfigs"),
                "hudson.plugins.git.UserRemoteConfig")

        url = ElementTree.SubElement(userconfigs,
            "url")
        url.text = self.__url

        if credentials:
            credentialsId = ElementTree.SubElement(userconfigs,
                         "credentialsId")
            credentialsId.text = credentials

        branch = ElementTree.SubElement(
            ElementTree.SubElement(
                ElementTree.SubElement(scm, "branches"),
                "hudson.plugins.git.BranchSpec"),
            "name")
        if self.__commit:
            branch.text = self.__commit
        elif self.__tag:
            branch.text = "refs/tags/" + self.__tag
        else:
            branch.text = "refs/heads/" + self.__branch

        ElementTree.SubElement(scm, "doGenerateSubmoduleConfigurations").text = "false"
        ElementTree.SubElement(scm, "submoduleCfg", attrib={"class" : "list"})

        extensions = ElementTree.SubElement(scm, "extensions")
        ElementTree.SubElement(
            ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.RelativeTargetDirectory"),
            "relativeTargetDir").text = os.path.normpath(os.path.join(workPath, self.__dir))
        # remove untracked files
        ElementTree.SubElement(extensions,
            "hudson.plugins.git.extensions.impl.CleanCheckout")
        shallow = options.get("scm.git.shallow")
        if shallow is not None:
            try:
                shallow = int(shallow)
                if shallow < 0: raise ValueError()
            except ValueError:
                raise BuildError("Invalid 'git.shallow' option: " + str(shallow))
            if shallow > 0:
                co = ElementTree.SubElement(extensions,
                    "hudson.plugins.git.extensions.impl.CloneOption")
                ElementTree.SubElement(co, "shallow").text = "true"
                ElementTree.SubElement(co, "noTags").text = "false"
                ElementTree.SubElement(co, "reference").text = ""
                ElementTree.SubElement(co, "depth").text = str(shallow)
                ElementTree.SubElement(co, "honorRefspec").text = "false"

        return scm

    def merge(self, other):
        return False

    def getDirectories(self):
        return { self.__dir : hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return bool(self.__tag) or bool(self.__commit)

    def hasJenkinsPlugin(self):
        return True

    def callGit(self, workspacePath, *args):
        cmdLine = ['git']
        cmdLine.extend(args)
        try:
            output = subprocess.check_output(cmdLine, cwd=os.path.join(os.getcwd(), workspacePath, self.__dir),
                universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise BuildError("git error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                os.path.join(workspacePath, self.__dir), " ".join(cmdLine), e.output.rstrip()))
        return output

    # Get GitSCM status. The purpose of this function is to return the status of the given directory
    #
    # return values:
    #  - error: the scm is in a error state. Use this if git returned a error code.
    #  - dirty: SCM is dirty. Could be: modified files, switched to another branch/tag/commit/repo, unpushed commits
    #  - clean: same branch/tag/commit as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkou. 'error' and 'dirty' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath, dir):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty','',''

        status = 'clean'
        shortStatus = ""
        longStatus = ""
        def setStatus(shortMsg, longMsg, dirty=True):
            nonlocal status, shortStatus, longStatus
            if (shortMsg not in shortStatus):
                shortStatus += shortMsg
            longStatus += longMsg
            if (dirty):
                status = 'dirty'

        try:
            output = self.callGit(workspacePath, 'ls-remote' ,'--get-url').rstrip()
            if output != self.__url:
                setStatus("S", colorize("> URL: configured: '{}'  actual: '{}'\n".format(self.__url, output), "33"))
            else:
                if self.__commit:
                    output = self.callGit(workspacePath, 'rev-parse', 'HEAD').rstrip()
                    if output != self.__commit:
                        setStatus("S", colorize("> commitId: configured: {}  actual: {}\n".format(self.__commit, output), "33"))
                elif self.__tag:
                    output = self.callGit(workspacePath, 'tag', '--points-at', 'HEAD').rstrip().splitlines()
                    if self.__tag not in output:
                        actual = ("'" + ", ".join(output) + "'") if output else "not on any tag"
                        setStatus("S", colorize("    > tag: configured: '{}' actual: {}\n".format(self.__tag, actual), "33"))
                elif self.__branch:
                    output = self.callGit(workspacePath, 'rev-parse', '--abbrev-ref', 'HEAD').rstrip()
                    if output != self.__branch:
                        setStatus("S", colorize("> branch: configured: {} actual: {}\n".format(self.__branch, output), "33"))
                    else:
                        output = self.callGit(workspacePath, 'rev-list', 'origin/'+self.__branch+'..HEAD')
                        if len(output):
                            setStatus("U", "")
                            # do not print detailed status this point.
                            # git log --branches --not --remotes --decorate will give the same informations.

            output = self.callGit(workspacePath, 'status', '--porcelain')
            if len(output):
                longMsg = colorize("> modified:\n", "33")
                for line in output.split('\n'):
                    if line != "":
                       longMsg += '  '+line + '\n'
                setStatus("M", longMsg)

            # the following shows unpushed commits even on local branches. do not mark the SCM as dirty.
            output = self.callGit(workspacePath, 'log', '--branches', '--not', '--remotes', '--decorate')
            if len(output):
                longMsg = colorize("> unpushed:\n", "33")
                for line in output.split('\n'):
                   if line != "":
                       longStatus += '  ' + line + '\n'
                setStatus("u", longMsg, False)

        except BuildError as e:
            print(e)
            ret = 'error'

        return status, shortStatus, longStatus

    def getAuditSpec(self):
        return ("git", [self.__dir])


class GitAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'git',
        'dir' : str,
        'remotes' : { str : str },
        'commit' : str,
        'description' : str,
        'dirty' : bool
    })

    def _scanDir(self, workspace, dir):
        self.__dir = dir
        dir = os.path.join(workspace, dir)
        try:
            remotes = subprocess.check_output(["git", "remote", "-v"],
                cwd=dir, universal_newlines=True).split("\n")
            remotes = (r[:-8].split("\t") for r in remotes if r.endswith("(fetch)"))
            self.__remotes = { remote:url for (remote,url) in remotes }

            self.__commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                cwd=dir, universal_newlines=True).strip()
            self.__description = subprocess.check_output(
                ["git", "describe", "--always", "--dirty"],
                cwd=dir, universal_newlines=True).strip()
            self.__dirty = subprocess.call(["git", "diff-index", "--quiet", "HEAD"],
                cwd=dir) != 0
        except subprocess.CalledProcessError as e:
            raise BuildError("Git audit failed: " + str(e))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))

    def _load(self, data):
        self.__dir = data["dir"]
        self.__remotes = data["remotes"]
        self.__commit = data["commit"]
        self.__description = data["description"]
        self.__dirty = data["dirty"]

    def dump(self):
        return {
            "type" : "git",
            "dir" : self.__dir,
            "remotes" : self.__remotes,
            "commit" : self.__commit,
            "description" : self.__description,
            "dirty" : self.__dirty,
        }

    def getStatusLine(self):
        return self.__description
