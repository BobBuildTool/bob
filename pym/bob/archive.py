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

from .errors import BuildError
from .tty import colorize
from .utils import asHexStr, removePath
from tempfile import mkstemp, NamedTemporaryFile, TemporaryFile
from pipes import quote
import os.path
import subprocess
import tarfile
import textwrap
import urllib.parse
import http.client

ARCHIVE_GENERATION = '-1'

def buildIdToName(bid):
    return asHexStr(bid) + ARCHIVE_GENERATION

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

class ArtifactNotFoundError(Exception):
    pass

class ArtifactExistsError(Exception):
    pass

class ArtifactDownloadError(Exception):
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
        try:
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
        except tarfile.TarError as e:
            raise BuildError("Error extracting binary artifact: " + str(e))

    def _openDownloadTar(self, buildId):
        raise ArtifactNotFoundError()

    def downloadPackage(self, buildId, audit, content, verbose):
        if not self.canDownloadLocal():
            return False

        if verbose > 0:
            print(colorize("   DOWNLOAD  {} from {} .. "
                            .format(content, self._remoteName(buildId)), "32"),
                  end="")
        else:
            print(colorize("   DOWNLOAD  {} .. ".format(content), "32"), end="")
        try:
            with self._openDownloadTar(buildId) as (name, fileobj):
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

    def _openUploadTar(self, buildId):
        raise ArtifactExistsError()

    def uploadPackage(self, buildId, audit, content, verbose):
        if not self.canUploadLocal():
            return

        try:
            with self._openUploadTar(buildId) as (name, fileobj):
                pax = { 'bob-archive-vsn' : "1" }
                if verbose > 0:
                    print(colorize("   UPLOAD    {} to {}"
                                    .format(content, self._remoteName(buildId)), "32"))
                else:
                    print(colorize("   UPLOAD    {}".format(content), "32"))
                with tarfile.open(name, "w|gz", fileobj=fileobj,
                                  format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
                    tar.add(audit, "meta/" + os.path.basename(audit))
                    tar.add(content, arcname="content")
        except ArtifactExistsError:
            print("   UPLOAD    skipped ({} exists in archive)".format(content))
            return
        except tarfile.TarError as e:
            raise BuildError("Error archiving binary artifact: " + str(e))
        except OSError as e:
            raise BuildError("Cannot upload artifact: " + str(e))


class LocalArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__basePath = os.path.abspath(spec["path"])

    def _getPath(self, buildId):
        packageResultId = buildIdToName(buildId)
        packageResultPath = os.path.join(self.__basePath, packageResultId[0:2],
                                         packageResultId[2:4])
        packageResultFile = os.path.join(packageResultPath,
                                         packageResultId[4:]) + ".tgz"
        return (packageResultPath, packageResultFile)

    def _remoteName(self, buildId):
        return self._getPath(buildId)[1]

    def _openDownloadTar(self, buildId):
        (packageResultPath, packageResultFile) = self._getPath(buildId)
        if os.path.isfile(packageResultFile) and tarfile.is_tarfile(packageResultFile):
            return LocalArchiveDownloader(packageResultFile)
        else:
            raise ArtifactNotFoundError()

    def _openUploadTar(self, buildId):
        (packageResultPath, packageResultFile) = self._getPath(buildId)
        if os.path.isfile(packageResultFile):
            raise ArtifactExistsError()

        # open temporary file in destination directory
        if not os.path.isdir(packageResultPath): os.makedirs(packageResultPath)
        return LocalArchiveUploader(
            NamedTemporaryFile(dir=packageResultPath, delete=False),
            packageResultFile)

    def upload(self, step, buildIdFile, tgzFile):
        if self.canUploadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_FILE="{DIR}/$(hexdump -e '2/1 "%02x/" 14/1 "%02x"' {BUILDID}){GEN}.tgz"
            if [[ ! -e ${{BOB_UPLOAD_FILE}} ]] ; then
                mkdir -p "${{BOB_UPLOAD_FILE%/*}}"{FIXUP}
                cp {RESULT} "$BOB_UPLOAD_FILE"{FIXUP}
            fi""".format(DIR=self.__basePath, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                         FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                         GEN=ARCHIVE_GENERATION))

    def download(self, step, buildIdFile, tgzFile):
        if not self.canDownloadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_FILE="{DIR}/$(hexdump -e '2/1 "%02x/" 14/1 "%02x"' {BUILDID}){GEN}.tgz"
                cp "$BOB_DOWNLOAD_FILE" {RESULT} || echo Download failed: $?
            fi
            """.format(DIR=self.__basePath, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile)),
                       GEN=ARCHIVE_GENERATION)

