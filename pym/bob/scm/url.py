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
from ..utils import hashString
from pipes import quote
import os.path
import re
import schema

class UrlScm:

    SCHEMA = schema.Schema({
        'scm' : 'url',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('digestSHA1') : str,
        schema.Optional('digestSHA256') : str,
        schema.Optional('extract') : schema.Or(bool, str),
        schema.Optional('fileName') : str
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
        "tar"  : "tar xf",
        "gzip" : "gunzip -kf",
        "xz"   : "unxz -kf",
        "7z"   : "7z x -y",
        "zip"  : "unzip -o",
    }

    def __init__(self, spec):
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

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'url',
            'url' : self.__url,
            'digestSHA1' : self.__digestSha1,
            'digestSHA256' : self.__digestSha256,
            'dir' : self.__dir,
            'fileName' : self.__fn,
            'extract' : self.__extract
        }]

    def asScript(self):
        ret = """
mkdir -p {DIR}
cd {DIR}
if [ -e {FILE} ] ; then
    curl -sSgL -o {FILE} -z {FILE} {URL}
else
    (
        F=$(mktemp)
        trap 'rm -f $F' EXIT
        set -e
        curl -sSgL -o $F {URL}
        mv $F {FILE}
    )
fi
""".format(DIR=quote(self.__dir), URL=quote(self.__url), FILE=quote(self.__fn))

        if self.__digestSha1:
            ret += "echo {DIGEST} {FILE} | sha1sum -c\n".format(DIGEST=self.__digestSha1, FILE=self.__fn)
        if self.__digestSha256:
            ret += "echo {DIGEST} {FILE} | sha256sum -c\n".format(DIGEST=self.__digestSha256, FILE=self.__fn)

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
            ret += """
if [ {FILE} -nt .{FILE}.extracted ] ; then
    {TOOL} {FILE}
    touch .{FILE}.extracted
fi
""".format(FILE=quote(self.__fn), TOOL=extractor)

        return ret

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir extract" if a SHA checksum was specified.
        Otherwise it is "url dir extract".
        """
        return ( self.__digestSha256 if self.__digestSha256
                 else (self.__digestSha1 if self.__digestSha1 else self.__url)
                    ) + " " + os.path.join(self.__dir, self.__fn) + " " + str(self.__extract)

    def merge(self, other):
        return False

    def getDirectories(self):
        fn = os.path.join(self.__dir, self.__fn)
        return { fn : hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return (self.__digestSha1 is not None) or (self.__digestSha256 is not None)

    def hasJenkinsPlugin(self):
        return False

    def status(self, workspacePath, dir, verbose = 0):
        return 'clean'

