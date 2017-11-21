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

from .errors import BuildError
from .tty import colorize
from .utils import asHexStr, removePath
from pipes import quote
from tempfile import mkstemp, NamedTemporaryFile, TemporaryFile
import gzip
import http.client
import os.path
import ssl
import subprocess
import tarfile
import textwrap
import urllib.parse

ARCHIVE_GENERATION = '-1'
ARTIFACT_SUFFIX = ".tgz"
BUILDID_SUFFIX = ".buildid"

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

    def uploadPackage(self, buildId, audit, content, verbose):
        pass

    def downloadPackage(self, buildId, audit, content, verbose):
        return False

    def upload(self, step, buildIdFile, tgzFile):
        return ""

    def download(self, step, buildIdFile, tgzFile):
        return ""

    def uploadLocalLiveBuildId(self, liveBuildId, buildId, verbose):
        pass

    def downloadLocalLiveBuildId(self, liveBuildId, verbose):
        return None

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId):
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

    def downloadPackage(self, buildId, audit, content, verbose):
        if not self.canDownloadLocal():
            return False

        if verbose > 0:
            print(colorize("   DOWNLOAD  {} from {} .. "
                            .format(content, self._remoteName(buildId, ARTIFACT_SUFFIX)), "32"),
                  end="")
        else:
            print(colorize("   DOWNLOAD  {} .. ".format(content), "32"), end="")
        try:
            with self._openDownloadFile(buildId, ARTIFACT_SUFFIX) as (name, fileobj):
                with tarfile.open(name, "r|*", fileobj=fileobj, errorlevel=1) as tar:
                    removePath(audit)
                    removePath(content)
                    os.makedirs(content)
                    self.__extractPackage(tar, audit, content)
            print(colorize("ok", "32"))
            return True
        except ArtifactNotFoundError:
            print(colorize("not found", "33"))
            return False
        except ArtifactDownloadError as e:
            print(colorize(e.reason, "33"))
            return False
        except BuildError as e:
            print(colorize("error", "31"))
            raise
        except OSError as e:
            print(colorize("error", "31"))
            raise BuildError("Cannot download artifact: " + str(e))
        except tarfile.TarError as e:
            print(colorize("error", "31"))
            raise BuildError("Error extracting binary artifact: " + str(e))

    def downloadLocalLiveBuildId(self, liveBuildId, verbose):
        if not self.canDownloadLocal():
            return None

        ret = None
        if verbose > 0:
            print(colorize("   LOOKUP    {} .. "
                            .format(self._remoteName(liveBuildId, BUILDID_SUFFIX)), "32"),
                  end="")

        try:
            with self._openDownloadFile(liveBuildId, BUILDID_SUFFIX) as (name, fileobj):
                ret = readFileOrHandle(name, fileobj)
            if verbose > 0: print(colorize("ok", "32"))
        except ArtifactNotFoundError:
            if verbose > 0: print(colorize("unknown", "33"))
        except ArtifactDownloadError as e:
            if verbose > 0: print(colorize(e.reason, "33"))
        except BuildError as e:
            if verbose > 0: print(colorize("error", "31"))
            raise
        except OSError as e:
            if verbose > 0: print(colorize("error", "31"))
            raise BuildError("Cannot download artifact: " + str(e))

        return ret

    def _openUploadFile(self, buildId, suffix):
        raise ArtifactUploadError("not implemented")

    def uploadPackage(self, buildId, audit, content, verbose):
        if not self.canUploadLocal():
            return

        shown = False
        try:
            with self._openUploadFile(buildId, ARTIFACT_SUFFIX) as (name, fileobj):
                pax = { 'bob-archive-vsn' : "1" }
                if verbose > 0:
                    print(colorize("   UPLOAD    {} to {} .. "
                                    .format(content, self._remoteName(buildId, ARTIFACT_SUFFIX)), "32"),
                          end="")
                else:
                    print(colorize("   UPLOAD    {} .. ".format(content), "32"), end="")
                shown = True
                with gzip.open(name or fileobj, 'wb', 6) as gzf:
                    with tarfile.open(name, "w", fileobj=gzf,
                                      format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
                        tar.add(audit, "meta/" + os.path.basename(audit))
                        tar.add(content, arcname="content")
            print(colorize("ok", "32"))
        except ArtifactExistsError:
            if shown:
                print("skipped ({} exists in archive)".format(content))
            else:
                print("   UPLOAD    skipped ({} exists in archive)".format(content))
        except (ArtifactUploadError, tarfile.TarError, OSError) as e:
            if shown:
                if verbose > 0:
                    print(colorize("error ("+str(e)+")", "31"))
                else:
                    print(colorize("error", "31"))
            if not self.__ignoreErrors:
                raise BuildError("Cannot upload artifact: " + str(e))

    def uploadLocalLiveBuildId(self, liveBuildId, buildId, verbose):
        if not self.canUploadLocal():
            return

        shown = False
        try:
            with self._openUploadFile(liveBuildId, BUILDID_SUFFIX) as (name, fileobj):
                if verbose > 0:
                    print(colorize("   CACHE     {} .. "
                                    .format(self._remoteName(liveBuildId, BUILDID_SUFFIX)), "32"),
                          end="")
                    shown = True
                writeFileOrHandle(name, fileobj, buildId)
            if verbose > 0: print(colorize("ok", "32"))
        except ArtifactExistsError:
            if verbose > 0:
                if shown:
                    print("skipped (exists in archive)")
                else:
                    print("   CACHE     skipped (exists in archive)")
        except (ArtifactUploadError, OSError) as e:
            if shown: print(colorize("error ("+str(e)+")", "31"))
            if not self.__ignoreErrors:
                raise BuildError("Cannot upload artifact: " + str(e))


class LocalArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__basePath = os.path.abspath(spec["path"])

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
        if not os.path.isdir(packageResultPath): os.makedirs(packageResultPath)
        return LocalArchiveUploader(
            NamedTemporaryFile(dir=packageResultPath, delete=False),
            packageResultFile)

    def __uploadJenkins(self, step, buildIdFile, resultFile, suffix):
        if not self.canUploadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_UPLOAD_FILE="{DIR}/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            if [[ ! -e ${{BOB_UPLOAD_FILE}} ]] ; then
                mkdir -p "${{BOB_UPLOAD_FILE%/*}}"{FIXUP}
                cp {RESULT} "$BOB_UPLOAD_FILE"{FIXUP}
            fi""".format(DIR=self.__basePath, BUILDID=quote(buildIdFile), RESULT=quote(resultFile),
                         FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                         GEN=ARCHIVE_GENERATION, SUFFIX=suffix))

    def upload(self, step, buildIdFile, tgzFile):
        return self.__uploadJenkins(step, buildIdFile, tgzFile, ARTIFACT_SUFFIX)

    def download(self, step, buildIdFile, tgzFile):
        if not self.canDownloadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
                BOB_DOWNLOAD_FILE="{DIR}/${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}{SUFFIX}"
                cp "$BOB_DOWNLOAD_FILE" {RESULT} || echo Download failed: $?
            fi
            """.format(DIR=self.__basePath, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX))

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

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
    def __init__(self, tmp, destination):
        self.tmp = tmp
        self.destination = destination
    def __enter__(self):
        return (None, self.tmp)
    def __exit__(self, exc_type, exc_value, traceback):
        self.tmp.close()
        # atomically move file to destination at end of upload
        if exc_type is None:
            os.replace(self.tmp.name, self.destination)
        else:
            os.unlink(self.tmp.name)
        return False


class SimpleHttpArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__url = urllib.parse.urlparse(spec["url"])
        self.__connection = None

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
            ctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
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

    def _openDownloadFile(self, buildId, suffix):
        (ok, result) = self.__retry(lambda: self.__openDownloadFile(buildId, suffix))
        if ok:
            return result
        else:
            raise ArtifactDownloadError(str(result))

    def __openDownloadFile(self, buildId, suffix):
        connection = self._getConnection()
        url = self._makeUrl(buildId, suffix)
        connection.request("GET", url)
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
        connection.request("HEAD", url)
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
        tmp.seek(0)
        connection = self._getConnection()
        connection.request("PUT", url, tmp, headers={ 'If-None-Match' : '*' })
        response = connection.getresponse()
        response.read()
        if response.status == 412:
            # precondition failed -> lost race with other upload
            raise ArtifactExistsError()
        elif response.status not in [200, 201, 204]:
            raise ArtifactUploadError("PUT {} {}".format(response.status, response.reason))

    def upload(self, step, buildIdFile, tgzFile):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        # upload with curl if file does not exist yet on server
        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_UPLOAD_URL="{URL}/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            if ! curl --output /dev/null --silent --head --fail "$BOB_UPLOAD_URL" ; then
                curl -sSgf -T {RESULT} "$BOB_UPLOAD_URL"{FIXUP}
            fi""".format(URL=self.__url.geturl(), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                         FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                         GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX))

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.canDownloadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
                BOB_DOWNLOAD_URL="{URL}/${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}{SUFFIX}"
                curl -sSg --fail -o {RESULT} "$BOB_DOWNLOAD_URL" || echo Download failed: $?
            fi
            """.format(URL=self.__url.geturl(), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION, SUFFIX=ARTIFACT_SUFFIX))

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        # upload with curl if file does not exist yet on server
        return "\n" + textwrap.dedent("""\
            # upload live build-id
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {LIVEBUILDID}){GEN}"
            BOB_UPLOAD_URL="{URL}/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}{SUFFIX}"
            BOB_UPLOAD_RSP=$(curl -sSgf -w '%{{http_code}}' -H 'If-None-Match: *' -T {BUILDID} "$BOB_UPLOAD_URL" || true)
            if [[ $BOB_UPLOAD_RSP != 2?? && $BOB_UPLOAD_RSP != 412 ]]; then
                echo "Upload failed with code $BOB_UPLOAD_RSP"{FAIL}
            fi
            """.format(URL=self.__url.geturl(), LIVEBUILDID=quote(liveBuildId),
                       BUILDID=quote(buildId),
                       FAIL="" if self._ignoreErrors() else "; exit 1",
                       GEN=ARCHIVE_GENERATION, SUFFIX=BUILDID_SUFFIX))

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

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId):
        return self.__uploadJenkins(step, liveBuildId, buildId, BUILDID_SUFFIX)

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

    def uploadPackage(self, buildId, audit, content, verbose):
        for i in self.__archives:
            if not i.canUploadLocal(): continue
            i.uploadPackage(buildId, audit, content, verbose)

    def downloadPackage(self, buildId, audit, content, verbose):
        for i in self.__archives:
            if not i.canDownloadLocal(): continue
            if i.downloadPackage(buildId, audit, content, verbose): return True
        return False

    def upload(self, step, buildIdFile, tgzFile):
        return "\n".join(
            i.upload(step, buildIdFile, tgzFile) for i in self.__archives
            if i.canUploadJenkins())

    def download(self, step, buildIdFile, tgzFile):
        return "\n".join(
            i.download(step, buildIdFile, tgzFile) for i in self.__archives
            if i.canDownloadJenkins())

    def uploadLocalLiveBuildId(self, liveBuildId, buildId, verbose):
        for i in self.__archives:
            if not i.canUploadLocal(): continue
            i.uploadLocalLiveBuildId(liveBuildId, buildId, verbose)

    def downloadLocalLiveBuildId(self, liveBuildId, verbose):
        ret = None
        for i in self.__archives:
            if not i.canDownloadLocal(): continue
            ret = i.downloadLocalLiveBuildId(liveBuildId, verbose)
            if ret is not None: break
        return ret

    def uploadJenkinsLiveBuildId(self, step, liveBuildId, buildId):
        return "\n".join(
            i.uploadJenkinsLiveBuildId(step, liveBuildId, buildId)
            for i in self.__archives if i.canUploadJenkins())


def getSingleArchiver(recipes, archiveSpec):
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        return LocalArchive(archiveSpec)
    elif archiveBackend == "http":
        return SimpleHttpArchive(archiveSpec)
    elif archiveBackend == "shell":
        return CustomArchive(archiveSpec, recipes.envWhiteList())
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
