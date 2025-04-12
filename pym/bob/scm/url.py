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
from abc import abstractmethod
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
    return os.stat(update).st_mtime_ns > os.stat(existing).st_mtime_ns

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

    # Do tilde expansion on all platforms. If not applicable or if it fails the
    # url is left unchanged.
    url = os.path.expanduser(url)

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

RIGHT_TO_VAL = { 'r' : 4, 'w' : 2, 'x' : 1 }
SUBJECT_TO_SHIFT = { 'u' : 6, 'g' : 3, 'o' : 0 }

def parseMode(mode):
    if isinstance(mode, int):
        return mode
    if not isinstance(mode, str):
        raise ValueError("mode must be a string or integer")
    ret = 0
    for m in mode.split(","):
        subject, right = m.split("=")
        num_right = 0
        for r in right: num_right |= RIGHT_TO_VAL[r]
        for s in subject:
            ret &= ~(7 << SUBJECT_TO_SHIFT[s])
            ret |= num_right << SUBJECT_TO_SHIFT[s]
    return ret

def dumpMode(mode):
    if mode is None:
        return None

    ret = []
    for s in "ugo":
        m = mode >> SUBJECT_TO_SHIFT[s]
        if m & 7:
            entry = s + "="
            for i in "rwx":
                if m & RIGHT_TO_VAL[i]:
                    entry += i
            ret.append(entry)
    return ",".join(ret)

isWin32 = sys.platform == "win32"


class Extractor():
    SUPPORT_STRIP = False

    def __init__(self, dir, file, strip, separateDownload):
        if strip != 0 and not self.SUPPORT_STRIP:
            raise BuildError("Extractor does not support 'stripComponents'!")
        self.dir = dir
        self.file = file
        self.strip = strip
        self.separateDownload = separateDownload

    async def _extract(self, cmds, invoker, dirCreated, stdout=None):
        destination = self.getCompressedFilePath(invoker)
        (destDir, destFile) = os.path.split(destination)
        canary = os.path.join(destDir, "."+destFile+".extracted")
        if dirCreated or isYounger(destination, canary):
            for cmd in cmds:
                if shutil.which(cmd[0]) is None: continue
                await invoker.checkCommand(cmd, cwd=self.dir, stdout=stdout)
                invoker.trace("<touch>", canary)
                with open(canary, "wb") as f:
                    pass
                os.utime(canary)
                break
            else:
                invoker.fail("No suitable extractor found!")

    def getCompressedFilePath(self, invoker):
        downloadFolder = os.path.join(os.pardir, "download") if self.separateDownload else ""
        return os.path.abspath(invoker.joinPath(downloadFolder, self.dir, self.file)) \

    @abstractmethod
    async def extract(self, invoker, dirCreated):
        return False

# Use the Python tar/zip extraction only on Windows. They are slower and in
# case of tarfile broken in certain ways (e.g. tarfile will result in
# different file modes!). But it shouldn't make a difference on Windows.
class TarExtractor(Extractor):
    SUPPORT_STRIP = True

    async def extract(self, invoker, dirCreated):
        cmds = []
        compressedFilePath = self.getCompressedFilePath(invoker)
        if isWin32 and self.strip == 0:
            cmds.append(["python", "-m", "tarfile", "-e", compressedFilePath])

        cmd = ["tar", "-x", "--no-same-owner", "--no-same-permissions",
                   "-f", compressedFilePath]
        if self.strip > 0:
            cmd.append("--strip-components={}".format(self.strip))
        cmds.append(cmd)

        await self._extract(cmds, invoker, dirCreated)


class ZipExtractor(Extractor):

    async def extract(self, invoker, dirCreated):
        cmds = []
        compressedFilePath = self.getCompressedFilePath(invoker)
        if isWin32:
            cmds.append(["python", "-m", "zipfile",
                   "-e", compressedFilePath, "."])

        cmds.append(["unzip", "-o", compressedFilePath])
        await self._extract(cmds, invoker, dirCreated)


class SingleFileExtractor(Extractor):

    async def extract(self, invoker, dirCreated):
        # The gunzip and unxz tools extracts the file at the location of the
        # input file. In case the separateDownload policy is active, the
        # destination might be in a separete folder.
        src = self.getCompressedFilePath(invoker)
        dst = invoker.joinPath(self.dir, self.file)

        suffix = dst[-4:]
        if self.EXT_IGNORE_CASE:
            suffix = suffix.lower()

        for ext, repl in self.EXT_MAP.items():
            if suffix.endswith(ext):
                dst = dst[:-len(ext)] + repl
                break
        else:
            raise BuildError("unkown suffix")

        with open(dst, 'wb') as f:
            cmd = [self.CMD, "-c", src]
            await self._extract([cmd], invoker, dirCreated, f)

        shutil.copystat(src, dst)