class LocalArchiveDownloader:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return (self.name, None)
    def __exit__(self, exc_type, exc_value, traceback):
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

    def _makeUrl(self, buildId):
        packageResultId = buildIdToName(buildId)
        return "/".join([self.__url.path, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + ".tgz"])

    def _remoteName(self, buildId):
        url = self.__url
        return urllib.parse.urlunparse((url.scheme, url.netloc, self._makeUrl(buildId), '', '', ''))

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

    def _openDownloadTar(self, buildId):
        retry = True
        while True:
            try:
                connection = self._getConnection()
                url = self._makeUrl(buildId)
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
            except OSError as e:
                raise ArtifactDownloadError(str(e))
            except http.client.HTTPException as e:
                self._resetConnection()
                if not retry: raise ArtifactDownloadError(str(e))
                retry = False

    def _openUploadTar(self, buildId):
        retry = True
        while True:
            try:
                connection = self._getConnection()
                url = self._makeUrl(buildId)

                # check if already there
                connection.request("HEAD", url)
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    raise ArtifactExistsError()
                elif response.status != 404:
                    raise BuildError("Error for HEAD on {}: {} {}"
                                        .forma(url, response.status, response.reason))

                # create temporary file
                return SimpleHttpUploader(self, url)

            except http.client.HTTPException as e:
                self._resetConnection()
                if not retry: raise BuildError("Upload failed: " + str(e))
                retry = False

    def upload(self, step, buildIdFile, tgzFile):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        # upload with curl if file does not exist yet on server
        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_UPLOAD_URL="{URL}/${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}.tgz"
            if ! curl --output /dev/null --silent --head --fail "$BOB_UPLOAD_URL" ; then
                curl -sSgf -T {RESULT} "$BOB_UPLOAD_URL"{FIXUP}
            fi""".format(URL=self.__url.geturl(), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                         FIXUP=" || echo Upload failed: $?" if self._ignoreErrors() else "",
                         GEN=ARCHIVE_GENERATION))

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.canDownloadJenkins():
            return ""

        return "\n" + textwrap.dedent("""\
            if [[ ! -e {RESULT} ]] ; then
                BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
                BOB_DOWNLOAD_URL="{URL}/${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}.tgz"
                curl -sSg --fail -o {RESULT} "$BOB_DOWNLOAD_URL" || echo Download failed: $?
            fi
            """.format(URL=self.__url.geturl(), BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
                       GEN=ARCHIVE_GENERATION))

class SimpleHttpDownloader:
    def __init__(self, archiver, response):
        self.archiver = archiver
        self.response = response
    def __enter__(self):
        return (None, self.response)
    def __exit__(self, exc_type, exc_value, traceback):
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
            # actual upload
            if exc_type is None:
                self.tmp.seek(0)
                connection = self.archiver._getConnection()
                connection.request("PUT", self.url, self.tmp)
                response = connection.getresponse()
                response.read()
                if response.status not in [200, 201]:
                    raise BuildError("Error uploading {}: {} {}"
                                        .format(self.url, response.status, response.reason))
        except http.client.HTTPException as e:
            self.archiver._resetConnection()
            raise BuildError("Upload failed: " + str(e))
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

    def _makeUrl(self, buildId):
        packageResultId = buildIdToName(buildId)
        return "/".join([packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + ".tgz"])

    def _remoteName(self, buildId):
        return self._makeUrl(buildId)

    def canDownloadLocal(self):
        return super().canDownloadLocal() and (self.__downloadCmd is not None)

    def canUploadLocal(self):
        return super().canUploadLocal() and (self.__uploadCmd is not None)

    def canDownloadJenkins(self):
        return super().canDownloadJenkins() and (self.__downloadCmd is not None)

    def canUploadJenkins(self):
        return super().canUploadJenkins() and (self.__uploadCmd is not None)

    def _openDownloadTar(self, buildId):
        (tmpFd, tmpName) = mkstemp()
        url = self._makeUrl(buildId)
        if verbose > 0:
            print(colorize("   DOWNLOAD  {} from {} ({}) ...".format(path, url, tmpName), "32"), end="")
        else:
            print(colorize("   DOWNLOAD  {}...".format(path), "32"), end="")
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
                ArtifactDownloadError("failed (exit {})".format(ret))
        finally:
            if tmpName is not None: os.unlink(tmpName)

    def _openUploadTar(self, buildId):
        (tmpFd, tmpName) = mkstemp()
        os.close(tmpFd)
        return CustomUploader(tmpName, self._makeUrl(buildId), self.__whiteList,
            self.__uploadCmd, self._ignoreErrors())

    def upload(self, step, buildIdFile, tgzFile):
        # only upload if requested
        if not self.canUploadJenkins():
            return ""

        cmd = self.__uploadCmd
        if self._ignoreErrors():
            # wrap in subshell
            cmd = "( " + cmd + " ) || Upload failed: $?"

        return "\n" + textwrap.dedent("""\
            # upload artifact
            cd $WORKSPACE
            BOB_UPLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
            BOB_LOCAL_ARTIFACT={RESULT}
            BOB_REMOTE_ARTIFACT="${{BOB_UPLOAD_BID:0:2}}/${{BOB_UPLOAD_BID:2:2}}/${{BOB_UPLOAD_BID:4}}.tgz"
            """.format(BUILDID=quote(buildIdFile), RESULT=quote(tgzFile), GEN=ARCHIVE_GENERATION)) + cmd

    def download(self, step, buildIdFile, tgzFile):
        # only download if requested
        if not self.canDownloadJenkins():
            return ""

        return """
