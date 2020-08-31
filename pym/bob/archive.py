# Bob build tool
# Copyright (C) 2016-2020 The BobBuildTool Contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Binary archive handling module.

Every backend is expected to implement the following behaviour:

Downloads will almost never throw a fatal error. It should make no difference
if an artifact was not found or could not be read. Bob should move on to the
next backend or build the package. The only exception is when an artifact could
be found and there are errors during extraction (e.g. some sanity checks fail,
corrupted archive, no space left, ...).

Uploads are supposed to throw a BuildError when something goes wrong unless the
'nofail' option is set. If the artifact is already in the artifact repository
it must not be overwritten. The backend should make sure that even on
concurrent uploads the artifact must appear atomically for unrelated readers.
"""

from . import BOB_VERSION
from .errors import BuildError
from .tty import stepAction, stepMessage, \
    SKIPPED, EXECUTED, WARNING, INFO, TRACE, ERROR, IMPORTANT
from .utils import asHexStr, removePath, isWindows
from shlex import quote
from tempfile import mkstemp, NamedTemporaryFile, TemporaryFile
import argparse
import asyncio
import base64
import concurrent.futures
import concurrent.futures.process
import gzip
import hashlib
import http.client
import os
import os.path
import signal
import ssl
import subprocess
import tarfile
import textwrap
import urllib.parse

ARCHIVE_GENERATION = '-1'
ARTIFACT_SUFFIX = ".tgz"
BUILDID_SUFFIX = ".buildid"
FINGERPRINT_SUFFIX = ".fprnt"

def buildIdToName(bid):
    return asHexStr(bid) + ARCHIVE_GENERATION

def readFileOrHandle(name, fileobj):
    if fileobj is not None:
        return fileobj.read()
    with open(name, "rb") as f:
        return f.read()

def writeFileOrHandle(name, fileobj, content):
    if fileobj is not None:
        fileobj.write(content)
        return
    with open(name, "wb") as f:
        f.write(content)


class DummyArchive:
    """Archive that does nothing"""

    def wantDownload(self, enable):
        pass

    def wantUpload(self, enable):
        pass

    def canDownloadLocal(self):
        return False

    def canUploadLocal(self):
        return False

    def canDownloadJenkins(self):
        return False

    def canUploadJenkins(self):
        return False

    async def uploadPackage(self, step, buildId, audit, content):
        pass

    async def downloadPackage(self, step, buildId, audit, content):
        return False

    def upload(self, step, buildIdFile, tgzFile):
        return ""

    def download(self, step, buildIdFile, tgzFile):
        return ""

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId):
        pass

    async def downloadLocalLiveBuildId(self, step, liveBuildId):
        return None

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return ""

    async def uploadLocalFingerprint(self, step, key, fingerprint):
        pass

    async def downloadLocalFingerprint(self, step, key):
        return None

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return ""

class ArtifactNotFoundError(Exception):
    pass

class ArtifactExistsError(Exception):
    pass

class ArtifactDownloadError(Exception):
    def __init__(self, reason):
        self.reason = reason

class ArtifactUploadError(Exception):
    def __init__(self, reason):
        self.reason = reason

class BaseArchive:
    def __init__(self, spec):
        flags = spec.get("flags", ["upload", "download"])
        self.__useDownload = "download" in flags
        self.__useUpload = "upload" in flags
        self.__ignoreErrors = "nofail" in flags
        self.__useLocal = "nolocal" not in flags
        self.__useJenkins = "nojenkins" not in flags
        self.__wantDownload = False
        self.__wantUpload = False

    def _ignoreErrors(self):
        return self.__ignoreErrors

    def wantDownload(self, enable):
        self.__wantDownload = enable

    def wantUpload(self, enable):
        self.__wantUpload = enable

    def canDownloadLocal(self):
        return self.__wantDownload and self.__useDownload and self.__useLocal

    def canUploadLocal(self):
        return self.__wantUpload and self.__useUpload and self.__useLocal

    def canDownloadJenkins(self):
        return self.__wantDownload and self.__useDownload and self.__useJenkins

    def canUploadJenkins(self):
        return self.__wantUpload and self.__useUpload and self.__useJenkins

    def __extractPackage(self, tar, audit, content):
        if tar.pax_headers.get('bob-archive-vsn', "0") != "1":
            raise BuildError("Unsupported binary artifact")

        f = tar.next()
        while f is not None:
            if f.name.startswith("content/"):
                if f.islnk():
                    if not f.linkname.startswith("content/"):
                        raise BuildError("invalid hard link in archive: '{}' -> '{}'"
                                            .format(f.name, f.linkname))
                    f.linkname = f.linkname[8:]
                f.name = f.name[8:]
                try:
                    tar.extract(f, content)
                except UnicodeError:
                    raise BuildError("File name encoding error while extracting '{}'".format(f.name),
                                     help="Your locale(7) probably does not (fully) support unicode.")
            elif f.name == "meta/audit.json.gz":
                f.name = audit
                tar.extract(f)
            elif f.name == "content" or f.name == "meta":
                pass
            else:
                raise BuildError("Binary artifact contained unknown file: " + f.name)
            f = tar.next()

    def _openDownloadFile(self, buildId, suffix):
        raise ArtifactNotFoundError()

    async def downloadPackage(self, step, buildId, audit, content):
        if not self.canDownloadLocal():
            return False

        loop = asyncio.get_event_loop()
        suffix = ARTIFACT_SUFFIX
        details = " from {}".format(self._remoteName(buildId, suffix))
        with stepAction(step, "DOWNLOAD", content, details=details) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(None, BaseArchive._downloadPackage,
                    self, buildId, suffix, audit, content)
                if not ret: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Download of package interrupted.")

    def _downloadPackage(self, buildId, suffix, audit, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openDownloadFile(buildId, suffix) as (name, fileobj):
                with tarfile.open(name, "r|*", fileobj=fileobj, errorlevel=1) as tar:
                    removePath(audit)
                    removePath(content)
                    os.makedirs(content)
                    self.__extractPackage(tar, audit, content)
            return (True, None, None)
        except ArtifactNotFoundError:
            return (False, "not found", WARNING)
        except ArtifactDownloadError as e:
            return (False, e.reason, WARNING)
        except BuildError as e:
            raise
        except OSError as e:
            raise BuildError("Cannot download artifact: " + str(e))
        except tarfile.TarError as e:
            raise BuildError("Error extracting binary artifact: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    async def downloadLocalLiveBuildId(self, step, liveBuildId):
        if not self.canDownloadLocal():
            return None

        loop = asyncio.get_event_loop()
        with stepAction(step, "MAP-SRC", self._remoteName(liveBuildId, BUILDID_SUFFIX), (INFO,TRACE)) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(None,
                    BaseArchive._downloadLocalFile, self, liveBuildId, BUILDID_SUFFIX)
                if ret is None: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Download of build-id interrupted.")

    def _downloadLocalFile(self, key, suffix):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openDownloadFile(key, suffix) as (name, fileobj):
                ret = readFileOrHandle(name, fileobj)
            return (ret, None, None)
        except ArtifactNotFoundError:
            return (None, "not found", WARNING)
        except ArtifactDownloadError as e:
            return (None, e.reason, WARNING)
        except BuildError as e:
            raise
        except OSError as e:
            raise BuildError("Cannot download file: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    def _openUploadFile(self, buildId, suffix):
        raise ArtifactUploadError("not implemented")

    async def uploadPackage(self, step, buildId, audit, content):
        if not self.canUploadLocal():
            return
        if not audit:
            stepMessage(step, "UPLOAD", "skipped (no audit trail)", SKIPPED,
                IMPORTANT)
            return

        loop = asyncio.get_event_loop()
        suffix = ARTIFACT_SUFFIX
        details = " to {}".format(self._remoteName(buildId, suffix))
        with stepAction(step, "UPLOAD", content, details=details) as a:
            try:
                msg, kind = await loop.run_in_executor(None, BaseArchive._uploadPackage,
                    self, buildId, suffix, audit, content)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Upload of package interrupted.")

    def _uploadPackage(self, buildId, suffix, audit, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openUploadFile(buildId, suffix) as (name, fileobj):
                pax = { 'bob-archive-vsn' : "1" }
                with gzip.open(name or fileobj, 'wb', 6) as gzf:
                    with tarfile.open(name, "w", fileobj=gzf,
                                      format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
                        tar.add(audit, "meta/" + os.path.basename(audit))
                        tar.add(content, arcname="content")
        except ArtifactExistsError:
            return ("skipped ({} exists in archive)".format(content), SKIPPED)
        except (ArtifactUploadError, tarfile.TarError, OSError) as e:
            if self.__ignoreErrors:
                return ("error ("+str(e)+")", ERROR)
            else:
                raise BuildError("Cannot upload artifact: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        return ("ok", EXECUTED)

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId):
        if not self.canUploadLocal():
            return

        loop = asyncio.get_event_loop()
        with stepAction(step, "CACHE-BID", self._remoteName(liveBuildId, BUILDID_SUFFIX), (INFO,TRACE)) as a:
            try:
                msg, kind = await loop.run_in_executor(None, BaseArchive._uploadLocalFile, self, liveBuildId, BUILDID_SUFFIX, buildId)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Upload of build-id interrupted.")

    def _uploadLocalFile(self, key, suffix, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openUploadFile(key, suffix) as (name, fileobj):
                writeFileOrHandle(name, fileobj, content)
        except ArtifactExistsError:
            return ("skipped (exists in archive)", SKIPPED)
        except (ArtifactUploadError, OSError) as e:
            if self.__ignoreErrors:
                return ("error ("+str(e)+")", ERROR)
            else:
                raise BuildError("Cannot upload file: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        return ("ok", EXECUTED)

    async def uploadLocalFingerprint(self, step, key, fingerprint):
        if not self.canUploadLocal():
            return

        loop = asyncio.get_event_loop()
        with stepAction(step, "CACHE-FPR", self._remoteName(key, FINGERPRINT_SUFFIX)) as a:
            try:
                msg, kind = await loop.run_in_executor(None, BaseArchive._uploadLocalFile, self, key, FINGERPRINT_SUFFIX, fingerprint)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Upload of build-id interrupted.")

    async def downloadLocalFingerprint(self, step, key):
        if not self.canDownloadLocal():
            return None

        loop = asyncio.get_event_loop()
        with stepAction(step, "MAP-FPRNT", self._remoteName(key, FINGERPRINT_SUFFIX)) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(None,
                    BaseArchive._downloadLocalFile, self, key, FINGERPRINT_SUFFIX)
                if ret is None: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Download of fingerprint interrupted.")


class LocalArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__basePath = os.path.abspath(spec["path"])
        self.__fileMode = spec.get("fileMode")
        self.__dirMode = spec.get("directoryMode")

    def _getPath(self, buildId, suffix):
        packageResultId = buildIdToName(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + suffix
        return (packageResultPath, packageResultFile)

    def _remoteName(self, buildId, suffix):
        return self._getPath(buildId, suffix)[1]

    def _openDownloadFile(self, buildId, suffix):
        (packageResultPath, packageResultFile) = self._getPath(buildId, suffix)
        if os.path.isfile(packageResultFile):
            return LocalArchiveDownloader(packageResultFile)
        else:
            raise ArtifactNotFoundError()

    def _openUploadFile(self, buildId, suffix):
        (packageResultPath, packageResultFile) = self._getPath(buildId, suffix)
        if os.path.isfile(packageResultFile):
            raise ArtifactExistsError()

        # open temporary file in destination directory
        if not os.path.isdir(packageResultPath):
            if self.__dirMode is not None:
                oldMask = os.umask(~self.__dirMode & 0o777)
            try:
                os.makedirs(packageResultPath, exist_ok=True)
            finally:
                if self.__dirMode is not None:
                    os.umask(oldMask)
        return LocalArchiveUploader(
            NamedTemporaryFile(dir=packageResultPath, delete=False),
            self.__fileMode,
            packageResultFile)

    def __uploadJenkins(self, step, buildIdFile, resultFile, suffix):
        """Generate upload shell script.

        We cannot simply copy the artifact to the final location as this is not
        atomic. Instead we create a temporary file at the repository root, copy
        the artifact there and hard-link the temporary file at the final
        location. If the link fails it is usually caused by a concurrent
        upload. Test that the artifact is readable in this case to distinguish
        it from other fatal errors.
        """
        if not self.canUploadJenkins():
            return ""

        if self.__dirMode is not None:
            setDirMode = "umask {:03o}".format(~self.__dirMode & 0o777)
        else:
            setDirMode = ":"

        if self.__fileMode is not None:
            setFileMode = 'chmod {:03o} "$T"'.format(self.__fileMode & 0o777)
        else:
            setFileMode = ""

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_UPLOAD_FILE={DIR}"/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            if [[ ! -e ${{BOB_UPLOAD_FILE}} ]] ; then
                (
                    set -eE
                    T="$(mktemp -p {DIR})"
                    trap 'rm -f $T' EXIT
                    cp {RESULT} "$T"
                    {SET_FILE_MODE}
                    ({SET_DIR_MODE} ; mkdir -p "${{BOB_UPLOAD_FILE%/*}}")
                    if ! ln -T "$T" "$BOB_UPLOAD_FILE" ; then
                        [[ -r "$BOB_UPLOAD_FILE" ]] || exit 2
                    fi
                ){FIXUP}
            fi""".format(DIR=quote(self.__basePath), BUILDID=quote(buildIdFile), RESULT=quote(resultFile),
                         FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                         GEN=ARCHIVE_GENERATION, SUFFIX=suffix,
                         SET_DIR_MODE=setDirMode, SET_FILE_MODE=setFileMode))

    def upload(self, step, buildIdFile, tgzFile):
        return self.__uploadJenkins(step, buildIdFile, tgzFile, ARTIFACT_SUFFIX)

    def download(self, step, buildIdFile, tgzFile):
        if not self.canDownloadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
                BOB_DOWNLOAD_FILE={DIR}"/${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}{SUFFIX}"
                cp "$BOB_DOWNLOAD_FILE" {RESULT} || echo Download failed: $?
            fi
            """.format(DIR=quote(self.__basePath), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX))

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return self.__uploadJenkins(step, keyFile, fingerprintFile, FINGERPRINT_SUFFIX)

class LocalArchiveDownloader:
    def __init__(self, name):
        try:
            self.fd = open(name, "rb")
        except OSError as e:
            raise ArtifactDownloadError(str(e))
    def __enter__(self):
        return (None, self.fd)
    def __exit__(self, exc_type, exc_value, traceback):
        self.fd.close()
        return False

class LocalArchiveUploader:
    def __init__(self, tmp, fileMode, destination):
        self.tmp = tmp
        self.fileMode = fileMode
        self.destination = destination
    def __enter__(self):
        return (None, self.tmp)
    def __exit__(self, exc_type, exc_value, traceback):
        self.tmp.close()
        # atomically move file to destination at end of upload
        if exc_type is None:
            if not isWindows():
                if self.fileMode is not None:
                    os.chmod(self.tmp.name, self.fileMode)
                # Cannot use os.rename() because it will unconditionally
                # replace an existing file. Instead we link the file at the
                # destination and unlink the temporary file.
                try:
                    os.link(self.tmp.name, self.destination)
                except FileExistsError:
                    pass # lost race
                finally:
                    os.unlink(self.tmp.name)
            else:
                try:
                    os.rename(self.tmp.name, self.destination)
                except OSError:
                    os.remove(self.tmp.name) # lost race
        else:
            os.unlink(self.tmp.name)
        return False


class SimpleHttpArchive(BaseArchive):
    def __init__(self, spec, secureSSL):
        super().__init__(spec)
        self.__url = urllib.parse.urlparse(spec["url"])
        self.__connection = None
        self.__sslVerify = spec.get("sslVerify", secureSSL)

    def __retry(self, request):
        retry = True
        while True:
            try:
                return (True, request())
            except (http.client.HTTPException, OSError) as e:
                self._resetConnection()
                if not retry: return (False, e)
                retry = False

    def _makeUrl(self, buildId, suffix):
        packageResultId = buildIdToName(buildId)
        return "/".join([self.__url.path, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + suffix])

    def _remoteName(self, buildId, suffix):
        url = self.__url
        return urllib.parse.urlunparse((url.scheme, url.netloc, self._makeUrl(buildId, suffix), '', '', ''))

    def _getConnection(self):
        if self.__connection is not None:
            return self.__connection

        url = self.__url
        if url.scheme == 'http':
            connection = http.client.HTTPConnection(url.hostname, url.port)
        elif url.scheme == 'https':
            ctx = None if self.__sslVerify else ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            connection = http.client.HTTPSConnection(url.hostname, url.port,
                                                     context=ctx)
        else:
            raise BuildError("Unsupported URL scheme: '{}'".format(url.schema))

        self.__connection = connection
        return connection

    def _resetConnection(self):
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None

    def _getHeaders(self):
        headers = { 'User-Agent' : 'BobBuildTool/{}'.format(BOB_VERSION) }
        if self.__url.username is not None:
            username = urllib.parse.unquote(self.__url.username)
            passwd = urllib.parse.unquote(self.__url.password)
            userPass = username + ":" + passwd
            headers['Authorization'] = 'Basic ' + base64.b64encode(
                userPass.encode("utf-8")).decode("ascii")
        return headers

    def _openDownloadFile(self, buildId, suffix):
        (ok, result) = self.__retry(lambda: self.__openDownloadFile(buildId, suffix))
        if ok:
            return result
        else:
            raise ArtifactDownloadError(str(result))

    def __openDownloadFile(self, buildId, suffix):
        connection = self._getConnection()
        url = self._makeUrl(buildId, suffix)
        connection.request("GET", url, headers=self._getHeaders())
        response = connection.getresponse()
        if response.status == 200:
            return SimpleHttpDownloader(self, response)
        else:
            response.read()
            if response.status == 404:
                raise ArtifactNotFoundError()
            else:
                raise ArtifactDownloadError("{} {}".format(response.status,
                                                           response.reason))

    def _openUploadFile(self, buildId, suffix):
        (ok, result) = self.__retry(lambda: self.__openUploadFile(buildId, suffix))
        if ok:
            return result
        else:
            raise ArtifactUploadError(str(result))

    def __openUploadFile(self, buildId, suffix):
        connection = self._getConnection()
        url = self._makeUrl(buildId, suffix)

        # check if already there
        connection.request("HEAD", url, headers=self._getHeaders())
        response = connection.getresponse()
        response.read()
        if response.status == 200:
            raise ArtifactExistsError()
        elif response.status != 404:
            raise ArtifactUploadError("HEAD {} {}".format(response.status, response.reason))

        # create temporary file
        return SimpleHttpUploader(self, url)

    def _putUploadFile(self, url, tmp):
        (ok, result) = self.__retry(lambda: self.__putUploadFile(url, tmp))
        if ok:
            return result
        else:
            raise ArtifactUploadError(str(result))

    def __putUploadFile(self, url, tmp):
        # Determine file length outself and add a "Content-Length" header. This
        # used to work in Python 3.5 automatically but was removed later.
        tmp.seek(0, os.SEEK_END)
        length = str(tmp.tell())
        tmp.seek(0)
        headers = self._getHeaders()
        headers.update({ 'Content-Length' : length, 'If-None-Match' : '*' })
        connection = self._getConnection()
        connection.request("PUT", url, tmp, headers=headers)
        response = connection.getresponse()
        response.read()
        if response.status == 412:
            # precondition failed -> lost race with other upload
            raise ArtifactExistsError()
        elif response.status not in [200, 201, 204]:
            raise ArtifactUploadError("PUT {} {}".format(response.status, response.reason))

    def __uploadJenkins(self, step, keyFile, contentFile, suffix):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        # upload with curl if file does not exist yet on server
        insecure = "" if self.__sslVerify else "-k"
        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {KEYFILE}){GEN}"
            BOB_UPLOAD_URL={URL}"/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            if ! curl --output /dev/null --silent --head --fail {INSECURE} "$BOB_UPLOAD_URL" ; then
                BOB_UPLOAD_RSP=$(curl -sSgf {INSECURE} -w '%{{http_code}}' -H 'If-None-Match: *' -T {CONTENTFILE} "$BOB_UPLOAD_URL" || true)
                if [[ $BOB_UPLOAD_RSP != 2?? && $BOB_UPLOAD_RSP != 412 ]]; then
                    echo "Upload failed with code $BOB_UPLOAD_RSP"{FAIL}
                fi
            fi""".format(URL=quote(self.__url.geturl()), KEYFILE=quote(keyFile),
                         CONTENTFILE=quote(contentFile),
                         FAIL="" if self._ignoreErrors() else "; exit 1",
                         GEN=ARCHIVE_GENERATION, SUFFIX=suffix,
                         INSECURE=insecure))

    def upload(self, step, buildIdFile, tgzFile):
        return self.__uploadJenkins(step, buildIdFile, tgzFile, ARTIFACT_SUFFIX)

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.canDownloadJenkins():
            return ""

        insecure = "" if self.__sslVerify else "-k"
        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
                BOB_DOWNLOAD_URL={URL}"/${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}{SUFFIX}"
                curl -sSg {INSECURE} --fail -o {RESULT} "$BOB_DOWNLOAD_URL" || echo Download failed: $?
            fi
            """.format(URL=quote(self.__url.geturl()), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX,
                       INSECURE=insecure))

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return self.__uploadJenkins(step, keyFile, fingerprintFile, FINGERPRINT_SUFFIX)

class SimpleHttpDownloader:
    def __init__(self, archiver, response):
        self.archiver = archiver
        self.response = response
    def __enter__(self):
        return (None, self.response)
    def __exit__(self, exc_type, exc_value, traceback):
        # reset connection on abnormal termination
        if exc_type is not None:
            self.archiver._resetConnection()
        return False

class SimpleHttpUploader:
    def __init__(self, archiver, url):
        self.archiver = archiver
        self.tmp = TemporaryFile()
        self.url = url
    def __enter__(self):
        return (None, self.tmp)
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            # do actual upload on regular handle close
            if exc_type is None:
                self.archiver._putUploadFile(self.url, self.tmp)
        finally:
            self.tmp.close()
        return False


class CustomArchive(BaseArchive):
    """Custom command archive"""

    def __init__(self, spec, whiteList):
        super().__init__(spec)
        self.__downloadCmd = spec.get("download")
        self.__uploadCmd = spec.get("upload")
        self.__whiteList = whiteList

    def _makeUrl(self, buildId, suffix):
        packageResultId = buildIdToName(buildId)
        return "/".join([packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + suffix])

    def _remoteName(self, buildId, suffix):
        return self._makeUrl(buildId, suffix)

    def canDownloadLocal(self):
        return super().canDownloadLocal() and (self.__downloadCmd is not None)

    def canUploadLocal(self):
        return super().canUploadLocal() and (self.__uploadCmd is not None)

    def canDownloadJenkins(self):
        return super().canDownloadJenkins() and (self.__downloadCmd is not None)

    def canUploadJenkins(self):
        return super().canUploadJenkins() and (self.__uploadCmd is not None)

    def _openDownloadFile(self, buildId, suffix):
        (tmpFd, tmpName) = mkstemp()
        url = self._makeUrl(buildId, suffix)
        try:
            os.close(tmpFd)
            env = { k:v for (k,v) in os.environ.items() if k in self.__whiteList }
            env["BOB_LOCAL_ARTIFACT"] = tmpName
            env["BOB_REMOTE_ARTIFACT"] = url
            ret = subprocess.call(["/bin/bash", "-ec", self.__downloadCmd],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                cwd="/tmp", env=env)
            if ret == 0:
                ret = tmpName
                tmpName = None
                return CustomDownloader(ret)
            else:
                raise ArtifactDownloadError("failed (exit {})".format(ret))
        finally:
            if tmpName is not None: os.unlink(tmpName)

    def _openUploadFile(self, buildId, suffix):
        (tmpFd, tmpName) = mkstemp()
        os.close(tmpFd)
        return CustomUploader(tmpName, self._makeUrl(buildId, suffix), self.__whiteList,
            self.__uploadCmd)

    def __uploadJenkins(self, step, buildIdFile, tgzFile, suffix):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        cmd = self.__uploadCmd
        if self._ignoreErrors():
            # wrap in subshell
            cmd = "( " + cmd + " ) || echo Upload failed: $?"

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_LOCAL_ARTIFACT={RESULT}
            BOB_REMOTE_ARTIFACT="${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            """.format(BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION, SUFFIX=suffix)) + cmd

    def upload(self, step, buildIdFile, tgzFile):
        return self.__uploadJenkins(step, buildIdFile, tgzFile, ARTIFACT_SUFFIX)

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.canDownloadJenkins():
            return ""

        return """
if [[ ! -e {RESULT} ]] ; then
    BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
    BOB_LOCAL_ARTIFACT={RESULT}
    BOB_REMOTE_ARTIFACT="${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}{SUFFIX}"
    {CMD}
fi
""".format(CMD=self.__downloadCmd, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
           GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX)

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return self.__uploadJenkins(step, keyFile, fingerprintFile, FINGERPRINT_SUFFIX)

