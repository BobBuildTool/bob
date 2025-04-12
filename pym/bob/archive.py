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

from .audit import Audit
from .errors import BuildError
from .tty import stepAction, stepMessage, \
    SKIPPED, EXECUTED, WARNING, INFO, TRACE, ERROR, IMPORTANT
from .utils import asHexStr, removePath, isWindows, getBashPath, tarfileOpen, binStat, removePrefix
from .webdav import WebDav, HTTPException, HttpDownloadError, HttpUploadError, HttpNotFoundError, HttpAlreadyExistsError
from tempfile import mkstemp, NamedTemporaryFile, TemporaryFile, gettempdir
import asyncio
import concurrent.futures
import concurrent.futures.process
import gzip
import io
import os
import os.path
import shutil
import signal
import struct
import subprocess
import tarfile
import urllib.parse
import hashlib

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

    def canManage(self):
        return False

    def wantDownloadLocal(self, enable):
        pass

    def wantDownloadJenkins(self, enable):
        pass

    def wantUploadLocal(self, enable):
        pass

    def wantUploadJenkins(self, enable):
        pass

    def canDownload(self):
        return False

    def canUpload(self):
        return False

    def canCache(self):
        return False

    async def uploadPackage(self, step, buildId, audit, content, executor=None):
        pass

    async def downloadPackage(self, step, buildId, audit, content, caches=[],
                              executor=None):
        return False

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId, executor=None):
        pass

    async def downloadLocalLiveBuildId(self, step, liveBuildId, executor=None):
        return None

    async def uploadLocalFingerprint(self, step, key, fingerprint, executor=None):
        pass

    async def downloadLocalFingerprint(self, step, key, executor=None):
        return None

    def getArchiveName(self):
       return "Dummy"

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

