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
from ..utils import hashString, joinScripts
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
        self.__modules = [{
            "recipe" : spec['recipe'],
            "url" : spec["url"],
            "dir" : spec.get("dir"),
            "revision" : spec.get("revision")
        }]

    @staticmethod
    def __moduleAsScript(m):
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
          URL=m["url"],
          SUBDIR=m["dir"] if m["dir"] else ".",
          REVISION_ARG=(("-r " + str( m["revision"] ) ) if m["revision"] else '')
          )

    @staticmethod
    def __moduleAsDigestScript(m):
        return (m["url"] + ( ("@"+str(m["revision"])) if m["revision"] else "" ) + " > "
                + (m["dir"] if m["dir"] else "."))

    def getProperties(self):
        ret = [ m.copy() for m in self.__modules ]
        for m in ret: m['scm'] = "svn"
        return ret

    def asScript(self):
        return joinScripts([ SvnScm.__moduleAsScript(m) for m in self.__modules ])

    def asDigestScript(self):
        """Return forward compatible stable string describing this/these svn module(s).

        Each module has its own line where the module is represented as "url[@rev] > dir".
        """
        return "\n".join([ SvnScm.__moduleAsDigestScript(m) for m in self.__modules ])

    def asJenkins(self, workPath, credentials, options):
        scm = ElementTree.Element("scm", attrib={
            "class" : "hudson.scm.SubversionSCM",
            "plugin" : "subversion@2.4.5",
        })

        locations = ElementTree.SubElement(scm, "locations")
        for m in self.__modules:
            location = ElementTree.SubElement(locations,
                "hudson.scm.SubversionSCM_-ModuleLocation")

            url = m[ "url" ]
            if m["revision"]:
                url += ( "@" + m["revision"] )

            ElementTree.SubElement(location, "remote").text = url
            credentialsId = ElementTree.SubElement(location, "credentialsId")
            if credentials: credentialsId.text = credentials
            ElementTree.SubElement(location, "local").text = (
                os.path.join(workPath, m["dir"]) if m["dir"] else workPath )
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

    def merge(self, other):
        if not isinstance(other, SvnScm):
            return False

        self.__modules.extend(other.__modules)
        return True

    def getDirectories(self):
        return { m['dir'] : hashString(SvnScm.__moduleAsDigestScript(m)) for m in self.__modules }

    def isDeterministic(self):
        return all([ str(m['revision']).isnumeric() for m in self.__modules ])

    def hasJenkinsPlugin(self):
        return True

    def callSubversion(self, workspacePath, *args):
        cmdLine = ['svn']
        cmdLine.extend(args)

        try:
            output = subprocess.check_output(cmdLine, cwd=workspacePath,
                universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise BuildError("svn error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                os.path.join(workspacePath, self.__dir), " ".join(cmdLine), e.output.rstrip()))
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
    def status(self, workspacePath, dir):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty','',''

        for m in self.__modules:
            if m['dir'] == dir:
                break;

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
            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'status')
            if len(svnoutput):
                longMsg = colorize("> modified:\n", "33")
                for line in svnoutput.split('\n'):
                    longMsg += '  '+line.rstrip()
                setStatus('M', longMsg)

            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'info', '--xml')
            info = ElementTree.fromstring(svnoutput)
            entry = info.find('entry')
            url = entry.find('url').text
            revision = entry.attrib['revision']

            if m['url'] != url:
                setStatus('S', colorize("> URLs do not match!\n     recipe:\t{}\n     svn info:\t{}".format(m['url'], url), "33"))
            if m['revision'] is not None and int(revision) != int(m['revision']):
                setStatus('S', colorize("> wrong revision: recipe: {} svn info: {}".format(m['revision'], revision), "33"))

        except BuildError as e:
            print(e)
            status = 'error'

        return status, shortStatus, longStatus

    def getAuditSpec(self):
        return ("svn", [ m["dir"] for m in self.__modules ])


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