class CustomDownloader:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return (self.name, None)
    def __exit__(self, exc_type, exc_value, traceback):
        os.unlink(self.name)
        return False

class CustomUploader:
    def __init__(self, name, remoteName, whiteList, uploadCmd):
        self.name = name
        self.remoteName = remoteName
        self.whiteList = whiteList
        self.uploadCmd = uploadCmd

    def __enter__(self):
        return (self.name, None)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                env = { k:v for (k,v) in os.environ.items() if k in self.whiteList }
                env["BOB_LOCAL_ARTIFACT"] = self.name
                env["BOB_REMOTE_ARTIFACT"] = self.remoteName
                ret = subprocess.call(["/bin/bash", "-ec", self.uploadCmd],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    cwd="/tmp", env=env)
                if ret != 0:
                    raise ArtifactUploadError("command return with status {}".format(ret))
        finally:
            os.unlink(self.name)
        return False


class AzureArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__container = spec['container']
        self.__account = spec['account']
        self.__key = spec.get('key')
        self.__sasToken = spec.get('sasToken')
        try:
            from azure.storage.blob import BlockBlobService
        except ImportError:
            raise BuildError("azure-storage-blob Python3 library not installed!")
        self.__service = BlockBlobService(account_name=self.__account,
            account_key=self.__key, sas_token=self.__sasToken, socket_timeout=6000)

    @staticmethod
    def __makeBlobName(buildId, suffix):
        packageResultId = buildIdToName(buildId)
        return "/".join([packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + suffix])

    def _remoteName(self, buildId, suffix):
        return "https://{}.blob.core.windows.net/{}/{}".format(self.__account,
            self.__container, self.__makeBlobName(buildId, suffix))

    def _openDownloadFile(self, buildId, suffix):
        from azure.common import AzureException, AzureMissingResourceHttpError
        (tmpFd, tmpName) = mkstemp()
        try:
            os.close(tmpFd)
            self.__service.get_blob_to_path(self.__container,
                self.__makeBlobName(buildId, suffix), tmpName)
            ret = tmpName
            tmpName = None
            return AzureDownloader(ret)
        except AzureMissingResourceHttpError:
            raise ArtifactNotFoundError()
        except AzureException as e:
            raise ArtifactDownloadError(str(e))
        finally:
            if tmpName is not None: os.unlink(tmpName)

    def _openUploadFile(self, buildId, suffix):
        from azure.common import AzureException

        blobName = self.__makeBlobName(buildId, suffix)
        try:
            if self.__service.exists(self.__container, blobName):
                raise ArtifactExistsError()
        except AzureException as e:
            raise ArtifactUploadError(str(e))
        (tmpFd, tmpName) = mkstemp()
        os.close(tmpFd)
        return AzureUploader(self.__service, self.__container, tmpName, blobName)

    def __uploadJenkins(self, step, keyFile, contentFile, suffix):
        if not self.canUploadJenkins():
            return ""

        args = []
        if self.__key: args.append("--key=" + self.__key)
        if self.__sasToken: args.append("--sas-token=" + self.__sasToken)

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            bob _upload azure {ARGS} {ACCOUNT} {CONTAINER} {KEYFILE} {SUFFIX} {CONTENTFILE}{FIXUP}
            """.format(ARGS=" ".join(map(quote, args)), ACCOUNT=quote(self.__account),
                       CONTAINER=quote(self.__container), KEYFILE=quote(keyFile),
                       CONTENTFILE=quote(contentFile),
                       FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                       SUFFIX=suffix))

    def upload(self, step, buildIdFile, tgzFile):
        return self.__uploadJenkins(step, buildIdFile, tgzFile, ARTIFACT_SUFFIX)

    def download(self, step, buildIdFile, tgzFile):
        if not self.canDownloadJenkins():
            return ""

        args = []
        if self.__key: args.append("--key=" + self.__key)
        if self.__sasToken: args.append("--sas-token=" + self.__sasToken)

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                bob _download azure {ARGS} {ACCOUNT} {CONTAINER} {BUILDID} {SUFFIX} {RESULT} || echo Download failed: $?
            fi
            """.format(ARGS=" ".join(map(quote, args)), ACCOUNT=quote(self.__account),
                       CONTAINER=self.__container, BUILDID=quote(buildIdFile),
                       RESULT=quote(tgzFile), SUFFIX=ARTIFACT_SUFFIX))

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return self.__uploadJenkins(step, keyFile, fingerprintFile, FINGERPRINT_SUFFIX)

    @staticmethod
    def scriptDownload(args):
        service, container, remoteBlob, localFile = AzureArchive.scriptGetService(args)
        from azure.common import AzureException

        # Download into temporary file and rename if downloaded successfully
        tmpName = None
        try:
            (tmpFd, tmpName) = mkstemp(dir=".")
            os.close(tmpFd)
            service.get_blob_to_path(container, remoteBlob, tmpName)
            os.rename(tmpName, localFile)
            tmpName = None
        except (OSError, AzureException) as e:
            raise BuildError("Download failed: " + str(e))
        finally:
            if tmpName is not None: os.unlink(tmpName)

    @staticmethod
    def scriptUpload(args):
        service, container, remoteBlob, localFile = AzureArchive.scriptGetService(args)
        from azure.common import AzureException, AzureConflictHttpError
        try:
            service.create_blob_from_path(container, remoteBlob, localFile, if_none_match="*")
            print("OK")
        except AzureConflictHttpError:
            print("skipped")
        except (OSError, AzureException) as e:
            raise BuildError("Upload failed: " + str(e))

    @staticmethod
    def scriptGetService(args):
        parser = argparse.ArgumentParser()
        parser.add_argument('account')
        parser.add_argument('container')
        parser.add_argument('buildid')
        parser.add_argument('suffix')
        parser.add_argument('file')
        parser.add_argument('--key')
        parser.add_argument('--sas-token')
        args = parser.parse_args(args)

        try:
            from azure.storage.blob import BlockBlobService
        except ImportError:
            raise BuildError("azure-storage-blob Python3 library not installed!")

        service = BlockBlobService(account_name=args.account, account_key=args.key,
            sas_token=args.sas_token, socket_timeout=6000)

        try:
            with open(args.buildid, 'rb') as f:
                remoteBlob = AzureArchive.__makeBlobName(f.read(), args.suffix)
        except OSError as e:
            raise BuildError(str(e))

        return (service, args.container, remoteBlob, args.file)

