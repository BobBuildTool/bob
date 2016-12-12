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
import os, os.path
import schema
import subprocess
import xml.etree.ElementTree

class SvnScm:

    SCHEMA = schema.Schema({
        'scm' : 'svn',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('revision') : schema.Or(int, str)
    })

    def __init__(self, spec):
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
        scm = xml.etree.ElementTree.Element("scm", attrib={
            "class" : "hudson.scm.SubversionSCM",
            "plugin" : "subversion@2.4.5",
        })

        locations = xml.etree.ElementTree.SubElement(scm, "locations")
        for m in self.__modules:
            location = xml.etree.ElementTree.SubElement(locations,
                "hudson.scm.SubversionSCM_-ModuleLocation")

            url = m[ "url" ]
            if m["revision"]:
                url += ( "@" + m["revision"] )

            xml.etree.ElementTree.SubElement(location, "remote").text = url
            credentialsId = xml.etree.ElementTree.SubElement(location, "credentialsId")
            if credentials: credentialsId.text = credentials
            xml.etree.ElementTree.SubElement(location, "local").text = (
                os.path.join(workPath, m["dir"]) if m["dir"] else workPath )
            xml.etree.ElementTree.SubElement(location, "depthOption").text = "infinity"
            xml.etree.ElementTree.SubElement(location, "ignoreExternalsOption").text = "true"

            xml.etree.ElementTree.SubElement(scm, "excludedRegions")
            xml.etree.ElementTree.SubElement(scm, "includedRegions")
            xml.etree.ElementTree.SubElement(scm, "excludedUsers")
            xml.etree.ElementTree.SubElement(scm, "excludedRevprop")
            xml.etree.ElementTree.SubElement(scm, "excludedCommitMessages")
            xml.etree.ElementTree.SubElement(scm, "workspaceUpdater",
                attrib={"class":"hudson.scm.subversion.UpdateUpdater"})
            xml.etree.ElementTree.SubElement(scm, "ignoreDirPropChanges").text = "false"
            xml.etree.ElementTree.SubElement(scm, "filterChangelog").text = "false"

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
    # and if verbose is not zero print additional informations about it.
    #
    # return values:
    #  - error: the scm is in a error state. Use this if svn call returns a error code.
    #  - unclean: SCM is unclean. Could be: modified files, switched to another URL or revision
    #  - clean: same URL and revision as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkout. 'error' and 'unclean' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath, dir, verbose=0):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty'

        for m in self.__modules:
            if m['dir'] == dir:
                break;

        status = ""
        longStatus = ""
        try:
            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'status')
            if len(svnoutput):
                status += "M"
                longStatus += colorize("    > modified:\n", "33")
                if verbose >= 2:
                    for line in svnoutput.split('\n'):
                        longStatus += '       '+line.rstrip()

            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'info', '--xml')
            info = xml.etree.ElementTree.fromstring(svnoutput)
            entry = info.find('entry')
            url = entry.find('url').text
            revision = entry.attrib['revision']

            if m['url'] != url:
                status += "S"
                longStatus += colorize("     > URLs do not match!\n     recipe:\t{}\n     svn info:\t{}".format(m['url'], url), "33")
            if m['revision'] is not None and int(revision) != int(m['revision']):
                status += "S"
                longStatus += colorize("    ! wrong revision: recipe: {} svn info: {}".format(m['revision'], revision), "33")

            if status == "":
                if verbose >= 3:
                    print(colorize("   STATUS   {}".format(scmdir), "32"))
                return 'clean'
            else:
                if verbose != 0:
                    print(colorize("   STATUS {0: <4} {1}".format(status, scmdir), "33"))
                if (verbose >= 2) and (longStatus != ""):
                    print(longStatus)
                return 'unclean'
        except BuildError as e:
            print(e)
            ret = 'error'

        return ret

