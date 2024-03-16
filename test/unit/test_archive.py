# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from binascii import hexlify
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
import asyncio
import base64
import http.server
import os, os.path
import socketserver
import stat
import subprocess
import tarfile
import threading
import urllib.parse
import sys

from bob.archive import DummyArchive, SimpleHttpArchive, getArchiver
from bob.errors import BuildError
from bob.utils import runInEventLoop, getProcessPoolExecutor

DOWNLOAD_ARITFACT = b'\x00'*20
NOT_EXISTS_ARTIFACT = b'\x01'*20
WRONG_VERSION_ARTIFACT = b'\x02'*20
ERROR_UPLOAD_ARTIFACT = b'\x03'*20
ERROR_DOWNLOAD_ARTIFACT = b'\x04'*20
BROKEN_ARTIFACT = b'\xba\xdc\x0f\xfe'*5

UPLOAD1_ARTIFACT = b'\x10'*20
UPLOAD2_ARTIFACT = b'\x11'*20

class DummyRecipeSet:
    def __init__(self, archive, whiteList=[]):
        self.__archive = archive
        self.__whiteList = whiteList
    def archiveSpec(self):
        return self.__archive
    def envWhiteList(self):
        return set(self.__whiteList)
    def getPolicy(self, policy):
        return None

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()
    def getWorkspacePath(self):
        return "unused"

def run(coro):
    with patch('bob.archive.signal.signal'):
        return runInEventLoop(coro)