if [[ ! -e {RESULT} ]] ; then
    BOB_DOWNLOAD_BID="$(hexdump -ve '/1 "%02x"' {BUILDID}){GEN}"
    BOB_LOCAL_ARTIFACT={RESULT}
    BOB_REMOTE_ARTIFACT="${{BOB_DOWNLOAD_BID:0:2}}/${{BOB_DOWNLOAD_BID:2:2}}/${{BOB_DOWNLOAD_BID:4}}.tgz"
    {CMD}
fi
""".format(CMD=self.__downloadCmd, BUILDID=quote(buildIdFile), RESULT=quote(tgzFile),
           GEN=ARCHIVE_GENERATION)

class CustomDownloader:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return (self.name, None)
    def __exit__(self, exc_type, exc_value, traceback):
        os.unlink(self.name)
        return False

class CustomUploader:
    def __init__(self, name, remoteName, whiteList, uploadCmd, ignoreErrors):
        self.name = name
        self.remoteName = remoteName
        self.whiteList = whiteList
        self.uploadCmd = uploadCmd
        self.ignoreErrors = ignoreErrors

    def __enter__(self):
        return (self.name, None)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                env = { k:v for (k,v) in os.environ.items() if k in self.__whiteList }
                env["BOB_LOCAL_ARTIFACT"] = self.name
                env["BOB_REMOTE_ARTIFACT"] = self.remoteName
                ret = subprocess.call(["/bin/bash", "-ec", self.uploadCmd],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    cwd="/tmp", env=env)
                if ret != 0 and not self.ignoreErrors:
                    raise BuildError("Upload failed: command return with status {}"
                                        .format(ret))
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
