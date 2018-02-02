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

from ..errors import ParseError
from ..utils import asHexStr, hashString, hashFile
from .scm import Scm, ScmAudit
from pipes import quote
import hashlib
import os.path
import re
import schema

class UrlScm(Scm):

    SCHEMA = schema.Schema({
        'scm' : 'url',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('digestSHA1') : str,
        schema.Optional('digestSHA256') : str,
        schema.Optional('extract') : schema.Or(bool, str),
        schema.Optional('fileName') : str,
        schema.Optional('stripComponents') : int,
    })

    EXTENSIONS = [
        (".tar.gz",    "tar"),
        (".tar.xz",    "tar"),
        (".tar.bz2",   "tar"),
        (".tar.bzip2", "tar"),
        (".tgz",       "tar"),
        (".tar",       "tar"),
        (".gz",        "gzip"),
        (".xz",        "xz"),
        (".7z",        "7z"),
        (".zip",       "zip"),
    ]

    EXTRACTORS = {
        "tar"  : ("tar xf", "--strip-components={}"),
        "gzip" : ("gunzip -kf", None),
        "xz"   : ("unxz -kf", None),
        "7z"   : ("7z x -y", None),
        "zip"  : ("unzip -o", None),
    }

    def __init__(self, spec, overrides=[], tidy=None):
        super().__init__(overrides)
        self.__recipe = spec['recipe']
        self.__url = spec["url"]
        self.__digestSha1 = spec.get("digestSHA1")
        if self.__digestSha1:
            # validate digest
            if re.match("^[0-9a-f]{40}$", self.__digestSha1) is None:
                raise ParseError("Invalid SHA1 digest: " + str(self.__digestSha1))
        self.__digestSha256 = spec.get("digestSHA256")
        if self.__digestSha256:
            # validate digest
            if re.match("^[0-9a-f]{64}$", self.__digestSha256) is None:
                raise ParseError("Invalid SHA256 digest: " + str(self.__digestSha256))
        self.__dir = spec.get("dir", ".")
        self.__fn = spec.get("fileName")
        if not self.__fn:
            self.__fn = self.__url.split("/")[-1]
        self.__extract = spec.get("extract", "auto")
        self.__tidy = tidy
        self.__strip = spec.get("stripComponents", 0)

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'url',
            'url' : self.__url,
            'digestSHA1' : self.__digestSha1,
            'digestSHA256' : self.__digestSha256,
            'dir' : self.__dir,
            'fileName' : self.__fn,
            'extract' : self.__extract,
            'stripComponents' : self.__strip,
        }]

    def asScript(self):
        ret = """
mkdir -p {DIR}
cd {DIR}
if [ -e {FILE} ] ; then
    curl -sSgLf -o {FILE} -z {FILE} {URL}
else
    (
        F=$(mktemp)
        trap 'rm -f $F' EXIT
        set -e
        curl -sSgLf -o $F {URL}
        mv $F {FILE}
    )
fi
""".format(DIR=quote(self.__dir), URL=quote(self.__url), FILE=quote(self.__fn))

        if self.__digestSha1:
            ret += "echo {DIGEST}\ \ {FILE} | sha1sum -c\n".format(DIGEST=self.__digestSha1, FILE=self.__fn)
        if self.__digestSha256:
            ret += "echo {DIGEST}\ \ {FILE} | sha256sum -c\n".format(DIGEST=self.__digestSha256, FILE=self.__fn)

        extractor = None
        if self.__extract in ["yes", "auto", True]:
            for (ext, tool) in UrlScm.EXTENSIONS:
                if self.__fn.endswith(ext):
                    extractor = UrlScm.EXTRACTORS[tool]
                    break
            if not extractor and self.__extract != "auto":
                raise ParseError("Don't know how to extract '"+self.__fn+"' automatically.")
        elif self.__extract in UrlScm.EXTRACTORS:
            extractor = UrlScm.EXTRACTORS[tool]
        elif self.__extract not in ["no", False]:
            raise ParseError("Invalid extract mode: " + self.__extract)

        if extractor:
            if self.__strip > 0:
                if extractor[1] is None:
                    raise ParseError("Extractor does not support 'stripComponents'!")
                strip = " " + extractor[1].format(self.__strip)
            else:
                strip = ""
            ret += """
if [ {FILE} -nt .{FILE}.extracted ] ; then
    {TOOL} {FILE}{STRIP}
    touch .{FILE}.extracted
fi
""".format(FILE=quote(self.__fn), TOOL=extractor[0], STRIP=strip)

        return ret

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir extract" if a SHA checksum was specified.
        Otherwise it is "url dir extract". A "s#" is appended if leading paths
        are stripped where # is the number of stripped elements.
        """
        return ( self.__digestSha256 if self.__digestSha256
                 else (self.__digestSha1 if self.__digestSha1 else self.__url)
                    ) + " " + os.path.join(self.__dir, self.__fn) + " " + str(self.__extract) + \
                    ( " s{}".format(self.__strip) if self.__strip > 0 else "" )

    def getDirectories(self):
        fn = self.__dir if self.__tidy else os.path.join(self.__dir, self.__fn)
        return { fn : hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return (self.__digestSha1 is not None) or (self.__digestSha256 is not None)

    def getAuditSpec(self):
        return ("url", [os.path.join(self.__dir, self.__fn)])

    def hasLiveBuildId(self):
        return self.isDeterministic()

    def predictLiveBuildId(self):
        if self.__digestSha256:
            return [ bytes.fromhex(self.__digestSha256) ]
        elif self.__digestSha1:
            return [ bytes.fromhex(self.__digestSha1) ]
        else:
            return [None]

    def calcLiveBuildId(self, workspacePath):
        return self.predictLiveBuildId()

    def getLiveBuildIdSpec(self, workspacePath):
        if self.__digestSha256:
            return [ "=" + self.__digestSha256 ]
        elif self.__digestSha1:
            return [ "=" + self.__digestSha1 ]
        else:
            return [None]


class UrlAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'url',
        'dir' : str,
        'digest' : {
            'algorithm' : 'sha1',
            'value' : str
        }
    })

    def _scanDir(self, workspace, dir):
        self.__dir = dir
        self.__hash = asHexStr(hashFile(os.path.join(workspace, dir)))

    def _load(self, data):
        self.__dir = data["dir"]
        self.__hash = data["digest"]["value"]

    def dump(self):
        return {
            "type" : "url",
            "dir" : self.__dir,
            "digest" : {
                "algorithm" : "sha1",
                "value" : self.__hash
            }
        }