class BaseTester:

    def __createArtifact(self, bid, version="1"):
        bid = hexlify(bid).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        return self.__createArtifactByName(name, version)

    def __createArtifactByName(self, name, version="1"):
        pax = { 'bob-archive-vsn' : version }
        with tarfile.open(name, "w|gz", format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
            with NamedTemporaryFile() as audit:
                audit.write(b'AUDIT')
                audit.seek(0)
                tar.addfile(tar.gettarinfo(arcname="meta/audit.json.gz", fileobj=audit), audit)
            with TemporaryDirectory() as content:
                with open(os.path.join(content, "data"), "wb") as f:
                    f.write(b'DATA')
                tar.add(content, "content")

        return name

    def __createBuildId(self, bid):
        bid = hexlify(bid).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.buildid")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "wb") as f:
            f.write(b'\x00'*20)
        return name

    def __testArtifact(self, bid):
        bid = hexlify(bid).decode("ascii")
        artifact = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")
        return self.__testArtifactByName(artifact)

    def __testArtifactByName(self, artifact):
        self.assertTrue(os.path.exists(artifact))

        # try to dissect
        with tarfile.open(artifact, errorlevel=1) as tar:
            self.assertEqual(tar.pax_headers.get('bob-archive-vsn'), "1")

            # find audit trail
            f = tar.next()
            foundAudit = False
            foundData = False
            while f:
                if f.name == "meta/audit.json.gz":
                    self.assertEqual(tar.extractfile(f).read(), b"AUDIT")
                    foundAudit = True
                elif f.name == "content/data":
                    self.assertEqual(tar.extractfile(f).read(), b"DATA")
                    foundData = True
                elif f.name == "content":
                    pass
                else:
                    self.fail(f.name)
                f = tar.next()

        # make sure we got all that is expected
        self.assertTrue(foundAudit)
        self.assertTrue(foundData)

    def __testBuildId(self, bid, content):
        bid = hexlify(bid).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.buildid")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "rb") as f:
            self.assertEqual(f.read(), content)

    def __testWorkspace(self, audit, workspace):
        with open(audit, "rb") as f:
            self.assertEqual(f.read(), b'AUDIT')
        with open(os.path.join(workspace, "data"), "rb") as f:
            self.assertEqual(f.read(), b'DATA')

    def __getArchiveInstance(self, spec):
        # let concrete class amend properties
        self._setArchiveSpec(spec)

        # We create a multi-archive with a dummy backend and the real one. This
        # way we implicitly test the MultiArchive too.
        recipes = DummyRecipeSet([ { 'backend' : 'none' }, spec ])
        return getArchiver(recipes)

    def __getSingleArchiveInstance(self, spec):
        # let concrete class amend properties
        self._setArchiveSpec(spec)
        recipes = DummyRecipeSet(spec)
        return getArchiver(recipes)

    def setUp(self):
        # create repo
        self.repo = TemporaryDirectory()

        # add artifacts
        self.dummyFileName = self.__createArtifact(DOWNLOAD_ARITFACT)
        self.__createArtifact(WRONG_VERSION_ARTIFACT, "0")
        self.__createBuildId(DOWNLOAD_ARITFACT)

        # create ERROR_DOWNLOAD_ARTIFACT that is there but cannot be opened
        bid = hexlify(ERROR_DOWNLOAD_ARTIFACT).decode("ascii")
        os.makedirs(os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz"), exist_ok=True)
        os.makedirs(os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.buildid"), exist_ok=True)

        # make sure ERROR_UPLOAD_ARTIFACT cannot be created
        bid = hexlify(ERROR_UPLOAD_ARTIFACT).decode("ascii")
        os.makedirs(os.path.join(self.repo.name, bid[0:2]), exist_ok=True)
        with open(os.path.join(self.repo.name, bid[0:2], bid[2:4]), "wb") as f:
            pass

        # create broken artifact
        bid = hexlify(BROKEN_ARTIFACT).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "wb") as f:
            f.write(b'\x00')

        self.executor = getProcessPoolExecutor()

    def tearDown(self):
        self.executor.shutdown()
        self.repo.cleanup()

    # standard tests for options
    def testOptions(self):
        """Test that wantDownload/wantUpload options work"""

        a = self.__getArchiveInstance({})
        self.assertFalse(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantDownloadLocal(True)
        self.assertTrue(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantUploadLocal(True)
        self.assertFalse(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantDownloadJenkins(True)
        self.assertTrue(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantUploadJenkins(True)
        self.assertFalse(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

    def testFlags(self):
        """Test that standard flags work"""

        # Local up/download

        a = self.__getArchiveInstance({"flags":["download"]})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertTrue(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({"flags":["upload"]})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertFalse(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({"flags":["download", "upload"]})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

        # Jenkins up/download

        a = self.__getArchiveInstance({"flags":["download"]})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertTrue(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({"flags":["upload"]})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertFalse(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({"flags":["download", "upload"]})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

        # No local up/download

        a = self.__getArchiveInstance({"flags":["download", "upload", "nolocal"]})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertFalse(a.canDownload())
        self.assertFalse(a.canUpload())

        a = self.__getArchiveInstance({"flags":["download", "upload", "nolocal"]})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

        # No Jenkins up/download

        a = self.__getArchiveInstance({"flags":["download", "upload", "nojenkins"]})
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertTrue(a.canDownload())
        self.assertTrue(a.canUpload())

        a = self.__getArchiveInstance({"flags":["download", "upload", "nojenkins"]})
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertFalse(a.canDownload())
        self.assertFalse(a.canUpload())

    def testDisabledLocal(self):
        """Disabled local must not do anything"""
        a = self.__getArchiveInstance({})
        self.assertFalse(run(a.downloadPackage(DummyStep(), b'\xcc'*20, "unused", "unused", executor=self.executor)))
        self.assertFalse(run(a.uploadPackage(DummyStep(), b'\xcc'*20, "unused", "unused", executor=self.executor)))
        self.assertEqual(run(a.downloadLocalLiveBuildId(DummyStep(), b'\xcc'*20, executor=self.executor)), None)
        run(a.uploadLocalLiveBuildId(DummyStep(), b'\xcc'*20, b'\xcc', executor=self.executor))

    def __testDownload(self, archive):
        self.assertTrue(archive.canDownload())

        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            self.assertTrue(run(archive.downloadPackage(DummyStep(), DOWNLOAD_ARITFACT, audit, content, executor=self.executor)))
            self.__testWorkspace(audit, content)
            self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), DOWNLOAD_ARITFACT, executor=self.executor)), b'\x00'*20)

        # non-existent and erro cases
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            self.assertFalse(run(archive.downloadPackage(DummyStep(), NOT_EXISTS_ARTIFACT, audit, content, executor=self.executor)))
            self.assertFalse(run(archive.downloadPackage(DummyStep(), ERROR_DOWNLOAD_ARTIFACT, audit, content, executor=self.executor)))
            self.assertFalse(run(archive.downloadPackage(DummyStep(), ERROR_UPLOAD_ARTIFACT, audit, content, executor=self.executor)))
            self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), NOT_EXISTS_ARTIFACT, executor=self.executor)), None)
            self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), ERROR_DOWNLOAD_ARTIFACT, executor=self.executor)), None)
            self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), ERROR_UPLOAD_ARTIFACT, executor=self.executor)), None)
            with self.assertRaises(BuildError):
                run(archive.downloadPackage(DummyStep(), BROKEN_ARTIFACT, audit, content, executor=self.executor))
            with self.assertRaises(BuildError):
                run(archive.downloadPackage(DummyStep(), WRONG_VERSION_ARTIFACT, audit, content, executor=self.executor))

    def __testUploadNormal(self, archive):
        self.assertTrue(archive.canUpload())

        with TemporaryDirectory() as tmp:
            # create simple workspace
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            with open(audit, "wb") as f:
                f.write(b"AUDIT")
            os.mkdir(content)
            with open(os.path.join(content, "data"), "wb") as f:
                f.write(b"DATA")

            # upload
            run(archive.uploadPackage(DummyStep(), DOWNLOAD_ARITFACT, audit, content, executor=self.executor)) # exists alread

            bid = UPLOAD1_ARTIFACT
            run(archive.uploadPackage(DummyStep(), bid, audit, content, executor=self.executor))
            self.__testArtifact(bid)

            bid = UPLOAD2_ARTIFACT
            run(archive.uploadPackage(DummyStep(), bid, audit, content, executor=self.executor))
            self.__testArtifact(bid)

            # Provoke upload failure
            with self.assertRaises(BuildError):
                run(archive.uploadPackage(DummyStep(), ERROR_UPLOAD_ARTIFACT, audit, content, executor=self.executor))

        # regular live-build-id uploads
        run(archive.uploadLocalLiveBuildId(DummyStep(), DOWNLOAD_ARITFACT, b'\x00', executor=self.executor)) # exists already
        run(archive.uploadLocalLiveBuildId(DummyStep(), UPLOAD1_ARTIFACT, b'\x00', executor=self.executor))
        self.__testBuildId(UPLOAD1_ARTIFACT, b'\x00')
        run(archive.uploadLocalLiveBuildId(DummyStep(), UPLOAD2_ARTIFACT, b'\x00', executor=self.executor))
        self.__testBuildId(UPLOAD2_ARTIFACT, b'\x00')

        # Live-build-id can be replaced
        run(archive.uploadLocalLiveBuildId(DummyStep(), UPLOAD2_ARTIFACT, b'\x11', executor=self.executor))
        self.__testBuildId(UPLOAD2_ARTIFACT, b'\x11')

        # provoke upload errors
        with self.assertRaises(BuildError):
            run(archive.uploadLocalLiveBuildId(DummyStep(), ERROR_UPLOAD_ARTIFACT, b'\x00', executor=self.executor))

    def __testUploadNoFail(self, archive):
        self.assertTrue(archive.canUpload())

        with TemporaryDirectory() as tmp:
            # create simple workspace
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            with open(audit, "wb") as f:
                f.write(b"AUDIT")
            os.mkdir(content)
            with open(os.path.join(content, "data"), "wb") as f:
                f.write(b"DATA")

            # must not throw
            run(archive.uploadPackage(DummyStep(), ERROR_UPLOAD_ARTIFACT, audit, content, executor=self.executor))

        # also live-build-id upload errors must not throw with nofail
        run(archive.uploadLocalLiveBuildId(DummyStep(), ERROR_UPLOAD_ARTIFACT, b'\x00', executor=self.executor))

    def testDownloadLocal(self):
        """Local download tests"""

        archive = self.__getArchiveInstance({})
        archive.wantDownloadLocal(True)
        self.__testDownload(archive)

    def testUploadLocalNormal(self):
        """Local upload tests"""

        archive = self.__getArchiveInstance({})
        archive.wantUploadLocal(True)
        self.__testUploadNormal(archive)

    def testUploadPackageNoFail(self):
        """The nofail option must prevent fatal error on upload failures"""

        archive = self.__getArchiveInstance({"flags" : ["upload", "download", "nofail"]})
        archive.wantUploadLocal(True)
        self.__testUploadNoFail(archive)

    def testDownloadJenkins(self):
        """Jenkins download tests"""

        archive = self.__getArchiveInstance({})
        archive.wantDownloadJenkins(True)
        self.__testDownload(archive)

    def testUploadJenkinsNormal(self):
        """Jenkins upload tests"""

        archive = self.__getArchiveInstance({})
        archive.wantUploadJenkins(True)
        self.__testUploadNormal(archive)

    def testUploadJenkinsNoFail(self):
        """The nofail option must prevent fatal error on upload failures"""

        archive = self.__getArchiveInstance({"flags" : ["upload", "download", "nofail"]})
        archive.wantUploadJenkins(True)
        self.__testUploadNoFail(archive)

    def testDisabled(self):
        """Test that nothing is done if up/download is disabled"""

        archive = self.__getSingleArchiveInstance({})

        run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor)), None)
        run(archive.uploadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        run(archive.uploadLocalLiveBuildId(DummyStep(), b'\x00'*20, b'\x00'*20, executor=self.executor))


class TestDummyArchive(TestCase):

    def testOptionsLocal(self):
        a = DummyArchive()
        a.wantDownloadLocal(True)
        a.wantUploadLocal(True)
        self.assertFalse(a.canDownload())
        self.assertFalse(a.canUpload())

    def testOptionsJenkins(self):
        a = DummyArchive()
        a.wantDownloadJenkins(True)
        a.wantUploadJenkins(True)
        self.assertFalse(a.canDownload())
        self.assertFalse(a.canUpload())

    def testDownloadLocal(self):
        run(DummyArchive().downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused"))
        self.assertEqual(run(DummyArchive().downloadLocalLiveBuildId(DummyStep(), b'\x00'*20)), None)
        self.assertEqual(run(DummyArchive().downloadLocalFingerprint(DummyStep(), b'\x00'*20)), None)

    def testUploadLocal(self):
        run(DummyArchive().uploadPackage(DummyStep(), b'\x00'*20, "unused", "unused"))
        run(DummyArchive().uploadLocalLiveBuildId(DummyStep(), b'\x00'*20, b'\x00'*20))
        run(DummyArchive().uploadLocalFingerprint(DummyStep(), b'\x00'*20, b'\x00'*20))


def createHttpHandler(repoPath, username=None, password=None):

    class Handler(http.server.BaseHTTPRequestHandler):

        def getCommon(self):
            if username is not None:
                challenge = 'Basic ' + base64.b64encode(
                    (username+":"+password).encode("utf-8")).decode("ascii")
                if self.headers.get('Authorization') != challenge:
                    self.send_error(401, "Unauthorized")
                    self.send_header("WWW-Authenticate", 'Basic realm="default"')
                    self.end_headers()
                    return None

            path = repoPath + self.path
            try:
                f = open(path, "rb")
            except FileNotFoundError:
                self.send_error(404, "not found")
                return None
            except OSError:
                self.send_error(500, "internal error")
                return None

            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.end_headers()
            return f

        def do_HEAD(self):
            f = self.getCommon()
            if f: f.close()

        def do_GET(self):
            f = self.getCommon()
            if f:
                self.wfile.write(f.read())
                f.close()

        def do_PUT(self):
            length = int(self.headers['Content-Length'])
            content  = self.rfile.read(length)

            exists = False
            path = repoPath + self.path
            if os.path.exists(path):
                if "If-None-Match" in self.headers:
                    self.send_response(412)
                    self.end_headers()
                    return
                else:
                    exists = True

            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
                self.send_response(200 if exists else 201)
                self.end_headers()
            except OSError:
                self.send_error(500, "internal error")

    return Handler

class TestLocalArchive(BaseTester, TestCase):

    def _setArchiveSpec(self, spec):
        spec['backend'] = "file"
        spec["path"] = self.repo.name


class TestHttpArchive(BaseTester, TestCase):

    def setUp(self):
        super().setUp()
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0), createHttpHandler(self.repo.name))
        self.ip, self.port = self.httpd.server_address
        self.server = threading.Thread(target=self.httpd.serve_forever)
        self.server.daemon = True
        self.server.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def _setArchiveSpec(self, spec):
        spec['backend'] = "http"
        spec["url"] = "http://{}:{}".format(self.ip, self.port)

    def testInvalidServer(self):
        """Test download on non-existent server"""

        spec = { 'url' : "https://127.1.2.3:7257" }
        archive = SimpleHttpArchive(spec)
        archive.wantDownloadLocal(True)
        archive.wantUploadLocal(True)

        # Local
        run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor)), None)