class GZipExtractor(SingleFileExtractor):
    CMD = "gunzip"
    EXT_IGNORE_CASE = True
    EXT_MAP = {
        ".gz" : "",
        "-gz" : "",
        ".z" : "",
        "-z" : "",
        "_z" : "",
        ".tgz" : ".tar",
        ".taz" : ".tar",
    }


class XZExtractor(SingleFileExtractor):
    CMD = "unxz"
    EXT_IGNORE_CASE = False
    EXT_MAP = {
        ".xz" : "",
        ".lzma" : "",
        ".lz" : "",
        ".txz" : ".tar",
        ".tlz" : ".tar",
    }


class SevenZipExtractor(Extractor):

    async def extract(self, invoker, dirCreated):
        cmds = [["7z", "x", "-y", self.getCompressedFilePath(invoker)]]
        await self._extract(cmds, invoker, dirCreated)


class UrlScm(Scm):

    __DEFAULTS = {
        schema.Optional('extract') : schema.Or(bool, str),
        schema.Optional('fileName') : str,
        schema.Optional('stripComponents') : int,
        schema.Optional('sslVerify') : bool,
        schema.Optional('retries') : schema.And(int, lambda n: n >= 0, error="Invalid retries attribute"),
        schema.Optional('fileMode') : schema.And(schema.Use(parseMode), lambda m: m & 0o777, error="Invalid fileMode attr"),
    }

    __SCHEMA = {
        'scm' : 'url',
        'url' : str,
        schema.Optional('digestSHA1') : str,
        schema.Optional('digestSHA256') : str,
        schema.Optional('digestSHA512') : str,
    }

    DEFAULTS = {
        **__DEFAULTS,
        schema.Optional('dir') : str,
    }

    SCHEMA = schema.Schema({
        **__SCHEMA,
        **DEFAULTS,
        schema.Optional('if') : schema.Or(str, IfExpression),
    })

    # Layers have no "dir" and no "if"
    LAYERS_SCHEMA = schema.Schema({
        **__SCHEMA,
        **__DEFAULTS,
    })

    MIRRORS_SCHEMA = schema.Schema({
        'scm' : 'url',
        'url' : re.compile,
        'mirror' : str,
        schema.Optional('upload') : bool,
    })

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

    EXTRACTORS = {
        "tar"  : TarExtractor,
        "gzip" : GZipExtractor,
        "xz"   : XZExtractor,
        "7z"   : SevenZipExtractor,
        "zip"  : ZipExtractor,
    }

    DEFAULT_VALUES = {
        "dir" : ".",
        "extract" : "auto",
        "stripComponents" : 0,
        "sslVerify" : True,
    }

    def __init__(self, spec, overrides=[], stripUser=None,
                 preMirrors=[], fallbackMirrors=[], defaultFileMode=None,
                 separateDownload=False):
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
        self.__strip = spec.get("stripComponents", 0)
        self.__sslVerify = spec.get('sslVerify', True)
        self.__stripUser = stripUser
        self.__retries = spec.get("retries", 0)
        self.__preMirrors = preMirrors
        self.__fallbackMirrors = fallbackMirrors
        self.__preMirrorsUrls = spec.get("preMirrors")
        self.__preMirrorsUpload = spec.get("__preMirrorsUpload")
        self.__fallbackMirrorsUrls = spec.get("fallbackMirrors")
        self.__fallbackMirrorsUpload = spec.get("__fallbackMirrorsUpload")
        self.__fileMode = spec.get("fileMode", 0o600 if defaultFileMode else None)
        self.__separateDownload = spec.get("__separateDownload", separateDownload)

    def getProperties(self, isJenkins, pretty=False):
        ret = super().getProperties(isJenkins, pretty)
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
            'preMirrors' : self.__getPreMirrorsUrls(),
            'fallbackMirrors' : self.__getFallbackMirrorsUrls(),
            'fileMode' : dumpMode(self.__fileMode) if pretty else self.__fileMode,
        })
        if not pretty:
            ret.update({
                '__preMirrorsUpload' : self.__getPreMirrorsUpload(),
                '__fallbackMirrorsUpload' : self.__getFallbackMirrorsUpload(),
                '__separateDownload': self.__separateDownload,
            })

        if pretty:
            ret = { k : v for k, v in ret.items()
                    if v is not None and v != self.DEFAULT_VALUES.get(k) }

        return ret

    def __applyMirrors(self, mirrors):
        if not self.isDeterministic():
            return []

        return [ (re.sub(m['url'], m['mirror'], self.__url), m.get('upload', False))
                 for m in mirrors
                 if m['scm'] == 'url' and re.match(m['url'], self.__url) ]

    def __getPreMirrorsUrls(self):
        ret = self.__preMirrorsUrls
        if ret is None:
            ret = self.__preMirrorsUrls = [ m[0] for m in self.__applyMirrors(self.__preMirrors) ]
        return ret

    def __getPreMirrorsUpload(self):
        ret = self.__preMirrorsUpload
        if ret is None:
            ret = self.__preMirrorsUpload = [ m[1] for m in self.__applyMirrors(self.__preMirrors) ]
        return ret

    def __getFallbackMirrorsUrls(self):
        ret = self.__fallbackMirrorsUrls
        if ret is None:
            ret = self.__fallbackMirrorsUrls = [ m[0] for m in self.__applyMirrors(self.__fallbackMirrors) ]
        return ret

    def __getFallbackMirrorsUpload(self):
        ret = self.__fallbackMirrorsUpload
        if ret is None:
            ret = self.__fallbackMirrorsUpload = [ m[1] for m in self.__applyMirrors(self.__fallbackMirrors) ]
        return ret

    def _getCandidateUrls(self, invoker):
        ret = list(zip(self.__getPreMirrorsUrls(), self.__getPreMirrorsUpload())) + \
              [(self.__url, False)] + \
              list(zip(self.__getFallbackMirrorsUrls(), self.__getFallbackMirrorsUpload()))
        try:
            return [ (parseUrl(url), upload) for (url, upload) in ret ]
        except ValueError as e:
            invoker.fail(str(e))

    def _download(self, url, destination, mode):
        headers = {}
        headers["User-Agent"] = "BobBuildTool/{}".format(BOB_VERSION)
        context = None if self.__sslVerify else sslNoVerifyContext()
        if os.path.isfile(destination) and (url.scheme in ["http", "https"]):
            # Try to avoid download if possible
            headers["If-Modified-Since"] = time2HTTPDate(os.stat(destination).st_mtime)

        tmpFileName = None
        req = urllib.request.Request(url=url.geturl(), headers=headers)
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
                        return False, "Response too short: {} < {} (bytes)".format(read, expected)

                # Atomically move file to destination. Set mode to 0600 to
                # retain Bob 0.15 behaviour if no explicit mode was configured.
                os.chmod(tmpFileName, mode or 0o600)
                replacePath(tmpFileName, destination)
                tmpFileName = None

        except urllib.error.HTTPError as e:
            if e.code != 304:
                return False, "HTTP error {}: {}".format(e.code, e.reason)
            else:
                # HTTP 304 Not modifed -> local file up-to-date
                return False, None
        except HTTPException as e:
            return False, "HTTP error: " + str(e)
        finally:
            if tmpFileName is not None:
                os.remove(tmpFileName)
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        return True, None

    async def _fetch(self, invoker, url, workspaceFile, destination, mode):
        if url.scheme in ['', 'file']:
            # Verify that host name is empty or "localhost"
            if url.netloc not in ['', 'localhost']:
                invoker.fail("Bad/unsupported URL: invalid host name: " + url.netloc)
            # Local files: copy only if newer (u), target never is a directory (T)
            try:
                if isYounger(url.path, destination):
                    if os.path.isdir(destination):
                        invoker.fail("Destination", destination, "is an existing directory!")
                    invoker.trace("<cp>", url.path, workspaceFile)
                    with tempfile.TemporaryDirectory(dir=os.path.dirname(destination)) as tmpDir:
                        tmpFile = os.path.join(tmpDir, self.__fn)
                        # Keep mtime when copying. Otherwise we would update
                        # mirrors bacause our file appears newer.
                        shutil.copy2(url.path, tmpFile)
                        if mode is not None: os.chmod(tmpFile, mode)
                        replacePath(tmpFile, destination)
                    return True, None
                else:
                    return False, None
            except OSError as e:
                return False, "Failed to copy: " + str(e)
        elif url.scheme in ["http", "https", "ftp"]:
            retries = self.__retries
            while True:
                invoker.trace("<wget>", url.geturl(), ">",
                        workspaceFile, "retires:", retries)
                try:
                    updated, err = await invoker.runInExecutor(UrlScm._download, self, url, destination, mode)
                    if err:
                        if retries == 0:
                            return False, err
                        else:
                            invoker.warn(err)
                    else:
                        break
                except (concurrent.futures.CancelledError,
                        concurrent.futures.process.BrokenProcessPool):
                    invoker.fail("Download interrupted!")
                retries -= 1
                await asyncio.sleep(3)

            return updated, None
        else:
            invoker.fail("Unsupported URL scheme: " + url.scheme)

    def _upload(self, source, url):
        headers = {}
        headers["User-Agent"] = "BobBuildTool/{}".format(BOB_VERSION)
        context = None if self.__sslVerify else sslNoVerifyContext()

        try:
            # Set default signal handler so that KeyboardInterrupt is raised.
            # Needed to gracefully handle ctrl+c.
            signal.signal(signal.SIGINT, signal.default_int_handler)

            # First check if the file already exists. Only upload if there is a
            # reason to do so.
            req = urllib.request.Request(url=url.geturl(), method='HEAD', headers=headers)
            try:
                with urllib.request.urlopen(req, context=context) as rsp:
                    return None
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    return "{}: HTTP error {}: {}".format(url.geturl(), e.code, e.reason)

            # Ok, HEAD returned a 404. Proceed with upload...
            with open(source, "rb") as f:
                headers["Content-Length"] = f.seek(0, os.SEEK_END)
                f.seek(0)
                req = urllib.request.Request(url=url.geturl(), method='PUT', data=f, headers=headers)
                with urllib.request.urlopen(req, context=context):
                    pass

        except urllib.error.URLError as e:
            return "{}: {}".format(url.geturl(), e.reason)
        except HTTPException as e:
            return "{}: HTTP error: {}".format(url.geturl(), str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

        return None

    async def _put(self, invoker, workspaceFile, source, url):
        if url.scheme in ['', 'file']:
            # Verify that host name is empty or "localhost"
            if url.netloc not in ['', 'localhost']:
                invoker.fail("Bad/unsupported URL: invalid host name: " + url.netloc)
            # Local files: copy only if newer (u), target never is a directory
            # (T). Move atomically to let concurrent other Bob instances see
            # only fully files.
            if isYounger(source, url.path):
                if os.path.isdir(url.path):
                    invoker.fail("Destination", url.path, "is an existing directory!")
                invoker.trace("<cp>", workspaceFile, url.path)
                destDir = os.path.dirname(url.path)
                os.makedirs(destDir, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=destDir) as tmpDir:
                    tmpFileName = os.path.join(tmpDir, os.path.basename(url.path))
                    shutil.copy2(source, tmpFileName)
                    os.replace(tmpFileName, url.path)
        elif url.scheme in ["http", "https"]:
            retries = self.__retries
            while True:
                invoker.trace("<wput>", workspaceFile, ">", url.geturl(), "retires:", retries)
                try:
                    err = await invoker.runInExecutor(UrlScm._upload, self, source, url)
                    if err:
                        if retries == 0:
                            invoker.fail(err)
                    else:
                        break
                except (concurrent.futures.CancelledError,
                        concurrent.futures.process.BrokenProcessPool):
                    invoker.fail("Upload interrupted!")
                retries -= 1
                await asyncio.sleep(3)
        else:
            invoker.fail("Upload not supported for URL scheme: " + url.scheme)

    def canSwitch(self, oldScm):
        diff = self._diffSpec(oldScm)
        if "scm" in diff:
            return False

        if self.__separateDownload != oldScm.__separateDownload:
            return False

        # Filter irrelevant properties
        diff -= { "sslVerify", "retries", 'preMirrors', '__preMirrorsUpload',
                  'fallbackMirrors', '__fallbackMirrorsUpload' }

        # As long as the SCM is deterministic and the hashes have not changed,
        # the concrete URL is irrelevant.
        if self.isDeterministic() and self.__digestSha1   == oldScm.__digestSha1   \
                                  and self.__digestSha256 == oldScm.__digestSha256 \
                                  and self.__digestSha512 == oldScm.__digestSha512:
            diff -= { "url" }

        # Adding, changing or removing hash sums is ok as long as the url stays
        # the same. Changing the fileMode is also trivial.
        return diff.issubset({"digestSHA1", "digestSHA256", "digestSHA512", "fileMode"})

    async def switch(self, invoker, oldScm):
        # Update fileMode here because invoke() does not touch the file if it
        # already exists.
        if self.__fileMode is not None and self.__fileMode != oldScm.__fileMode:
            destination = invoker.joinPath(self.__dir, self.__fn)
            if os.path.isfile(destination):
                os.chmod(destination, self.__fileMode)

        # The real work is done in invoke() below. It will fail if the file
        # does not match.
        return True

    async def invoke(self, invoker, workspaceCreated):
        if not os.path.isdir(invoker.joinPath(self.__dir)):
            os.makedirs(invoker.joinPath(self.__dir), exist_ok=True)
            workspaceCreated = True
        workspaceFile = os.path.join(self.__dir, self.__fn)
        extractor = self.__getExtractor()

        destination = invoker.joinPath(self.__dir, self.__fn)
        if extractor is not None and self.__separateDownload:
            downloadDestination = invoker.joinPath(os.pardir, "download", self.__dir)
            # os.makedirs doc:
            # Note: makedirs() will become confused if the path elements to create include pardir (eg. “..” on UNIX systems).
            # -> use normpath to collaps up-level reference
            os.makedirs(os.path.normpath(downloadDestination), exist_ok=True)
            destination = invoker.joinPath(os.pardir, "download", self.__dir, self.__fn)

        # Download only if necessary
        if not self.isDeterministic() or not os.path.isfile(destination):
            urls = self._getCandidateUrls(invoker)
            mode = self.__fileMode
            if mode is None and any(self.__url.startswith(s) for s in ("http://", "https://", "ftp://")):
                # Set explicit mode of downloaded files to retain Bob 0.15
                # behaviour. This must apply to fetches from local mirrors too.
                mode = 0o600
            err = None
            for url, upload in urls:
                if err:
                    # Output previously failed download attempt as warning
                    invoker.warn(err)
                downloaded, err = await self._fetch(invoker, url, workspaceFile, destination, mode)
                if err is None:
                    break
            else:
                invoker.fail(err)
        else:
            urls = []
            downloaded = False

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

        # Upload to mirrors that requested it. This is only done for stable
        # files, though.
        if downloaded:
            for url, upload in urls:
                if upload:
                    await self._put(invoker, workspaceFile, destination, url)

        # Run optional extractors
        if extractor is not None:
            await extractor.extract(invoker, workspaceCreated)

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir extract" if a SHA checksum was specified.
        Otherwise it is "url dir extract". A "s#" is appended if leading paths
        are stripped where # is the number of stripped elements. Also appended
        is "m<fileMode>" if fileMode is set.
        "sep" is appendend if the archive is not stored in the workspace.
        """
        if self.__stripUser:
            filt = removeUserFromUrl
        else:
            filt = lambda x: x
        return ( self.__digestSha512 or self.__digestSha256 or
                 self.__digestSha1 or filt(self.__url)
               ) + " " + posixpath.join(self.__dir, self.__fn) + " " + str(self.__extract) + \
               ( " s{}".format(self.__strip) if self.__strip > 0 else "" ) + \
               ( " m{}".format(self.__fileMode) if self.__fileMode is not None else "") + \
               ( " sep" if self.__separateDownload else "" )

    def getDirectory(self):
        return self.__dir

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

    def __getExtractor(self):
        extractor = None
        if self.__extract in ["yes", "auto", True]:
            for (ext, tool) in UrlScm.EXTENSIONS:
                if self.__fn.endswith(ext):
                    extractor = UrlScm.EXTRACTORS[tool](self.__dir, self.__fn,
                                                        self.__strip, self.__separateDownload)
                    break
            if extractor is None and self.__extract != "auto":
                raise ParseError("Don't know how to extract '"+self.__fn+"' automatically.")
        elif self.__extract in UrlScm.EXTRACTORS:
            extractor = UrlScm.EXTRACTORS[self.__extract](self.__dir, self.__fn,
                                                          self.__strip, self.__separateDownload)
        elif self.__extract not in ["no", False]:
            raise ParseError("Invalid extract mode: " + self.__extract)
        return extractor

    def postAttic(self, workspace):
        if self.__separateDownload:
            # os.path.exists returns False if os.pardir is in the path -> normalize it
            downloadDestination = os.path.normpath(os.path.join(workspace, os.pardir, "download", self.__dir))
            if os.path.exists(downloadDestination):
                shutil.rmtree(downloadDestination)

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
