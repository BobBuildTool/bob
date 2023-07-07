# Bob build tool
# Copyright (C) 2016-2020 The BobBuildTool Contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .. import BOB_VERSION
from ..errors import BuildError, ParseError
from ..stringparser import IfExpression
from ..utils import asHexStr, hashFile, removeUserFromUrl, sslNoVerifyContext, \
        replacePath
from .scm import Scm, ScmAudit
from http.client import HTTPException
import asyncio
import concurrent.futures.process
import contextlib
import hashlib
import os, os.path
import posixpath
import re
import schema
import shutil
import signal
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


WEEKDAYNAME = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
MONTHNAME = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep',
    'Oct', 'Nov', 'Dec']

def isYounger(update, existing):
    if not os.path.exists(existing): return True
    return os.stat(update).st_mtime > os.stat(existing).st_mtime

def time2HTTPDate(timestamp):
    """Return the timestamp formatted as RFC7231 Section 7.1.1.1 conformant string."""
    year, month, day, hh, mm, ss, wd, y, z = time.gmtime(timestamp)
    return "%s, %02d %3s %4d %02d:%02d:%02d GMT" % (WEEKDAYNAME[wd], day,
        MONTHNAME[month-1], year, hh, mm, ss)

def startsWithDrive(url):
    if len(url) < 2: return False
    if url[1] != ':': return False
    return ((url[0] >= 'A') and (url[0] <= 'Z')) or ((url[0] >= 'a') and (url[0] <= 'z'))

def parseUrl(url):
    r"""Parse URL and apply Windows quirks.

    On Windows we allow the user to specify the following paths as URLs. They
    are no valid URLs but we still accept them. cURL will work on them too
    (plus many others)...

     * C:\foo.bar
     * C:/foo.bar
     * file:///C:/foo.bar
     * file:///C:\foo.bar
     * \\server\path
     * file:///\\server\path

    We explicitly do not allow relative or absolute paths. Only fully qualified
    paths are accepted. See
    https://docs.microsoft.com/en-us/windows/win32/fileio/naming-a-file for
    more information. The following paths are rejected:

     * C:tmp.txt
     * tmp.txt
     * \tmp.txt
     * /tmp.txt
     * file:///tmp.txt
     * file:///C:tmp.txt
     * file:///\tmp.txt
    """

    # Only require some special processing on Windows. Unix URLs just work...
    if sys.platform != "win32":
        return urllib.parse.urlparse(url)

    # Does it start with a drive letter like "C:…"?
    if startsWithDrive(url):
        # Convert slashes to backslashes and make sure it's a fully qualified
        # path.
        path = url.replace('/', '\\')
        if not path[2:].startswith("\\"):
            raise ValueError("URL must be a fully qualified path name")
        return urllib.parse.ParseResult('file', '', path, '', '', '')
    elif url.startswith("\\\\"):
        # No slash conversion on UNC paths!
        return urllib.parse.ParseResult('file', '', url, '', '', '')

    # Regular parsing. Bail out if it is not a local path.
    url = urllib.parse.urlparse(url)
    if url.scheme == '':
        raise ValueError("Neither a fully qualified path nor a URL scheme given")
    if url.scheme != 'file':
        return url

    # The host part is checked in the generic code. We just take care of the
    # path here.
    if not url.path.startswith("/"):
        raise ValueError("Invalid path")

    path = url.path[1:]
    if path.startswith("\\\\"):
        pass
    elif startsWithDrive(path) and (path[2:].startswith("\\") or path[2:].startswith("/")):
        path = path.replace('/', '\\')
    else:
        raise ValueError("URL must be a fully qualified path name")

    # looks legit
    return urllib.parse.ParseResult(url.scheme, url.netloc,path, '', '', '')


isWin32 = sys.platform == "win32"