class AzureDownloader:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return (self.name, None)
    def __exit__(self, exc_type, exc_value, traceback):
        os.unlink(self.name)
        return False

class AzureUploader:
    def __init__(self, service, container, name, remoteName):
        self.__service = service
        self.__container = container
        self.__name = name
        self.__remoteName = remoteName

    def __enter__(self):
        return (self.__name, None)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.__upload()
        finally:
            os.unlink(self.__name)
        return False

    def __upload(self):
        from azure.common import AzureException, AzureConflictHttpError
        try:
            self.__service.create_blob_from_path(self.__container,
                self.__remoteName, self.__name, if_none_match="*")
        except AzureConflictHttpError:
            raise ArtifactExistsError()
        except AzureException as e:
            raise ArtifactUploadError(str(e))


class MultiArchive:
    def __init__(self, archives):
        self.__archives = archives

    def wantDownload(self, enable):
        for i in self.__archives: i.wantDownload(enable)

    def wantUpload(self, enable):
        for i in self.__archives: i.wantUpload(enable)

    def canDownloadLocal(self):
        return any(i.canDownloadLocal() for i in self.__archives)

    def canUploadLocal(self):
        return any(i.canUploadLocal() for i in self.__archives)

    def canDownloadJenkins(self):
        return any(i.canDownloadJenkins() for i in self.__archives)

    def canUploadJenkins(self):
        return any(i.canUploadJenkins() for i in self.__archives)

    async def uploadPackage(self, step, buildId, audit, content):
        for i in self.__archives:
            if not i.canUploadLocal(): continue
            await i.uploadPackage(step, buildId, audit, content)

    async def downloadPackage(self, step, buildId, audit, content):
        for i in self.__archives:
            if not i.canDownloadLocal(): continue
            if await i.downloadPackage(step, buildId, audit, content): return True
        return False

    def upload(self, step, buildIdFile, tgzFile):
        return "\n".join(
            i.upload(step, buildIdFile, tgzFile) for i in self.__archives
            if i.canUploadJenkins())

    def download(self, step, buildIdFile, tgzFile):
        return "\n".join(
            i.download(step, buildIdFile, tgzFile) for i in self.__archives
            if i.canDownloadJenkins())

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId):
        for i in self.__archives:
            if not i.canUploadLocal(): continue
            await i.uploadLocalLiveBuildId(step, liveBuildId, buildId)

    async def downloadLocalLiveBuildId(self, step, liveBuildId):
        ret = None
        for i in self.__archives:
            if not i.canDownloadLocal(): continue
            ret = await i.downloadLocalLiveBuildId(step, liveBuildId)
            if ret is not None: break
        return ret

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId, isWin):
        return "\n".join(
            i.uploadJenkinsLiveBuildId(step, liveBuildId, buildId, isWin)
            for i in self.__archives if i.canUploadJenkins())

    async def uploadLocalFingerprint(self, step, key, fingerprint):
        for i in self.__archives:
            if not i.canUploadLocal(): continue
            await i.uploadLocalFingerprint(step, key, fingerprint)

    async def downloadLocalFingerprint(self, step, key):
        ret = None
        for i in self.__archives:
            if not i.canDownloadLocal(): continue
            ret = await i.downloadLocalFingerprint(step, key)
            if ret is not None: break
        return ret

    def uploadJenkinsFingerprint(self, step, keyFile, fingerprintFile):
        return "\n".join(
            i.uploadJenkinsFingerprint(step, keyFile, fingerprintFile)
            for i in self.__archives if i.canUploadJenkins())


