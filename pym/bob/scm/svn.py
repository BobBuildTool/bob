# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BuildError
from ..utils import joinLines
from .scm import Scm, ScmAudit, ScmTaint, ScmStatus
from pipes import quote
from textwrap import indent
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
        schema.Optional('revision') : schema.Or(int, str),
        schema.Optional('sslVerify') : bool,
    })

    def __init__(self, spec, overrides=[]):
        super().__init__(spec, overrides)
        self.__url = spec["url"]
        self.__dir = spec.get("dir", ".")
        self.__revision = spec.get("revision")
        self.__sslVerify = spec.get('sslVerify', True)

    def getProperties(self):
        ret = super().getProperties()
        ret.update({
            'scm' : 'svn',
            "url" : self.__url,
            "dir" : self.__dir,
            'sslVerify' : self.__sslVerify,
        })
        if self.__revision:
            ret["revision"] = self.__revision
        return ret

    def asScript(self):
        options = "--non-interactive"
        if not self.__sslVerify:
            options += " --trust-server-cert-failures=unknown-ca,cn-mismatch,expired,not-yet-valid,other"
        return """
{HEADER}
if [[ -d {SUBDIR}/.svn ]] ; then
    if [[ {URL} != */tags/* ]] ; then
        svn up {OPTIONS} {REVISION_ARG} {SUBDIR}
    fi
else
    if ! svn co {OPTIONS} {REVISION_ARG} {URL} {SUBDIR} ; then
        rm -rf {SUBDIR}
        exit 1
    fi
fi
""".format(
          HEADER=super().asScript(), OPTIONS=options,
          URL=quote(self.__url), SUBDIR=quote(self.__dir),
          REVISION_ARG=(("-r " + quote(str(self.__revision))) if self.__revision else '')
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

    def getDirectory(self):
        return self.__dir

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
        except OSError as e:
            raise BuildError("Error calling svn: " + str(e))
        return output.strip()

    def status(self, workspacePath):
        status = ScmStatus()
        try:
            output = self.callSubversion(workspacePath, 'status')
            if output:
                status.add(ScmTaint.modified, joinLines("> modified:", indent(output, '   ')))

            output = self.callSubversion(workspacePath, 'info', '--xml')
            info = ElementTree.fromstring(output)
            entry = info.find('entry')
            url = entry.find('url').text
            revision = entry.attrib['revision']

            if self.__url != url:
                status.add(ScmTaint.switched,
                    "> URL: configured: '{}', actual: '{}'".format(self.__url, url))
            if self.__revision is not None and int(revision) != int(self.__revision):
                status.add(ScmTaint.switched,
                    "> revision: configured: {}, actual: {}".format(self.__revision, revision))

        except BuildError as e:
            status.add(ScmTaint.error, e.slogan)

        return status

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
            raise BuildError("Error calling svn: " + str(e))
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