class UrlScm(Scm):

    DEFAULTS = {
        schema.Optional('extract') : schema.Or(bool, str),
        schema.Optional('fileName') : str,
        schema.Optional('stripComponents') : int,
        schema.Optional('sslVerify') : bool,
        schema.Optional('retries') : schema.And(int, lambda n: n >= 0, error="Invalid retries attribute"),
    }

    __SCHEMA = {
        'scm' : 'url',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : schema.Or(str, IfExpression),
        schema.Optional('digestSHA1') : str,
        schema.Optional('digestSHA256') : str,
        schema.Optional('digestSHA512') : str,
    }

    SCHEMA = schema.Schema({**__SCHEMA, **DEFAULTS})

    EXTENSIONS = [
        (".tar.gz",    "tar"),
        (".tar.xz",    "tar"),
        (".tar.bz2",   "tar"),
        (".tar.bzip2", "tar"),
        (".tgz",       "tar"),
        (".txz",       "tar"),
        (".tar",       "tar"),
        (".gz",        "gzip"),
        (".xz",        "xz"),
        (".7z",        "7z"),
        (".zip",       "zip"),
    ]

    # Use the Python tar/zip extraction only on Windows. They are slower and in
    # case of tarfile broken in certain ways (e.g. tarfile will result in
    # different file modes!). But it shouldn't make a difference on Windows.
    EXTRACTORS = {
        "tar"  : [
            (isWin32, "python", ["-m", "tarfile", "-e", "{}"], None),
            (True, "tar", ["-x", "--no-same-owner", "--no-same-permissions", "-f", "{}"], "--strip-components={}"),
        ],
        "gzip" : [
            (True, "gunzip", ["-kf", "{}"], None),
        ],
        "xz" : [
            (True, "unxz", ["-kf", "{}"], None),
        ],
        "7z" : [
            (True, "7z", ["x", "-y", "{}"], None),
        ],
        "zip" : [
            (isWin32, "python", ["-m", "zipfile", "-e", "{}", "."], None),
            (True, "unzip", ["-o", "{}"], None),
        ],
    }

    def __init__(self, spec, overrides=[], tidy=None, stripUser=None):
        super().__init__(spec, overrides)
        self.__url = spec["url"]
        self.__digestSha1 = spec.get("digestSHA1")
        if self.__digestSha1:
            # validate digest
            if re.match("^[0-9a-f]{40}$", self.__digestSha1) is None:
                raise ParseError("Invalid SHA1 digest: '" + str(self.__digestSha1) + "'")
        self.__digestSha256 = spec.get("digestSHA256")
        if self.__digestSha256:
            # validate digest
            if re.match("^[0-9a-f]{64}$", self.__digestSha256) is None:
                raise ParseError("Invalid SHA256 digest: '" + str(self.__digestSha256) + "'")
        self.__digestSha512 = spec.get("digestSHA512")
        if self.__digestSha512:
            # validate digest
            if re.match("^[0-9a-f]{128}$", self.__digestSha512) is None:
                raise ParseError("Invalid SHA512 digest: '" + str(self.__digestSha512) + "'")
        self.__dir = spec.get("dir", ".")
        self.__fn = spec.get("fileName")
        if not self.__fn:
            url = self.__url
            if sys.platform == "win32":
                # On Windows we're allowed to provide native paths with
                # backslashes.
                url = url.replace('\\', '/')
            self.__fn = url.split("/")[-1]
        self.__extract = spec.get("extract", "auto")
        self.__tidy = tidy
        self.__strip = spec.get("stripComponents", 0)
        self.__sslVerify = spec.get('sslVerify', True)
        self.__stripUser = stripUser
        self.__retries = spec.get("retries", 0)

    def getProperties(self, isJenkins):
        ret = super().getProperties(isJenkins)
        ret.update({
            'scm' : 'url',
            'url' : self.__url,
            'digestSHA1' : self.__digestSha1,
            'digestSHA256' : self.__digestSha256,
            'digestSHA512' : self.__digestSha512,
            'dir' : self.__dir,
            'fileName' : self.__fn,
            'extract' : self.__extract,
            'stripComponents' : self.__strip,
            'sslVerify' : self.__sslVerify,
            'retries' : self.__retries,
        })
        return ret

    def _download(self, destination):
        headers = {}
        headers["User-Agent"] = "BobBuildTool/{}".format(BOB_VERSION)
        context = None if self.__sslVerify else sslNoVerifyContext()
        if os.path.isfile(destination) and self.__url.startswith("http"):
            # Try to avoid download if possible
            headers["If-Modified-Since"] = time2HTTPDate(os.stat(destination).st_mtime)

        tmpFileName = None
        req = urllib.request.Request(url=self.__url, headers=headers)
        try:
            # Set default signal handler so that KeyboardInterrupt is raised.
            # Needed to gracefully handle ctrl+c.
            signal.signal(signal.SIGINT, signal.default_int_handler)

            with contextlib.closing(urllib.request.urlopen(req, context=context)) as rsp:
                with tempfile.NamedTemporaryFile(dir=os.path.dirname(destination), delete=False) as f:
                    tmpFileName = f.name
                    read = 0
                    while True:
                        buf = rsp.read(16384)
                        if not buf:
                            break
                        read += len(buf)
                        f.write(buf)

                if "content-length" in rsp.info():
                    expected = int(rsp.info()["Content-Length"])
                    if expected > read:
                        return "Response too short: {} < {} (bytes)".format(read, expected)

                # Atomically move file to destination. Set explicit mode to
                # retain Bob 0.15 behaviour.
                os.chmod(tmpFileName, stat.S_IREAD|stat.S_IWRITE)
                replacePath(tmpFileName, destination)
                tmpFileName = None

        except urllib.error.HTTPError as e:
            if e.code != 304:
                return "HTTP error {}: {}".format(e.code, e.reason)
        except HTTPException as e:
            return "HTTP error: " + str(e)
        finally:
            if tmpFileName is not None:
                os.remove(tmpFileName)
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        return None

    def canSwitch(self, oldSpec):
        diff = self._diffSpec(oldSpec)

        # Filter irrelevant properties
        diff -= {"sslVerify"}

        # Adding, changing or removing hash sums is ok as long as the url stays
        # the same.
        return diff.issubset({"digestSHA1", "digestSHA256", "digestSHA512"})

    async def switch(self, invoker, oldSpec):
        # The real work is done in invoke() below. It will fail if the file
        # does not match.
        return True

    async def invoke(self, invoker):
        os.makedirs(invoker.joinPath(self.__dir), exist_ok=True)
        workspaceFile = os.path.join(self.__dir, self.__fn)
        destination = invoker.joinPath(self.__dir, self.__fn)

        # Download only if necessary
        if not self.isDeterministic() or not os.path.isfile(destination):
            try:
                url = parseUrl(self.__url)
            except ValueError as e:
                invoker.fail(str(e))

            if url.scheme in ['', 'file']:
                # Verify that host name is empty or "localhost"
                if url.netloc not in ['', 'localhost']:
                    invoker.fail("Bad/unsupported URL: invalid host name: " + url.netloc)
                # Local files: copy only if newer (u), target never is a directory (T)
                if isYounger(url.path, destination):
                    if os.path.isdir(destination):
                        invoker.fail("Destination", destination, "is an existing directory!")
                    invoker.trace("<cp>", url.path, workspaceFile)
                    shutil.copy(url.path, destination)
            elif url.scheme in ["http", "https", "ftp"]:
                retries = self.__retries
                while True:
                    invoker.trace("<wget>", self.__url, ">",
                            workspaceFile, "retires:", retries)
                    try:
                        err = await invoker.runInExecutor(UrlScm._download, self, destination)
                        if err:
                            if retries == 0:
                                invoker.fail(err)
                        else:
                            break
                    except (concurrent.futures.CancelledError,
                            concurrent.futures.process.BrokenProcessPool):
                        invoker.fail("Download interrupted!")
                    retries -= 1
                    await asyncio.sleep(3)
            else:
                invoker.fail("Unsupported URL scheme: " + url.scheme)


        # Always verify file hashes
        if self.__digestSha1:
            invoker.trace("<sha1sum>", workspaceFile)
            d = hashFile(destination, hashlib.sha1).hex()
            if d != self.__digestSha1:
                invoker.fail("SHA1 digest did not match! expected:", self.__digestSha1, "got:", d)
        if self.__digestSha256:
            invoker.trace("<sha256sum>", workspaceFile)
            d = hashFile(destination, hashlib.sha256).hex()
            if d != self.__digestSha256:
                invoker.fail("SHA256 digest did not match! expected:", self.__digestSha256, "got:", d)
        if self.__digestSha512:
            invoker.trace("<sha512sum>", workspaceFile)
            d = hashFile(destination, hashlib.sha512).hex()
            if d != self.__digestSha512:
                invoker.fail("SHA512 digest did not match! expected:", self.__digestSha512, "got:", d)

        # Run optional extractors
        extractors = self.__getExtractors()
        canary = invoker.joinPath(self.__dir, "." + self.__fn + ".extracted")
        if extractors and isYounger(destination, canary):
            for cmd in extractors:
                if shutil.which(cmd[0]) is None: continue
                await invoker.checkCommand(cmd, cwd=self.__dir)
                invoker.trace("<touch>", canary)
                with open(canary, "wb") as f:
                    pass
                os.utime(canary)
                break
            else:
                executor.fail("No suitable extractor found!")

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir extract" if a SHA checksum was specified.
        Otherwise it is "url dir extract". A "s#" is appended if leading paths
        are stripped where # is the number of stripped elements.
        """
        if self.__stripUser:
            filt = removeUserFromUrl
        else:
            filt = lambda x: x
        return ( self.__digestSha512 or self.__digestSha256 or
                 self.__digestSha1 or filt(self.__url)
               ) + " " + posixpath.join(self.__dir, self.__fn) + " " + str(self.__extract) + \
               ( " s{}".format(self.__strip) if self.__strip > 0 else "" )

    def getDirectory(self):
        return self.__dir if self.__tidy else os.path.join(self.__dir, self.__fn)

    def isDeterministic(self):
        return (self.__digestSha1 is not None) or \
               (self.__digestSha256 is not None) or \
               (self.__digestSha512 is not None)

    def getAuditSpec(self):
        return ("url", os.path.join(self.__dir, self.__fn),
                {"url" : self.__url})

    def hasLiveBuildId(self):
        return self.isDeterministic()

    async def predictLiveBuildId(self, step):
        return self.calcLiveBuildId(None)

    def calcLiveBuildId(self, workspacePath):
        if self.__digestSha512:
            return bytes.fromhex(self.__digestSha512)
        elif self.__digestSha256:
            return bytes.fromhex(self.__digestSha256)
        elif self.__digestSha1:
            return bytes.fromhex(self.__digestSha1)
        else:
            return None

    def __getExtractors(self):
        extractors = None
        if self.__extract in ["yes", "auto", True]:
            for (ext, tool) in UrlScm.EXTENSIONS:
                if self.__fn.endswith(ext):
                    extractors = UrlScm.EXTRACTORS[tool]
                    break
            if not extractors and self.__extract != "auto":
                raise ParseError("Don't know how to extract '"+self.__fn+"' automatically.")
        elif self.__extract in UrlScm.EXTRACTORS:
            extractors = UrlScm.EXTRACTORS[self.__extract]
        elif self.__extract not in ["no", False]:
            raise ParseError("Invalid extract mode: " + self.__extract)

        if extractors is None:
            return []

        ret = []
        for extractor in extractors:
            if not extractor[0]: continue
            if self.__strip > 0:
                if extractor[3] is None:
                    continue
                strip = [extractor[3].format(self.__strip)]
            else:
                strip = []
            ret.append([extractor[1]] + [a.format(self.__fn) for a in extractor[2]] + strip)

        if not ret:
            raise ParseError("Extractor does not support 'stripComponents'!")

        return ret


class UrlAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'url',
        'dir' : str,
        'digest' : {
            'algorithm' : 'sha1',
            'value' : str
        },
        schema.Optional('url') : str, # Added in Bob 0.16
    })

    async def _scanDir(self, workspace, dir, extra):
        self.__dir = dir
        self.__hash = asHexStr(hashFile(os.path.join(workspace, dir)))
        self.__url = extra.get("url")

    def _load(self, data):
        self.__dir = data["dir"]
        self.__hash = data["digest"]["value"]
        self.__url = data.get("url")

    def dump(self):
        ret = {
            "type" : "url",
            "dir" : self.__dir,
            "digest" : {
                "algorithm" : "sha1",
                "value" : self.__hash
            }
        }
        if self.__url is not None:
            ret["url"] = self.__url

        return ret