def getSingleArchiver(recipes, archiveSpec):
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        return LocalArchive(archiveSpec)
    elif archiveBackend == "http":
        return SimpleHttpArchive(archiveSpec, recipes.getPolicy('secureSSL'))
    elif archiveBackend == "shell":
        return CustomArchive(archiveSpec, recipes.envWhiteList())
    elif archiveBackend == "azure":
        return AzureArchive(archiveSpec)
    elif archiveBackend == "none":
        return DummyArchive()
    else:
        raise BuildError("Invalid archive backend: "+archiveBackend)

def getArchiver(recipes):
    archiveSpec = recipes.archiveSpec()
    if isinstance(archiveSpec, list):
        return MultiArchive([ getSingleArchiver(recipes, i) for i in archiveSpec ])
    else:
        return getSingleArchiver(recipes, archiveSpec)

def doDownload(args, bobRoot):
    archiveBackend = args[0]
    if archiveBackend == "azure":
        AzureArchive.scriptDownload(args[1:])
    else:
        raise BuildError("Invalid archive backend: "+archiveBackend)

def doUpload(args, bobRoot):
    archiveBackend = args[0]
    if archiveBackend == "azure":
        AzureArchive.scriptUpload(args[1:])
    else:
        raise BuildError("Invalid archive backend: "+archiveBackend)