class TarHelper:

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
                with tar.extractfile(f) as audit_src:
                    with open(audit, 'wb') as audit_dst:
                        shutil.copyfileobj(audit_src, audit_dst)
            elif f.name == "content" or f.name == "meta":
                pass
            else:
                raise BuildError("Binary artifact contained unknown file: " + f.name)
            f = tar.next()

    def _extract(self, fileobj, audit, content):
        with tarfileOpen(None, "r|*", fileobj=fileobj, errorlevel=1) as tar:
            removePath(audit)
            removePath(content)
            os.makedirs(content)
            self.__extractPackage(tar, audit, content)

    def _extractAudit(self, filename=None, fileobj=None):
        with tarfileOpen(name=filename, mode="r|*", fileobj=fileobj, errorlevel=1) as tar:
            # validate
            if tar.pax_headers.get('bob-archive-vsn') != "1":
                return

            # find audit trail
            f = tar.next()
            while f:
                if f.name == "meta/audit.json.gz": break
                f = tar.next()
            else:
                raise BuildError("Missing audit trail!")

            # read audit trail
            auditJsonGz = tar.extractfile(f)
            auditJson = gzip.GzipFile(fileobj=auditJsonGz)

            return Audit.fromByteStream(auditJson, filename)

    def _pack(self, name, fileobj, audit, content):
        pax = { 'bob-archive-vsn' : "1" }
        with gzip.open(name or fileobj, 'wb', 6) as gzf:
            with tarfileOpen(name, "w", fileobj=gzf,
                             format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
                tar.add(audit, "meta/" + os.path.basename(audit))
                tar.add(content, arcname="content")


class JenkinsArchive(TarHelper):
    ignoreErrors = False

    def __init__(self, spec):
        self.__xferArtifacts = spec.get("xfer", False)

    def wantDownloadLocal(self, enable):
        pass

    def wantDownloadJenkins(self, enable):
        pass

    def wantUploadLocal(self, enable):
        pass

    def wantUploadJenkins(self, enable):
        pass

    def canDownload(self):
        return True

    def canUpload(self):
        return True

    def canCache(self):
        return True

    def canManage(self):
        return False

    async def uploadPackage(self, step, buildId, audit, content, executor=None):
        if not audit:
            raise BuildError("Missing audit trail! Cannot proceed without one.")

        try:
            with open(self.buildIdName(step), "wb") as f:
                f.write(buildId)
        except OSError as e:
            raise BuildError("Cannot store artifact: " + str(e))

        if self.__xferArtifacts:
            loop = asyncio.get_event_loop()
            name = self.tgzName(step)
            with stepAction(step, "PACK", content) as a:
                try:
                    if os.path.exists(name):
                        a.setResult("skipped (exist already)", SKIPPED)
                    else:
                        msg, kind = await loop.run_in_executor(executor,
                            JenkinsArchive._uploadPackage, self, name, buildId,
                            audit, content)
                        a.setResult(msg, kind)
                except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                    raise BuildError("Packing of package interrupted.")

    def _uploadPackage(self, name, buildId, audit, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            self._pack(name, None, audit, content)
        except (tarfile.TarError, OSError) as e:
            raise BuildError("Cannot pack artifact: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        return ("ok", EXECUTED)

    async def downloadPackage(self, step, buildId, audit, content, caches=[],
                              executor=None):
        loop = asyncio.get_event_loop()
        with stepAction(step, "UNPACK", content) as a:
            try:
                ret, msg = await loop.run_in_executor(executor,
                    JenkinsArchive._downloadPackage, self, self.tgzName(step),
                    self.buildIdName(step), buildId, audit, content)
                if not ret: a.fail(msg, WARNING)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError("Extraction of package interrupted.")

    def _downloadPackage(self, tgzName, buildIdName, buildId, audit, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            if not os.path.exists(tgzName):
                return (False, "not found")
            with open(buildIdName, "rb") as f:
                assert f.read() == buildId, "Artifact {} has expect buildId".format(tgzName)

            with open(tgzName, "rb") as f:
                self._extract(f, audit, content)
            return (True, None)
        except (OSError, tarfile.TarError) as e:
            raise BuildError("Error extracting binary artifact: " + str(e))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    def cachePackage(self, buildId, workspace):
        try:
            with open(self._buildIdNameW(workspace), "xb") as f:
                f.write(buildId)
            if self.__xferArtifacts:
                return JenkinsCacheHelper(open(self._tgzNameW(workspace), "xb"))
            else:
                return None
        except FileExistsError:
            return None
        except OSError as e:
            raise BuildError("Cannot cache artifact: " + str(e))

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId, executor=None):
        pass

    async def downloadLocalLiveBuildId(self, step, liveBuildId, executor=None):
        return None

    async def uploadLocalFingerprint(self, step, key, fingerprint, executor=None):
        pass

    async def downloadLocalFingerprint(self, step, key, executor=None):
        return None

    @staticmethod
    def tgzName(step):
        return JenkinsArchive._tgzNameW(step.getWorkspacePath())

    @staticmethod
    def _tgzNameW(workspace):
        return workspace.replace('/', '_') + ARTIFACT_SUFFIX

    @staticmethod
    def buildIdName(step):
        return JenkinsArchive._buildIdNameW(step.getWorkspacePath())

    @staticmethod
    def _buildIdNameW(workspace):
        return workspace.replace('/', '_') + BUILDID_SUFFIX

class JenkinsCacheHelper:
    def __init__(self, f):
        self.__f = f

    def __enter__(self):
        return (None, self.__f)

    def __exit__(self, exc_type, exc_value, traceback):
        self.__f.close()
        return False


class BaseArchive(TarHelper):
    def __init__(self, spec):
        flags = spec.get("flags", ["upload", "download"])
        self.__name = spec.get("name")
        self.__useDownload = "download" in flags
        self.__useUpload = "upload" in flags
        self.__ignoreErrors = "nofail" in flags
        self.__useLocal = "nolocal" not in flags
        self.__useJenkins = "nojenkins" not in flags
        self.__useCache = "cache" in flags
        self.__managed = "managed" in flags
        self.__wantDownloadLocal = False
        self.__wantDownloadJenkins = False
        self.__wantUploadLocal = False
        self.__wantUploadJenkins = False

    @property
    def ignoreErrors(self):
        return self.__ignoreErrors

    def wantDownloadLocal(self, enable):
        self.__wantDownloadLocal = enable

    def wantDownloadJenkins(self, enable):
        self.__wantDownloadJenkins = enable

    def wantUploadLocal(self, enable):
        self.__wantUploadLocal = enable

    def wantUploadJenkins(self, enable):
        self.__wantUploadJenkins = enable

    def canDownload(self):
        return self.__useDownload and ((self.__wantDownloadLocal and self.__useLocal) or
                                       (self.__wantDownloadJenkins and self.__useJenkins))

    def canUpload(self):
        return self.__useUpload and ((self.__wantUploadLocal and self.__useLocal) or
                                     (self.__wantUploadJenkins and self.__useJenkins))

    def canCache(self):
        return self.__useCache

    def _openDownloadFile(self, buildId, suffix):
        raise ArtifactNotFoundError()

    def canManage(self):
        return self.__managed and self._canManage()

    def _canManage(self):
        return False

    def _namedErrorString(self, err):
        name = self.getArchiveName()
        if name:
            return "{}: {}".format(name, err)
        else:
            return err

    async def downloadPackage(self, step, buildId, audit, content, caches=[],
                              executor=None):
        if not self.canDownload():
            return False

        loop = asyncio.get_event_loop()
        suffix = ARTIFACT_SUFFIX
        details = " from {}".format(self._remoteName(buildId, suffix))
        with stepAction(step, "DOWNLOAD", content, details=details) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(executor, BaseArchive._downloadPackage,
                    self, buildId, suffix, audit, content, caches, step.getWorkspacePath())
                if not ret: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Download of package interrupted."))

    def cachePackage(self, buildId, workspace):
        try:
            return self._openUploadFile(buildId, ARTIFACT_SUFFIX, False)
        except ArtifactExistsError:
            return None
        except (ArtifactUploadError, HttpUploadError, OSError) as e:
            if self.__ignoreErrors:
                return None
            else:
                raise BuildError(self._namedErrorString("Cannot cache artifact: " + str(e)))

    def _downloadPackage(self, buildId, suffix, audit, content, caches, workspace):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openDownloadFile(buildId, suffix) as (name, fileobj):
                with Tee(name, fileobj, buildId, caches, workspace) as fo:
                    self._extract(fo, audit, content)
            return (True, None, None)
        except (ArtifactNotFoundError, HttpNotFoundError):
            return (False, self._namedErrorString("not found"), WARNING)
        except (ArtifactDownloadError, HttpDownloadError) as e:
            return (False, self._namedErrorString(e.reason), WARNING)
        except ConnectionRefusedError:
            return (False, self._namedErrorString("connection failed"), WARNING)
        except BuildError as e:
            raise
        except OSError as e:
            raise BuildError(self._namedErrorString("Cannot download artifact: " + str(e)))
        except tarfile.TarError as e:
            raise BuildError(self._namedErrorString("Error extracting binary artifact: " + str(e)))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    async def downloadLocalLiveBuildId(self, step, liveBuildId, executor=None):
        if not self.canDownload():
            return None

        loop = asyncio.get_event_loop()
        with stepAction(step, "MAP-SRC", self._remoteName(liveBuildId, BUILDID_SUFFIX), (INFO,TRACE)) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(executor,
                    BaseArchive._downloadLocalFile, self, liveBuildId, BUILDID_SUFFIX)
                if ret is None: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Download of build-id interrupted."))

    def _downloadLocalFile(self, key, suffix):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openDownloadFile(key, suffix) as (name, fileobj):
                ret = readFileOrHandle(name, fileobj)
            return (ret, None, None)
        except (ArtifactNotFoundError, HttpNotFoundError):
            return (None, self._namedErrorString("not found"), WARNING)
        except (ArtifactDownloadError, HttpDownloadError) as e:
            return (None, self._namedErrorString(e.reason), WARNING)
        except ConnectionRefusedError:
            return (None, self._namedErrorString("connection failed"), WARNING)
        except BuildError as e:
            raise
        except OSError as e:
            raise BuildError(self._namedErrorString("Cannot download file: " + str(e)))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    def _openUploadFile(self, buildId, suffix, overwrite):
        raise ArtifactUploadError("not implemented")

    async def uploadPackage(self, step, buildId, audit, content, executor=None):
        if not self.canUpload():
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
                msg, kind = await loop.run_in_executor(executor, BaseArchive._uploadPackage,
                    self, buildId, suffix, audit, content)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Upload of package interrupted."))

    def _uploadPackage(self, buildId, suffix, audit, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            with self._openUploadFile(buildId, suffix, False) as (name, fileobj):
                self._pack(name, fileobj, audit, content)
        except (ArtifactExistsError, HttpAlreadyExistsError):
            return (self._namedErrorString("skipped ({} exists in archive)".format(content)), SKIPPED)
        except (ArtifactUploadError, HttpUploadError, tarfile.TarError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error ("+str(e)+")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Cannot upload artifact: " + str(e)))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        return ("ok", EXECUTED)

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId, executor=None):
        if not self.canUpload():
            return

        loop = asyncio.get_event_loop()
        with stepAction(step, "CACHE-BID", self._remoteName(liveBuildId, BUILDID_SUFFIX), (INFO,TRACE)) as a:
            try:
                msg, kind = await loop.run_in_executor(executor, BaseArchive._uploadLocalFile, self, liveBuildId, BUILDID_SUFFIX, buildId)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Upload of build-id interrupted."))

    def _uploadLocalFile(self, key, suffix, content):
        # Set default signal handler so that KeyboardInterrupt is raised.
        # Needed to gracefully handle ctrl+c.
        signal.signal(signal.SIGINT, signal.default_int_handler)

        try:
            # Meta data file uploads overwrite their targets. Thus we should
            # never see an ArtifactExistsError.
            with self._openUploadFile(key, suffix, True) as (name, fileobj):
                writeFileOrHandle(name, fileobj, content)
        except (ArtifactUploadError, HttpUploadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error ("+str(e)+")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Cannot upload file: " + str(e)))
        finally:
            # Restore signals to default so that Ctrl+C kills process. Needed
            # to prevent ugly backtraces when user presses ctrl+c.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        return ("ok", EXECUTED)

    async def uploadLocalFingerprint(self, step, key, fingerprint, executor=None):
        if not self.canUpload():
            return

        loop = asyncio.get_event_loop()
        with stepAction(step, "CACHE-FPR", self._remoteName(key, FINGERPRINT_SUFFIX)) as a:
            try:
                msg, kind = await loop.run_in_executor(executor, BaseArchive._uploadLocalFile, self, key, FINGERPRINT_SUFFIX, fingerprint)
                a.setResult(msg, kind)
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Upload of build-id interrupted."))

    async def downloadLocalFingerprint(self, step, key, executor=None):
        if not self.canDownload():
            return None

        loop = asyncio.get_event_loop()
        with stepAction(step, "MAP-FPRNT", self._remoteName(key, FINGERPRINT_SUFFIX)) as a:
            try:
                ret, msg, kind = await loop.run_in_executor(executor,
                    BaseArchive._downloadLocalFile, self, key, FINGERPRINT_SUFFIX)
                if ret is None: a.fail(msg, kind)
                return ret
            except (concurrent.futures.CancelledError, concurrent.futures.process.BrokenProcessPool):
                raise BuildError(self._namedErrorString("Download of fingerprint interrupted."))

    def deleteFile(self, filepath):
        try:
            self._delete(filepath)
        except (ArtifactDownloadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error ("+str(e)+")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Could not delete file: " + str(e)))

    def _delete(self, filepath):
        raise ArtifactDownloadError("not implemented")

    def listDir(self, path):
        try:
            return self._listDir(path)
        except (ArtifactDownloadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error (" + str(e) + ")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Could not list dir: " + str(e)))

    def _listDir(self, path):
        raise ArtifactDownloadError("not implemented")

    def stat(self, filepath):
        try:
            return self._stat(filepath)
        except (ArtifactDownloadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error (" + str(e) + ")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Could not stat file: " + str(e)))

    def _stat(self, filepath):
        raise ArtifactDownloadError("not implemented")

    def getAudit(self, filepath):
        try:
            return self._getAudit(filepath)
        except (ArtifactDownloadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error (" + str(e) + ")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Could not get audit from file: " + str(e)))

    def _getAudit(self, filepath):
        raise ArtifactDownloadError("not implemented")

    def getArchiveUri(self):
        try:
            return self._getArchiveUri()
        except (ArtifactDownloadError, OSError) as e:
            if self.__ignoreErrors:
                return (self._namedErrorString("error (" + str(e) + ")"), ERROR)
            else:
                raise BuildError(self._namedErrorString("Could not get archive hash: " + str(e)))

    def _getArchiveUri(self):
        raise ArtifactDownloadError("not implemented")

    def getArchiveName(self):
       return self.__name


class Tee:
    def __init__(self, fileName, fileObj, buildId, caches, workspace):
        if fileObj is not None:
            self.__file = fileObj
            self.__owner = False
        else:
            self.__file = open(fileName, "rb")
            self.__owner = True

        self.__caches = []
        try:
            for c in caches:
                mirror = c.cachePackage(buildId, workspace)
                if mirror is not None:
                    self.__caches.append(MirrorWriter(mirror, c.ignoreErrors))
        except:
            for c in self.__caches: c.abort()
            raise

    def __enter__(self):
        return MirrorLeecher(self.__file, self.__caches)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self.__owner: self.__file.close()
            if exc_type is None:
                while self.__caches:
                    c = self.__caches.pop(0)
                    try:
                        c.commit()
                    except ArtifactExistsError:
                        pass
                    except (ArtifactUploadError, OSError) as e:
                        if not c.ignoreErrors:
                            raise BuildError("Cannot cache artifact: " + str(e))
        finally:
            for c in self.__caches: c.abort()
        return False

class MirrorWriter:
    def __init__(self, uploader, ignoreErrors):
        self.ignoreErrors = ignoreErrors
        self.__finalizer = uploader.__exit__
        self.__fileName, self.__fileObj = uploader.__enter__()
        if self.__fileName is not None:
            self.__fileObj = open(self.__fileName, "wb")

    def write(self, data):
        self.__fileObj.write(data)

    def commit(self):
        if self.__fileName is not None:
            self.__fileObj.close()
        self.__finalizer(None, None, None)

    def abort(self):
        if self.__fileName is not None:
            self.__fileObj.close()
        self.__finalizer(True, None, None)

class MirrorLeecher:
    def __init__(self, fileObj, caches):
        self.__file = fileObj
        self.__caches = caches

    def read(self, size=-1):
        ret = self.__file.read(size)
        if ret:
            i = 0
            while i < len(self.__caches):
                c = self.__caches[i]
                try:
                    c.write(ret)
                    i += 1
                except (ArtifactUploadError, OSError) as e:
                    del self.__caches[i]
                    c.abort()
                    if not c.ignoreErrors:
                        raise BuildError("Cannot cache artifact: " + str(e))
        return ret

    def close(self):
        pass


class LocalArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__path = spec["path"]
        self.__basePath = os.path.abspath(os.path.expanduser(self.__path))
        self.__fileMode = spec.get("fileMode")
        self.__dirMode = spec.get("directoryMode")

    def getArchiveName(self):
        return super().getArchiveName() or self.__path

    def _canManage(self):
        return True

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

    def _openUploadFile(self, buildId, suffix, overwrite):
        (packageResultPath, packageResultFile) = self._getPath(buildId, suffix)
        if not overwrite and os.path.isfile(packageResultFile):
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
            self.__fileMode, packageResultFile, overwrite)

    def _delete(self, filename):
        try:
            os.unlink(os.path.join(self.__basePath, filename))
        except FileNotFoundError:
            pass
        except OSError as e:
            raise BuildError(self._namedErrorString("Cannot remove {}: {}".format(filename, str(e))))

    def _listDir(self, path):
        return os.listdir(os.path.join(self.__basePath, path))

    def _stat(self, filename):
        return binStat(os.path.join(self.__basePath, filename))

    def _getAudit(self, filename):
        return self._extractAudit(filename=os.path.join(self.__basePath, filename))

    def _getArchiveUri(self):
        return self.__basePath


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
    def __init__(self, tmp, fileMode, destination, overwrite):
        self.tmp = tmp
        self.fileMode = fileMode
        self.destination = destination
        self.overwrite = overwrite
    def __enter__(self):
        return (None, self.tmp)
    def __exit__(self, exc_type, exc_value, traceback):
        self.tmp.close()
        # atomically move file to destination at end of upload
        if exc_type is None:
            if not isWindows() and self.fileMode is not None:
                os.chmod(self.tmp.name, self.fileMode)

            if self.overwrite:
                os.replace(self.tmp.name, self.destination)
            elif not isWindows():
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


class HttpArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__url = urllib.parse.urlparse(spec["url"])
        self._webdav = WebDav(self.__url, spec.get("sslVerify", True))
        self._retries = spec.get("retries", 1)

    def getArchiveName(self):
        name = super().getArchiveName()
        if name:
            return name

        url = self.__url
        return urllib.parse.urlunparse((url.scheme, url.netloc, url.path, '', '', ''))

    def __retry(self, request):
        retries = self._retries
        while True:
            try:
                return request()
            except (HTTPException, OSError) as e:
                self._webdav._resetConnection()
                if retries == 0: raise e
                retries -= 1

    def _canManage(self):
        return True

    def _resetConnection(self):
        self._webdav._resetConnection()

    def _makePath(self, buildId, suffix):
        packageResultId = buildIdToName(buildId)
        return "/".join([self.__url.path, packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + suffix])

    def _makeParentDirs(self, path):
        (dirs, _, _) = path.rpartition("/")
        return self.__retry(lambda: self._webdav.mkdir(dirs, dirs.count("/")))

    def _remoteName(self, buildId, suffix):
        url = self.__url
        return urllib.parse.urlunparse((url.scheme, url.netloc, self._makePath(buildId, suffix), '', '', ''))

    def _exists(self, path):
        return self.__retry(lambda: self._webdav.exists(path))

    def _openDownloadFile(self, buildId, suffix):
        path = self._makePath(buildId, suffix)
        return self.__retry(lambda: self.__openDownloadFile(path))

    def __openDownloadFile(self, path):
        return HttpDownloader(self, self._webdav.download(path))

    def _openUploadFile(self, buildId, suffix, overwrite):
        path = self._makePath(buildId, suffix)
        # check if already there
        if not overwrite:
            if self._exists(path):
                raise ArtifactExistsError()
        # Quoting RFC 4918:
        #
        #   A PUT that would result in the creation of a resource without an
        #   appropriately scoped parent collection MUST fail with a 409
        #   (Conflict).
        #
        # We don't want to waste bandwith by uploading big artifacts into a
        # missing directory. Thus make sure the directory always exists.
        self._makeParentDirs(path)
        return HttpUploader(self, path, overwrite)

    def _putUploadFile(self, path, tmp, overwrite):
        return self.__retry(lambda: self._webdav.upload(path, tmp, overwrite))

    def _listDir(self, path):
        path_info = self.__retry(lambda: self._webdav.listdir(path))
        entries = []
        for info in path_info:
            if info["path"]:
                entries.append(removePrefix(info["path"], path + "/"))
        return entries

    def _delete(self, filename):
        self.__retry(lambda: self._webdav.delete(filename))

    def _stat(self, filename):
        stats = self.__retry(lambda: self._webdav.stat(filename))
        if stats['etag'] is not None and not stats['etag'].startswith('W/'):
            return stats['etag']
        if not stats['mdate'] or not stats['len']:
            raise ArtifactDownloadError("Missing stats for file " + filename)
        from email.utils import parsedate_to_datetime
        return struct.pack('=dL', parsedate_to_datetime(stats['mdate']).timestamp(), stats['len'])

    def _getAudit(self, filename):
        downloader = self.__retry(lambda: self._webdav.getPartialDownloader("/".join([self.__url.path, filename])))
        while True:
            try:
                file = io.BytesIO(downloader.get())
                return self._extractAudit(fileobj=file)
            except (EOFError, tarfile.ReadError):
                # partial downloader reached EOF or could not extract the audit from the tarfile, so we get more data
                self.__retry(lambda: downloader.more())
                pass

    def _getArchiveUri(self):
        return self.__url.netloc + self.__url.path


class HttpDownloader:
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


class HttpUploader:
    def __init__(self, archiver, url, overwrite):
        self.archiver = archiver
        self.tmp = TemporaryFile()
        self.url = url
        self.overwrite = overwrite
    def __enter__(self):
        return (None, self.tmp)
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            # do actual upload on regular handle close
            if exc_type is None:
                self.archiver._putUploadFile(self.url, self.tmp, self.overwrite)
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

    def canDownload(self):
        return super().canDownload() and (self.__downloadCmd is not None)

    def canUpload(self):
        return super().canUpload() and (self.__uploadCmd is not None)

    def _openDownloadFile(self, buildId, suffix):
        (tmpFd, tmpName) = mkstemp()
        url = self._makeUrl(buildId, suffix)
        try:
            os.close(tmpFd)
            env = { k:v for (k,v) in os.environ.items() if k in self.__whiteList }
            env["BOB_LOCAL_ARTIFACT"] = tmpName
            env["BOB_REMOTE_ARTIFACT"] = url
            ret = subprocess.call([getBashPath(), "-ec", self.__downloadCmd],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                cwd=gettempdir(), env=env)
            if ret == 0:
                ret = tmpName
                tmpName = None
                return CustomDownloader(ret)
            else:
                raise ArtifactDownloadError("failed (exit {})".format(ret))
        finally:
            if tmpName is not None: os.unlink(tmpName)

    def _openUploadFile(self, buildId, suffix, overwrite):
        (tmpFd, tmpName) = mkstemp()
        os.close(tmpFd)
        return CustomUploader(tmpName, self._makeUrl(buildId, suffix), self.__whiteList,
            self.__uploadCmd, overwrite)

class CustomDownloader:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        return (self.name, None)
    def __exit__(self, exc_type, exc_value, traceback):
        os.unlink(self.name)
        return False

class CustomUploader:
    def __init__(self, name, remoteName, whiteList, uploadCmd, overwrite):
        self.name = name
        self.remoteName = remoteName
        self.whiteList = whiteList
        self.uploadCmd = uploadCmd
        self.overwrite = overwrite

    def __enter__(self):
        return (self.name, None)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                env = { k:v for (k,v) in os.environ.items() if k in self.whiteList }
                env["BOB_LOCAL_ARTIFACT"] = self.name
                env["BOB_REMOTE_ARTIFACT"] = self.remoteName
                if self.overwrite:
                    env["BOB_REMOTE_OVERWRITE"] = "1"
                ret = subprocess.call([getBashPath(), "-ec", self.uploadCmd],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    cwd=gettempdir(), env=env)
                if ret != 0:
                    raise ArtifactUploadError("command return with status {}".format(ret))
        finally:
            os.unlink(self.name)
        return False


class AzureStreamReadAdapter(io.RawIOBase):
    def __init__(self, raw):
        super().__init__()
        self.raw = raw
    def readable(self):
        return True
    def seekable(self):
        return False
    def writable(self):
        return False
    def read(self, size = -1):
        return self.raw.read(size)
    def readall(self):
        return self.raw.read()
    def readinto(self, buf):
        data = self.raw.read(len(buf))
        buf[0:len(data)] = data
        return len(data)

class AzureArchive(BaseArchive):
    def __init__(self, spec):
        super().__init__(spec)
        self.__container = spec['container']
        self.__account = spec['account']
        self.__credential = spec.get('key', spec.get('sasToken'))

    def getArchiveName(self):
        name = super().getArchiveName()
        if name:
            return name

        return f"https://{self.__account}.blob.core.windows.net/{self.__container}"

    def __getClient(self):
        try:
            from azure.storage.blob import ContainerClient
        except ImportError:
            raise BuildError("azure-storage-blob Python3 library not installed!")
        return ContainerClient("https://{}.blob.core.windows.net".format(self.__account),
            self.__container, self.__credential)

    @staticmethod
    def __makeBlobName(buildId, suffix):
        packageResultId = buildIdToName(buildId)
        return "/".join([packageResultId[0:2], packageResultId[2:4],
            packageResultId[4:] + suffix])

    def _remoteName(self, buildId, suffix):
        return "https://{}.blob.core.windows.net/{}/{}".format(self.__account,
            self.__container, self.__makeBlobName(buildId, suffix))

    def _openDownloadFile(self, buildId, suffix):
        client = self.__getClient()
        from azure.core.exceptions import AzureError, ResourceNotFoundError
        try:
            stream = client.download_blob(self.__makeBlobName(buildId, suffix))
            stream = AzureStreamReadAdapter(stream) # Make io.RawIOBase compatible
            stream = io.BufferedReader(stream, 1048576) # 1MiB buffer. Azure read()s are synchronous.
            ret = AzureDownloader(client, stream)
            client = None
            return ret
        except ResourceNotFoundError:
            raise ArtifactNotFoundError()
        except AzureError as e:
            raise ArtifactDownloadError(str(e))
        finally:
            if client is not None: client.close()

    def _openUploadFile(self, buildId, suffix, overwrite):
        containerClient = self.__getClient()
        from azure.core.exceptions import AzureError
        blobName = self.__makeBlobName(buildId, suffix)
        blobClient = None
        try:
            blobClient = containerClient.get_blob_client(blobName)
            if not overwrite and blobClient.exists():
                raise ArtifactExistsError()
            ret = AzureUploader(containerClient, blobClient, overwrite)
            containerClient = blobClient = None
            return ret
        except AzureError as e:
            raise ArtifactUploadError(str(e))
        finally:
            if blobClient is not None: blobClient.close()
            if containerClient is not None: containerClient.close()

class AzureDownloader:
    def __init__(self, client, stream):
        self.__client = client
        self.__stream = stream
    def __enter__(self):
        return (None, self.__stream)
    def __exit__(self, exc_type, exc_value, traceback):
        self.__client.close()
        return False

class AzureUploader:
    def __init__(self, containerClient, blobClient, overwrite):
        self.__containerClient = containerClient
        self.__blobClient = blobClient
        self.__overwrite = overwrite

    def __enter__(self):
        self.__tmp = TemporaryFile()
        return (None, self.__tmp)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.__upload()
        finally:
            self.__tmp.close()
            self.__blobClient.close()
            self.__containerClient.close()
        return False

    def __upload(self):
        from azure.core.exceptions import AzureError, ResourceExistsError
        try:
            self.__tmp.seek(0, os.SEEK_END)
            length = self.__tmp.tell()
            self.__tmp.seek(0)
            self.__blobClient.upload_blob(self.__tmp, length=length, overwrite=self.__overwrite)
        except ResourceExistsError:
            raise ArtifactExistsError()
        except AzureError as e:
            raise ArtifactUploadError(str(e))


class MultiArchive:
    def __init__(self, archives):
        self.__archives = archives

    def wantDownloadLocal(self, enable):
        for i in self.__archives: i.wantDownloadLocal(enable)

    def wantDownloadJenkins(self, enable):
        for i in self.__archives: i.wantDownloadJenkins(enable)

    def wantUploadLocal(self, enable):
        for i in self.__archives: i.wantUploadLocal(enable)

    def wantUploadJenkins(self, enable):
        for i in self.__archives: i.wantUploadJenkins(enable)

    def canDownload(self):
        return any(i.canDownload() for i in self.__archives)

    def canUpload(self):
        return any(i.canUpload() for i in self.__archives)

    async def uploadPackage(self, step, buildId, audit, content, executor=None):
        for i in self.__archives:
            if not i.canUpload(): continue
            await i.uploadPackage(step, buildId, audit, content, executor=executor)

    async def downloadPackage(self, step, buildId, audit, content, executor=None):
        for i in self.__archives:
            if not i.canDownload(): continue
            caches = [ a for a in self.__archives if (a is not i) and a.canCache() ]
            if await i.downloadPackage(step, buildId, audit, content, caches, executor):
                return True
        return False

    async def uploadLocalLiveBuildId(self, step, liveBuildId, buildId, executor=None):
        for i in self.__archives:
            if not i.canUpload(): continue
            await i.uploadLocalLiveBuildId(step, liveBuildId, buildId, executor=executor)

    async def downloadLocalLiveBuildId(self, step, liveBuildId, executor=None):
        ret = None
        for i in self.__archives:
            if not i.canDownload(): continue
            ret = await i.downloadLocalLiveBuildId(step, liveBuildId, executor=executor)
            if ret is not None: break
        return ret

    async def uploadLocalFingerprint(self, step, key, fingerprint, executor=None):
        for i in self.__archives:
            if not i.canUpload(): continue
            await i.uploadLocalFingerprint(step, key, fingerprint, executor=executor)

    async def downloadLocalFingerprint(self, step, key, executor=None):
        ret = None
        for i in self.__archives:
            if not i.canDownload(): continue
            ret = await i.downloadLocalFingerprint(step, key, executor=executor)
            if ret is not None: break
        return ret


def getSingleArchiver(recipes, archiveSpec):
    archiveBackend = archiveSpec.get("backend", "none")
    if archiveBackend == "file":
        return LocalArchive(archiveSpec)
    elif archiveBackend == "http":
        return HttpArchive(archiveSpec)
    elif archiveBackend == "shell":
        return CustomArchive(archiveSpec, recipes.envWhiteList())
    elif archiveBackend == "azure":
        return AzureArchive(archiveSpec)
    elif archiveBackend == "none":
        return DummyArchive()
    elif archiveBackend == "__jenkins":
        return JenkinsArchive(archiveSpec)
    else:
        raise BuildError("Invalid archive backend: "+archiveBackend)

def getArchiver(recipes, jenkins=None):
    archiveSpec = recipes.archiveSpec()
    if jenkins is not None:
        jenkins = jenkins.copy()
        jenkins["backend"] = "__jenkins"
        if isinstance(archiveSpec, list):
            archiveSpec = [jenkins] + archiveSpec
        else:
            archiveSpec = [jenkins, archiveSpec]

    if isinstance(archiveSpec, list):
        if len(archiveSpec) == 0:
            return DummyArchive()
        elif len(archiveSpec) == 1:
            return getSingleArchiver(recipes, archiveSpec[0])
        else:
            return MultiArchive([ getSingleArchiver(recipes, i) for i in archiveSpec ])
    else:
        return getSingleArchiver(recipes, archiveSpec)
