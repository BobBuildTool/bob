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
import os, os.path
import re
import schema
import subprocess
import xml.etree.ElementTree

class GitScm:

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

    def __init__(self, spec):
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
    git fetch -t origin
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
        scm = xml.etree.ElementTree.Element("scm", attrib={
            "class" : "hudson.plugins.git.GitSCM",
            "plugin" : "git@2.2.7",
        })
        xml.etree.ElementTree.SubElement(scm, "configVersion").text = "2"

        userconfigs =  xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "userRemoteConfigs"),
                "hudson.plugins.git.UserRemoteConfig")

        url = xml.etree.ElementTree.SubElement(userconfigs,
            "url")
        url.text = self.__url

        if credentials:
            credentialsId = xml.etree.ElementTree.SubElement(userconfigs,
                         "credentialsId")
            credentialsId.text = credentials

        branch = xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "branches"),
                "hudson.plugins.git.BranchSpec"),
            "name")
        if self.__commit:
            branch.text = self.__commit
        elif self.__tag:
            branch.text = "refs/tags/" + self.__tag
        else:
            branch.text = "refs/heads/" + self.__branch

        xml.etree.ElementTree.SubElement(scm, "doGenerateSubmoduleConfigurations").text = "false"
        xml.etree.ElementTree.SubElement(scm, "submoduleCfg", attrib={"class" : "list"})

        extensions = xml.etree.ElementTree.SubElement(scm, "extensions")
        xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.RelativeTargetDirectory"),
            "relativeTargetDir").text = os.path.normpath(os.path.join(workPath, self.__dir))
        # remove untracked files
        xml.etree.ElementTree.SubElement(extensions,
            "hudson.plugins.git.extensions.impl.CleanCheckout")
        shallow = options.get("scm.git.shallow")
        if shallow is not None:
            try:
                shallow = int(shallow)
                if shallow < 0: raise ValueError()
            except ValueError:
                raise BuildError("Invalid 'git.shallow' option: " + str(shallow))
            if shallow > 0:
                co = xml.etree.ElementTree.SubElement(extensions,
                    "hudson.plugins.git.extensions.impl.CloneOption")
                xml.etree.ElementTree.SubElement(co, "shallow").text = "true"
                xml.etree.ElementTree.SubElement(co, "noTags").text = "false"
                xml.etree.ElementTree.SubElement(co, "reference").text = ""
                xml.etree.ElementTree.SubElement(co, "depth").text = str(shallow)
                xml.etree.ElementTree.SubElement(co, "honorRefspec").text = "false"

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
    # and if verbose is not zero print additional informations about it.
    #
    # return values:
    #  - error: the scm is in a error state. Use this if git returned a error code.
    #  - unclean: SCM is unclean. Could be: modified files, switched to another branch/tag/commit/repo, unpushed commits
    #  - clean: same branch/tag/commit as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkou. 'error' and 'unclean' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath, dir, verbose=0):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty'

        status = ""
        longStatus = ""
        try:
            output = self.callGit(workspacePath, 'ls-remote' ,'--get-url').rstrip()
            if output != self.__url:
                status += "S"
                longStatus += colorize("   > URL: configured: '{}'  actual: '{}'\n".format(self.__url, output), "33")
            else:
                if self.__commit:
                    output = self.callGit(workspacePath, 'rev-parse', 'HEAD').rstrip()
                    if output != self.__commit:
                        status += "S"
                        longStatus +=  colorize("   > commitId: configured: {}  actual: {}\n".format(self.__commit, output), "33")
                elif self.__tag:
                    output = self.callGit(workspacePath, 'tag', '--points-at', 'HEAD').rstrip().split()
                    if self.__tag not in output:
                        status += "S"
                        longStatus += colorize("    > tag: configured: {} actual: {}\n".format(self.__tag, ", ".join(output)), "33")
                elif self.__branch:
                    output = self.callGit(workspacePath, 'rev-parse', '--abbrev-ref', 'HEAD').rstrip()
                    if output != self.__branch:
                        status += "S"
                        longStatus += colorize("    > branch: configured: {} actual: {}\n".format(self.__branch, output), "33")
                    else:
                        output = self.callGit(workspacePath, 'rev-list', 'origin/'+self.__branch+'..HEAD')
                        if len(output):
                            status += "U"
                            # do not print detailed status this point.
                            # git log --branches --not --remotes --decorate will give the same informations.

            output = self.callGit(workspacePath, 'status', '--porcelain')
            if len(output):
                longStatus += colorize("    > modified:\n", "33")
                status += "M"
                if verbose >=2:
                    for line in output.split('\n'):
                        if line != "":
                           longStatus += '      '+line + '\n'

            # the following shows unpushed commits even on local branches. do not mark the SCM as unclean.
            output = self.callGit(workspacePath, 'log', '--branches', '--not', '--remotes', '--decorate')
            if len(output):
                status += "u"
                longStatus += colorize("     > unpushed:\n", "33")
                if verbose >= 2:
                    for line in output.split('\n'):
                       if line != "":
                           longStatus += '      ' + line + '\n'
            ret = 'clean'
            if status == "":
                if verbose >= 3:
                    print(colorize("   STATUS      {0}".format(scmdir), "32"))
            elif status != "u":
                ret = 'unclean'

            if (status != "") and (verbose != 0):
                print(colorize("   STATUS {0: <4} {1}".format(status, scmdir), "33"))
                if (verbose >= 2) and (longStatus != ""):
                    print(longStatus)
        except BuildError as e:
            print(e)
            ret = 'error'

        return ret