class TestHttpBasicAuthArchive(BaseTester, TestCase):

    USERNAME = "bob"
    PASSWORD = "jd64&dm"

    def setUp(self):
        super().setUp()
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createHttpHandler(self.repo.name, self.USERNAME, self.PASSWORD))
        self.ip, self.port = self.httpd.server_address
        self.server = threading.Thread(target=self.httpd.serve_forever)
        self.server.daemon = True
        self.server.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def _setArchiveSpec(self, spec, password = None):
        spec['backend'] = "http"
        spec["url"] = "http://{}:{}@{}:{}".format(urllib.parse.quote(self.USERNAME),
            urllib.parse.quote(password or self.PASSWORD), self.ip, self.port)

    def testUnauthorized(self):
        """Test download on non-existent server"""

        spec = { }
        self._setArchiveSpec(spec, "wrong_password")
        archive = SimpleHttpArchive(spec)
        archive.wantDownloadLocal(True)
        archive.wantUploadLocal(True)

        run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor)), None)

@skipIf(sys.platform.startswith("win"), "requires POSIX platform")
class TestCustomArchive(BaseTester, TestCase):

    def _setArchiveSpec(self, spec):
        spec['backend'] = "shell"
        spec["download"] = "cp {}/$BOB_REMOTE_ARTIFACT $BOB_LOCAL_ARTIFACT".format(self.repo.name)
        spec["upload"] = "mkdir -p {P}/${{BOB_REMOTE_ARTIFACT%/*}} && cp $BOB_LOCAL_ARTIFACT {P}/$BOB_REMOTE_ARTIFACT".format(P=self.repo.name)

