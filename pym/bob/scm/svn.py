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

from ..errors import BuildError
from ..tty import colorize
from ..utils import hashString
from .scm import Scm, ScmAudit
import os, os.path
import schema
import subprocess
from xml.etree import ElementTree

class SvnScm(Scm):

    SCHEMA = schema.Schema({
        'scm' : 'svn',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('revision') : schema.Or(int, str)
    })

    def __init__(self, spec, overrides=[]):
        super().__init__(overrides)
        self.__recipe = spec['recipe']
        self.__url = spec["url"]
        self.__dir = spec.get("dir", ".")
        self.__revision = spec.get("revision")

    def getProperties(self):
        ret = {
            'scm' : 'svn',
            "recipe" : self.__recipe,
            "url" : self.__url,
            "dir" : self.__dir,
        }
        if self.__revision:
            ret["revision"] = self.__revision
        return ret

    def asScript(self):
        return """
if [[ -d "{SUBDIR}/.svn" ]] ; then
    if [[ "{URL}" != */tags/* ]] ; then
        svn up {REVISION_ARG} "{SUBDIR}"
    fi
else
    if ! svn co {REVISION_ARG} "{URL}" "{SUBDIR}" ; then
        rm -rf "{SUBDIR}"
        exit 1
    fi
fi
""".format(
          URL=self.__url, SUBDIR=self.__dir,
          REVISION_ARG=(("-r " + str( self.__revision ) ) if self.__revision else '')
          )


    def asDigestScript(self):
        """Return forward compatible stable string describing this svn module.

        The module is represented as "url[@rev] > dir".
        """
        return (self.__url + ( ("@"+str(self.__revision)) if self.__revision else "" ) + " > "
                + self.__dir)

    def asJenkins(self, workPath, credentials, options):
        scm = ElementTree.Element("scm", attrib={
            "class" : "hudson.scm.SubversionSCM",
            "plugin" : "subversion@2.4.5",
        })

        locations = ElementTree.SubElement(scm, "locations")
        location = ElementTree.SubElement(locations,
            "hudson.scm.SubversionSCM_-ModuleLocation")

        url = self.__url
        if self.__revision:
            url += ( "@" + str(self.__revision) )

        ElementTree.SubElement(location, "remote").text = url
        credentialsId = ElementTree.SubElement(location, "credentialsId")
        if credentials: credentialsId.text = credentials
        ElementTree.SubElement(location, "local").text = (
            os.path.normpath(os.path.join(workPath, self.__dir)) )
        ElementTree.SubElement(location, "depthOption").text = "infinity"
        ElementTree.SubElement(location, "ignoreExternalsOption").text = "true"

        ElementTree.SubElement(scm, "excludedRegions")
        ElementTree.SubElement(scm, "includedRegions")
        ElementTree.SubElement(scm, "excludedUsers")
        ElementTree.SubElement(scm, "excludedRevprop")
        ElementTree.SubElement(scm, "excludedCommitMessages")
        ElementTree.SubElement(scm, "workspaceUpdater",
            attrib={"class":"hudson.scm.subversion.UpdateUpdater"})
        ElementTree.SubElement(scm, "ignoreDirPropChanges").text = "false"
        ElementTree.SubElement(scm, "filterChangelog").text = "false"

        return scm

    def getDirectories(self):
        return { self.__dir : hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return str(self.__revision).isnumeric()

    def hasJenkinsPlugin(self):
        return True

    def callSubversion(self, workspacePath, *args):
        cmdLine = ['svn']
        cmdLine.extend(args)
        cwd = os.path.join(workspacePath, self.__dir)
        try:
            output = subprocess.check_output(cmdLine, cwd=cwd,
                universal_newlines=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise BuildError("svn error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                cwd, " ".join(cmdLine), e.output.rstrip()))
        return output

    # Get SvnSCM status. The purpose of this function is to return the status of the given directory
    #
    # return values:
    #  - error: the scm is in a error state. Use this if svn call returns a error code.
    #  - dirty: SCM is dirty. Could be: modified files, switched to another URL or revision
    #  - clean: same URL and revision as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkout. 'error' and 'dirty' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath):
        if not os.path.exists(os.path.join(workspacePath, self.__dir)):
            return 'empty','',''

        status = 'clean'
        shortStatus = ''
        longStatus = ''
        def setStatus(shortMsg, longMsg, dirty=True):
            nonlocal status, shortStatus, longStatus
            if (shortMsg not in shortStatus):
                shortStatus += shortMsg
            longStatus += longMsg
            if (dirty):
                status = 'dirty'

        try:
            svnoutput = self.callSubversion(workspacePath, 'status')
            if len(svnoutput):
                longMsg = colorize("> modified:\n", "33")
                for line in svnoutput.split('\n'):
                    longMsg += '  '+line.rstrip()
                setStatus('M', longMsg)

            svnoutput = self.callSubversion(workspacePath, 'info', '--xml')
            info = ElementTree.fromstring(svnoutput)
            entry = info.find('entry')
            url = entry.find('url').text
            revision = entry.attrib['revision']

            if self.__url != url:
                setStatus('S', colorize("> URLs do not match!\n     recipe:\t{}\n     svn info:\t{}".format(self.__url, url), "33"))
            if self.__revision is not None and int(revision) != int(self.__revision):
                setStatus('S', colorize("> wrong revision: recipe: {} svn info: {}".format(self.__revision, revision), "33"))

        except BuildError as e:
            print(e)
            status = 'error'

        return status, shortStatus, longStatus

    def getAuditSpec(self):
        return ("svn", self.__dir)


class SvnAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'svn',
        'dir' : str,
        'url' : str,
        'revision' : int,
        'dirty' : bool,
        'repository' : {
            'root' : str,
            'uuid' : str
        }
    })

    def _scanDir(self, workspace, dir):
        self.__dir = dir
        try:
            info = ElementTree.fromstring(subprocess.check_output(
                ["svn", "info", "--xml", dir],
                cwd=workspace, universal_newlines=True))
            self.__url = info.find('entry/url').text
            self.__revision = int(info.find('entry').get('revision'))
            self.__repoRoot = info.find('entry/repository/root').text
            self.__repoUuid = info.find('entry/repository/uuid').text

            status = subprocess.check_output(["svn", "status", dir],
                cwd=workspace, universal_newlines=True)
            self.__dirty = status != ""
        except subprocess.CalledProcessError as e:
            raise BuildError("Svn audit failed: " + str(e))
        except OSError as e:
            raise BuildError("Error calling git: " + str(e))
        except ElementTree.ParseError as e:
            raise BuildError("Invalid XML received from svn")

    def _load(self, data):
        self.__dir = data["dir"]
        self.__url = data["url"]
        self.__revision = data["revision"]
        self.__dirty = data["dirty"]
        self.__repoRoot = data["repository"]["root"]
        self.__repoUuid = data["repository"]["uuid"]

    def dump(self):
        return {
            "type" : "svn",
            "dir" : self.__dir,
            "url" : self.__url,
            "revision" : self.__revision,
            "dirty" :  self.__dirty,
            "repository" : {
                "root" :  self.__repoRoot,
                "uuid" :  self.__repoUuid,
            }
        }

    def getStatusLine(self):
        return self.__url + "@" + str(self.__revision) + ("-dirty" if self.__dirty else "")
