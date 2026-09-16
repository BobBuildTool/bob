# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from binascii import hexlify
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile
from unittest import TestCase, skipIf
from unittest.mock import patch
import asyncio
import base64
import gzip
import hashlib
import http.server
import json
import pickle
import os, os.path
import socketserver
import stat
import subprocess
import tarfile
import threading
import urllib.parse
import sys
from mocks.http_server import HttpServerMock

from bob.archive import DummyArchive, HttpArchive, GiteaArchive, getArchiver, \
    ArtifactExistsError, ARTIFACT_SUFFIX
from bob.errors import BobError, BuildError
from bob.utils import runInEventLoop, getProcessPoolExecutor
from bob.webdav import WebdavError, WebdavNotFoundError, WebdavAlreadyExistsError, \
    getNetLoc

DOWNLOAD_ARITFACT = b'\x00'*20
NOT_EXISTS_ARTIFACT = b'\x01'*20
WRONG_VERSION_ARTIFACT = b'\x02'*20
ERROR_UPLOAD_ARTIFACT = b'\x03'*20
ERROR_DOWNLOAD_ARTIFACT = b'\x04'*20
BROKEN_ARTIFACT = b'\xba\xdc\x0f\xfe'*5
VALID_ARTIFACT = b'\x05'*20
EMPTY_AUDIT = '{"artifact":{"variant-id":"1","build-id":"1","artifact-id":"1","result-hash":"1","meta":"1","build":"1","dependencies":{}}, "references":[]}'

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

class Base:

    def _createArtifact(self, bid, version="1", valid_data=False):
        bid = hexlify(bid).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        return self.__createArtifactByName(name, version, valid_data)

    def __createArtifactByName(self, name, version="1", valid_data=False):
        pax = { 'bob-archive-vsn' : version }
        with tarfile.open(name, "w|gz", format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
            with NamedTemporaryFile() as audit:
                if valid_data:
                    # add valid empty audit file
                    gzf = gzip.GzipFile(mode='wb', fileobj=audit)
                    gzf.write(EMPTY_AUDIT.encode('utf-8'))
                    gzf.close()
                else:
                    audit.write(b'AUDIT')
                audit.seek(0)
                tar.addfile(tar.gettarinfo(arcname="meta/audit.json.gz", fileobj=audit), audit)
            with TemporaryDirectory() as content:
                with open(os.path.join(content, "data"), "wb") as f:
                    f.write(b'DATA')
                tar.add(content, "content")

        return name

    def _createBuildId(self, bid):
        bid = hexlify(bid).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.buildid")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "wb") as f:
            f.write(b'\x00'*20)
        return name

    def setUp(self):
        # create repo
        self.repo = TemporaryDirectory()
        self.executor = getProcessPoolExecutor()

    def tearDown(self):
        self.executor.shutdown()
        self.repo.cleanup()

class BaseTester(Base):

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
        super().setUp()

        # add artifacts
        self.dummyFileName = self._createArtifact(DOWNLOAD_ARITFACT)
        self._createArtifact(WRONG_VERSION_ARTIFACT, "0")
        self._createBuildId(DOWNLOAD_ARITFACT)
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
                    self.send_response(401, "Unauthorized")
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
            path = repoPath + self.path
            # handle folder
            if path.endswith('/'):
                if os.path.exists(path):
                    self.send_response(200)
                    self.end_headers()
                else:
                    self.send_error(404, "Not found")
            # handle file
            else:
                f = self.getCommon()
                if f: f.close()

        def do_GET(self):
            path = repoPath + self.path
            # handle folder
            if path.endswith('/'):
                if os.path.exists(path):
                    self.send_response(200)
                    # just a random answer for directory
                    self.wfile.write(path + " is doing fine")
                    self.end_headers()
                else:
                    self.send_error(404, "Not found")
            # handle file
            else:
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

            if not os.path.isdir(os.path.dirname(path)):
                self.send_response(409)
                self.end_headers()
                return

            try:
                with open(path, "wb") as f:
                    f.write(content)
                self.send_response(200 if exists else 201)
                self.end_headers()
            except OSError:
                self.send_error(500, "internal error")

        def do_MKCOL(self):
            path = repoPath + self.path

            if os.path.exists(path):
                self.send_response(405)
                self.end_headers()
                return

            path = path.rstrip("/")

            parent, _ = os.path.split(path)
            if not os.path.isdir(parent):
                self.send_response(409)
                self.end_headers()
                return

            try:
                os.mkdir(path)
                self.send_response(201)
            except OSError:
                self.send_response(403)
            self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header('DAV', '1,2')
            self.send_header('Allow', "OPTIONS, GET, HEAD, PUT")
            self.end_headers()

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
        spec['name'] = "remote"
        spec['backend'] = "http"
        spec["url"] = "http://{}:{}".format(self.ip, self.port)

    def testInvalidServer(self):
        """Test download on non-existent server"""

        spec = { 'url' : "https://127.1.2.3:7257" }
        archive = HttpArchive(spec)
        archive.wantDownloadLocal(True)
        archive.wantUploadLocal(True)

        # Local
        run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor)), None)

    def testInvalidServerFail(self):
        """Test download on non-existent server w/ strictdownload"""

        spec = { 'url' : "https://127.1.2.3:7257", 'flags' : ["download", "strictdownload"] }
        archive = HttpArchive(spec)
        archive.wantDownloadLocal(True)
        archive.wantUploadLocal(True)

        with self.assertRaises(BuildError):
            run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        with self.assertRaises(BuildError):
            run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor))

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
        archive = HttpArchive(spec)
        archive.wantDownloadLocal(True)
        archive.wantUploadLocal(True)

        run(archive.downloadPackage(DummyStep(), b'\x00'*20, "unused", "unused", executor=self.executor))
        self.assertEqual(run(archive.downloadLocalLiveBuildId(DummyStep(), b'\x00'*20, executor=self.executor)), None)

    def testNoCredentialsInMessages(self):
        """The URL credentials must never show up in user visible strings.

        Everything that is derived from the URL ends up in log messages and
        therefore in build logs and CI consoles.
        """
        spec = { }
        self._setArchiveSpec(spec)
        archive = HttpArchive(spec)

        for name, uri in [
                ("getArchiveName", archive.getArchiveName()),
                ("getArchiveUri", archive.getArchiveUri()),
                ("_remoteName", archive._remoteName(DOWNLOAD_ARITFACT, ".tgz")),
            ]:
            with self.subTest(method=name):
                self.assertNotIn(self.PASSWORD, uri)
                self.assertNotIn(urllib.parse.quote(self.PASSWORD), uri)
                self.assertNotIn("@", uri)
                # ...but the host must still be there to be of any use
                self.assertIn("{}:{}".format(self.ip, self.port), uri)


class TestGetNetLoc(TestCase):
    """Unit tests for the URL credential sanitizer."""

    def testNoCredentials(self):
        url = urllib.parse.urlparse("https://host.test:8443/path")
        self.assertEqual(getNetLoc(url), "host.test:8443")

    def testCredentialsRemoved(self):
        url = urllib.parse.urlparse("https://user:pass@host.test:8443/path")
        self.assertEqual(getNetLoc(url), "host.test:8443")

    def testPasswordWithAtSign(self):
        """urlparse() delimits at the *last* '@'. We must cut at the same one."""
        url = urllib.parse.urlparse("https://user:p@ssw@rd@host.test/path")
        self.assertEqual(url.hostname, "host.test")
        self.assertEqual(getNetLoc(url), "host.test")

    def testIPv6(self):
        url = urllib.parse.urlparse("https://user:pass@[::1]:8443/path")
        self.assertEqual(getNetLoc(url), "[::1]:8443")


@skipIf(sys.platform.startswith("win"), "requires POSIX platform")
class TestCustomArchive(BaseTester, TestCase):

    def _setArchiveSpec(self, spec):
        spec['backend'] = "shell"
        spec["download"] = "cp {}/$BOB_REMOTE_ARTIFACT $BOB_LOCAL_ARTIFACT".format(self.repo.name)
        spec["upload"] = "mkdir -p {P}/${{BOB_REMOTE_ARTIFACT%/*}} && cp $BOB_LOCAL_ARTIFACT {P}/$BOB_REMOTE_ARTIFACT".format(P=self.repo.name)

class TestHttpArchiveRetries(Base, TestCase):

    def setUp(self):
        super().setUp()
        self.VALID_FILE = self._createArtifact(VALID_ARTIFACT, valid_data=True)
        self.spec = {'backend' : 'http', 'name' : 'http-archive', 'flags' : ['download', 'upload', 'managed']}

    def _getHttpArchiveInstance(self, port):
        self.spec["url"] = "http://localhost:{}/".format(port)
        recipes = DummyRecipeSet(self.spec)
        return getArchiver(recipes)

    def _testRetries(self, r):
        self.spec['retries'] = r
        # server will fail as often as retries in spec
        with HttpServerMock(repoPath=self.repo.name, retries=r) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            archive.wantDownloadLocal(True)
            with TemporaryDirectory() as tmp:
                audit = os.path.join(tmp, "audit.json.gz")
                content = os.path.join(tmp, "workspace")
                self.assertTrue(run(archive.downloadPackage(DummyStep(), VALID_ARTIFACT, audit, content,
                                                            executor=self.executor)))
        # server fails one more time than retries in archive sepc -> fail
        with HttpServerMock(repoPath=self.repo.name, retries=r+1) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            archive.wantDownloadLocal(True)
            with TemporaryDirectory() as tmp:
                audit = os.path.join(tmp, "audit.json.gz")
                content = os.path.join(tmp, "workspace")
                self.assertFalse(run(archive.downloadPackage(DummyStep(), VALID_ARTIFACT, audit, content,
                                                             executor=self.executor)))

    def testRetriesWithNoRetries(self):
        self._testRetries(0)

    def testRetriesWithOneRetry(self):
        self._testRetries(1)

    def testRetriesWithMultipleRetries(self):
        self._testRetries(5)

    def testRetriesList(self):
        """Listing also provides the stat of each found package."""
        self.spec['retries'] = 1
        with HttpServerMock(repoPath=self.repo.name, retries=1) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            found = []
            archive._list(lambda bid, path, st: found.append((bid, st)), ARTIFACT_SUFFIX)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0][0], VALID_ARTIFACT)
            self.assertIsNotNone(found[0][1])
        with HttpServerMock(repoPath=self.repo.name, retries=2) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            with self.assertRaises(WebdavError):
                archive._list(lambda bid, path, st: None, ARTIFACT_SUFFIX)

    def testRetriesDelete(self):
        self.spec['retries'] = 1
        with HttpServerMock(repoPath=self.repo.name, retries=1) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            self.assertIsNone(archive._delete(VALID_ARTIFACT, ARTIFACT_SUFFIX))
        self._createArtifact(VALID_ARTIFACT, valid_data=True)
        with HttpServerMock(repoPath=self.repo.name, retries=2) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            with self.assertRaises(WebdavError):
                archive._delete(VALID_ARTIFACT, ARTIFACT_SUFFIX)

    def testRetriesAudit(self):
        self.spec['retries'] = 1
        with HttpServerMock(repoPath=self.repo.name, retries=1) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            self.assertIsNotNone(archive._getAudit(VALID_ARTIFACT))
        with HttpServerMock(repoPath=self.repo.name, retries=2) as srv:
            archive = self._getHttpArchiveInstance(srv.port)
            with self.assertRaises(WebdavError):
                archive._getAudit(VALID_ARTIFACT)


def createGiteaHandler(repoPath, args, expectedAuth=None):
    """Mock of the Gitea 'generic' package registry.

    Requests use the generic package layout

        /api/packages/<owner>/generic/<package>/<version>/<filename>

    which this handler translates to the on-disk layout used by the
    BaseTester helpers (``<repo>/<id[0:2]>/<id[2:4]>/<id[4:]>-1<suffix>``) so
    that all the shared upload/download assertions apply unchanged.

    The package API that the managed operations use is served too:

        /api/v1/packages/<owner>/generic/<package>[/<version>/files]

    If ``expectedAuth`` is given it is the full ``Authorization`` header value
    (i.e. ``"Basic ..."``) that the client must send; anything else is answered
    with ``401``.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _authOk(self):
            if expectedAuth is None:
                return True
            if self.headers.get('Authorization') == expectedAuth:
                return True
            self.send_response(401, "Unauthorized")
            self.end_headers()
            return False

        def _diskPath(self):
            fname = self.path.rsplit("/", 1)[-1]
            for suffix in (".tgz", ".buildid", ".fprnt"):
                if fname.endswith(suffix):
                    break
            else:
                return None
            stem = fname[:-len(suffix)]        # <id>-1
            ident = stem[:-len("-1")]          # <id> (strip archive generation)
            return os.path.join(repoPath, ident[0:2], ident[2:4],
                                stem[4:] + suffix)

        def _maybeFail(self):
            if args.get("retries", 0) > 0:
                args["retries"] -= 1
                self.send_error(500, "flaky")
                return True
            return False

        def _allVersions(self):
            """All package versions, derived from the on-disk layout."""
            versions = set()
            for root, dirs, files in os.walk(repoPath):
                l2 = os.path.basename(root)
                l1 = os.path.basename(os.path.dirname(root))
                if len(l1) != 2 or len(l2) != 2: continue
                for f in files:
                    stem, _, ext = f.rpartition(".")
                    if ext in ("tgz", "buildid", "fprnt"):
                        versions.add(l1 + l2 + stem)
            return sorted(versions)

        def _versionFiles(self, version):
            ident = version[:-len("-1")]
            files = []
            for suffix in (".tgz", ".buildid", ".fprnt"):
                path = os.path.join(repoPath, ident[0:2], ident[2:4],
                                    ident[4:] + "-1" + suffix)
                if not os.path.isfile(path): continue
                with open(path, "rb") as f:
                    data = f.read()
                files.append({ "name" : version + suffix, "size" : len(data),
                               "sha256" : hashlib.sha256(data).hexdigest() })
            return files

        def _apiGet(self):
            """Serve the package API. Returns False for ordinary requests."""
            path, _, query = self.path.partition("?")
            if not path.startswith("/api/v1/packages/"):
                return False

            if args.get("badApi"):
                # a server that does not speak the package API at all
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>no idea what you want</html>")
                return True

            parts = path.strip("/").split("/")
            versions = self._allVersions()
            if len(parts) == 6:
                if not versions:
                    # the package is created with the first upload
                    self.send_error(404, "package does not exist"); return True
                query = urllib.parse.parse_qs(query)
                limit = int(query.get("limit", ["50"])[0])
                first = (int(query.get("page", ["1"])[0]) - 1) * limit
                body = [ { "version" : v } for v in versions[first:first+limit] ]
            elif len(parts) == 8 and parts[7] == "files":
                body = self._versionFiles(parts[6])
            else:
                self.send_error(404, "not found"); return True

            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True

        def do_HEAD(self):
            if not self._authOk(): return
            if self._maybeFail(): return
            path = self._diskPath()
            if path is None or not os.path.exists(path):
                self.send_error(404, "not found"); return
            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(os.path.getsize(path)))
            self.end_headers()

        def do_GET(self):
            if not self._authOk(): return
            if self._maybeFail(): return
            if self._apiGet(): return
            path = self._diskPath()
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                self.send_error(404, "not found"); return
            except OSError:
                self.send_error(500, "internal error"); return
            self.send_response(200)
            self.send_header("Content-type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_PUT(self):
            length = int(self.headers.get('Content-Length', 0))
            content = self.rfile.read(length)
            if not self._authOk(): return
            if self._maybeFail(): return
            path = self._diskPath()
            # The generic registry refuses to overwrite an existing file.
            if os.path.exists(path):
                self.send_response(409); self.end_headers(); return
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
            except OSError:
                self.send_error(500, "internal error"); return
            self.send_response(201); self.end_headers()

        def do_DELETE(self):
            if not self._authOk(): return
            if self._maybeFail(): return
            path = self._diskPath()
            if path and os.path.exists(path):
                os.unlink(path)
                self.send_response(204)
            else:
                self.send_response(404)
            self.end_headers()

    return Handler


class TestGiteaArchive(BaseTester, TestCase):

    def setUp(self):
        super().setUp()
        self.args = {"retries": 0}
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createGiteaHandler(self.repo.name, self.args))
        self.ip, self.port = self.httpd.server_address
        self.server = threading.Thread(target=self.httpd.serve_forever)
        self.server.daemon = True
        self.server.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def _setArchiveSpec(self, spec):
        spec['name'] = "gitea"
        spec['backend'] = "gitea"
        spec["url"] = "http://{}:{}".format(self.ip, self.port)
        spec["owner"] = "bob-artifacts"
        spec["package"] = "test"

    def testRemoteName(self):
        """The build-id maps to the generic package layout."""
        a = GiteaArchive({"backend":"gitea", "url":"https://gitea.example",
                          "owner":"o", "package":"p"})
        bid = bytes.fromhex("00112233445566778899aabbccddeeff00112233")
        self.assertEqual(a._remoteName(bid, ".tgz"),
            "https://gitea.example/api/packages/o/generic/p/"
            "00112233445566778899aabbccddeeff00112233-1/"
            "00112233445566778899aabbccddeeff00112233-1.tgz")

    def testArchiveName(self):
        """Without an explicit name the package base URL identifies the archive."""
        spec = {"backend":"gitea", "url":"https://gitea.example/git",
                "owner":"o", "package":"p"}
        self.assertEqual(GiteaArchive(spec).getArchiveName(),
            "https://gitea.example/git")
        self.assertEqual(GiteaArchive(dict(spec, name="my-archive")).getArchiveName(),
            "my-archive")


class TestArchivePickle(TestCase):
    """The archive objects are passed to the up-/download executor processes
    and must therefore be picklable. Verifying the SSL certificate or not must
    not make a difference."""

    def _checkPickle(self, spec):
        for sslVerify in [True, False]:
            archive = getArchiver(DummyRecipeSet(dict(spec, sslVerify=sslVerify)))
            self.assertIsNotNone(pickle.loads(pickle.dumps(archive)))

    def testHttp(self):
        self._checkPickle({"backend":"http", "url":"https://server.test/archive"})

    def testGitea(self):
        self._checkPickle({"backend":"gitea", "url":"https://gitea.example",
                           "owner":"o", "package":"p"})


class TestGiteaAuthArchive(BaseTester, TestCase):
    """Same as above but the mock server requires HTTP basic authentication.
    The credentials are part of the URL. Gitea takes a personal access token in
    place of the password."""

    USER = "alice"
    TOKEN = "s3cr3t-token"

    def setUp(self):
        super().setUp()
        self.args = {"retries": 0}
        expectedAuth = "Basic " + base64.b64encode(
            (self.USER + ":" + self.TOKEN).encode("utf-8")).decode("ascii")
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createGiteaHandler(self.repo.name, self.args, expectedAuth))
        self.ip, self.port = self.httpd.server_address
        self.server = threading.Thread(target=self.httpd.serve_forever)
        self.server.daemon = True
        self.server.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def _url(self, user, password):
        return "http://{}:{}@{}:{}".format(user, password, self.ip, self.port)

    def _setArchiveSpec(self, spec):
        spec['backend'] = "gitea"
        spec["url"] = self._url(self.USER, self.TOKEN)
        spec["owner"] = "o"
        spec["package"] = "p"

    def testWrongTokenFailsUpload(self):
        """A wrong token must fail the upload before the artifact body is sent."""
        archive = GiteaArchive({"backend":"gitea", "owner":"o", "package":"p",
                                "url":self._url(self.USER, "wrong-token")})
        archive.wantUploadLocal(True)
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            with open(audit, "wb") as f:
                f.write(b"AUDIT")
            os.mkdir(content)
            with open(os.path.join(content, "data"), "wb") as f:
                f.write(b"DATA")
            with self.assertRaises(BuildError) as cm:
                run(archive.uploadPackage(DummyStep(), UPLOAD1_ARTIFACT, audit,
                    content, executor=self.executor))
            self.assertIn("401", str(cm.exception))

    def testNoCredentialsInMessages(self):
        """The URL credentials must never show up in user visible strings.

        getArchiveName() in particular feeds _namedErrorString() and is thus
        printed on every plain "artifact not found" of a cache miss.
        """
        spec = { }
        self._setArchiveSpec(spec)
        archive = GiteaArchive(spec)

        for method, uri in [
                ("getArchiveName", archive.getArchiveName()),
                ("getArchiveUri", archive.getArchiveUri()),
                ("_remoteName", archive._remoteName(DOWNLOAD_ARITFACT, ".tgz")),
            ]:
            with self.subTest(method=method):
                self.assertNotIn(self.TOKEN, uri)
                self.assertNotIn(self.USER, uri)
                self.assertNotIn("@", uri)
                # ...but the host must still be there to be of any use
                self.assertIn("{}:{}".format(self.ip, self.port), uri)


class TestGiteaArchiveRetries(Base, TestCase):

    def setUp(self):
        super().setUp()
        self.VALID_FILE = self._createArtifact(VALID_ARTIFACT, valid_data=True)

    def _getArchive(self, port, retries):
        spec = {'backend':'gitea', 'name':'gitea', 'owner':'o', 'package':'p',
                'retries':retries, 'url':"http://localhost:{}".format(port)}
        return getArchiver(DummyRecipeSet(spec))

    def _startServer(self, retries):
        args = {"retries": retries}
        httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createGiteaHandler(self.repo.name, args))
        thread = threading.Thread(target=httpd.serve_forever)
        thread.daemon = True
        thread.start()
        return httpd, httpd.server_address[1]

    def _download(self, archive):
        archive.wantDownloadLocal(True)
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            return run(archive.downloadPackage(DummyStep(), VALID_ARTIFACT,
                audit, content, executor=self.executor))

    def _testRetries(self, r):
        # server fails exactly 'r' times -> download succeeds within retries
        httpd, port = self._startServer(r)
        try:
            self.assertTrue(self._download(self._getArchive(port, r)))
        finally:
            httpd.shutdown(); httpd.server_close()

        # server fails one more time than retries -> download fails (no throw)
        httpd, port = self._startServer(r + 1)
        try:
            self.assertFalse(self._download(self._getArchive(port, r)))
        finally:
            httpd.shutdown(); httpd.server_close()

    def testRetriesWithNoRetries(self):
        self._testRetries(0)

    def testRetriesWithOneRetry(self):
        self._testRetries(1)

    def testRetriesWithMultipleRetries(self):
        self._testRetries(3)


class TestGiteaManagedArchive(Base, TestCase):
    """The managed operations that back the "bob archive" command.

    They are served by the package API of the server because the registry
    itself cannot enumerate its content.
    """

    def setUp(self):
        super().setUp()
        self.args = {"retries": 0}
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createGiteaHandler(self.repo.name, self.args))
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        thread = threading.Thread(target=self.httpd.serve_forever)
        thread.daemon = True
        thread.start()

    def _archive(self):
        spec = {'backend':'gitea', 'name':'gitea', 'owner':'o', 'package':'p',
                'flags':['download', 'upload', 'managed'],
                'url':"http://localhost:{}".format(self.httpd.server_address[1])}
        return getArchiver(DummyRecipeSet(spec))

    def testCanManage(self):
        self.assertTrue(self._archive().canManage())

    def testEmptyArchive(self):
        """Nothing was uploaded yet, so the package does not even exist."""
        found = []
        self._archive().listPackages(lambda bid, path, st: found.append((bid, path)))
        self.assertEqual(found, [])

    def _sha256(self, name):
        with open(name, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def testListPackages(self):
        """listPackages() reports each package together with its stat.

        The file hash of the registry is used as the stat, that is the
        change indicator.
        """
        name = self._createArtifact(DOWNLOAD_ARITFACT)
        archive = self._archive()
        found = []
        archive.listPackages(lambda bid, path, st: found.append((bid, path, st)))
        self.assertEqual(found,
            [(DOWNLOAD_ARITFACT, archive._makePath(DOWNLOAD_ARITFACT, ".tgz"),
              self._sha256(name))])

        # a changed artifact must be detected
        with open(name, "ab") as f:
            f.write(b'\x00')
        found = []
        archive.listPackages(lambda bid, path, st: found.append((bid, path, st)))
        self.assertEqual(found[0][2], self._sha256(name))

    def testListPackagesWithoutArtifact(self):
        """Live-build-ids create package versions that hold no artifact.

        The scanner must not be told about them since there is no tarball to
        stat in the first place.
        """
        self._createBuildId(UPLOAD1_ARTIFACT)
        found = []
        self._archive().listPackages(lambda bid, path, st: found.append((bid, path)))
        self.assertEqual(found, [])

    def testListPackagesPaginated(self):
        """The package API returns at most 50 versions per request."""
        bids = [ bytes([i]) + b'\x42'*19 for i in range(60) ]
        for bid in bids:
            self._createArtifact(bid)
        found = []
        self._archive().listPackages(lambda bid, path, st: found.append(bid))
        self.assertEqual(sorted(found), sorted(bids))

    def testGetAudit(self):
        self._createArtifact(VALID_ARTIFACT, valid_data=True)
        audit = self._archive().getAudit(VALID_ARTIFACT)
        self.assertIsNotNone(audit)
        self.assertEqual(audit.getArtifact().getMetaData(), "1")

    def testDelete(self):
        name = self._createArtifact(DOWNLOAD_ARITFACT)
        self._archive().deletePackage(DOWNLOAD_ARITFACT)
        self.assertFalse(os.path.exists(name))

    def testDeleteNotFound(self):
        """Deleting a file that is already gone is not an error."""
        self._createArtifact(DOWNLOAD_ARITFACT)
        self._archive().deletePackage(NOT_EXISTS_ARTIFACT)

    def testBrokenApiReply(self):
        """A server that does not speak the package API must not throw up."""
        self._createArtifact(DOWNLOAD_ARITFACT)
        self.args["badApi"] = True
        with self.assertRaises(BobError):
            self._archive().listPackages(lambda bid, path, st: None)


def createGiteaStatusHandler(responses):
    """Mock Gitea registry that answers each HTTP method with a scripted result.

    ``responses`` maps the HTTP method ("HEAD"/"GET"/"PUT"/"DELETE") to either
    ``("status", code)`` to return that status code or ``("close",)`` to read
    the request and then drop the connection without any response (simulating a
    reverse-proxy or server that closes mid-upload). An unlisted method yields
    ``404``.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            length = int(self.headers.get('Content-Length', 0) or 0)
            if length:
                self.rfile.read(length)
            action = responses.get(self.command, ("status", 404))
            if action[0] == "close":
                self.close_connection = True
                self.wfile.close()
                return
            self.send_response(action[1])
            self.end_headers()

        do_HEAD = _handle
        do_GET = _handle
        do_PUT = _handle
        do_DELETE = _handle

    return Handler


class TestGiteaArchiveErrors(Base, TestCase):
    """Directly exercise the individual error-handling branches of the
    GiteaArchive HTTP methods using a mock server with scripted responses."""

    BUILD_ID = bytes.fromhex("00112233445566778899aabbccddeeff00112233")

    def _archive(self, responses):
        self.responses = responses
        self.httpd = socketserver.ThreadingTCPServer(("localhost", 0),
            createGiteaStatusHandler(responses))
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        thread = threading.Thread(target=self.httpd.serve_forever)
        thread.daemon = True
        thread.start()
        port = self.httpd.server_address[1]
        return GiteaArchive({"backend": "gitea", "owner": "o", "package": "p",
            "retries": 0, "url": "http://localhost:{}".format(port)})

    def _upload(self, archive):
        path = archive._makePath(self.BUILD_ID, ".tgz")
        with TemporaryFile() as tmp:
            tmp.write(b"DATA")
            archive._putUploadFile(path, tmp, False)

    def testCanManageAndUri(self):
        archive = self._archive({})
        self.assertTrue(archive._canManage())
        self.assertIn("localhost", archive.getArchiveUri())

    def testDownloadNotFound(self):
        archive = self._archive({"GET": ("status", 404)})
        with self.assertRaises(WebdavNotFoundError):
            archive._openDownloadFile(self.BUILD_ID, ".tgz")

    def testDownloadHttpError(self):
        archive = self._archive({"GET": ("status", 403)})
        with self.assertRaises(WebdavError):
            archive._openDownloadFile(self.BUILD_ID, ".tgz")

    def testExistsByHead(self):
        # the HEAD preflight finds the artifact -> no upload
        archive = self._archive({"HEAD": ("status", 200)})
        with self.assertRaises(ArtifactExistsError):
            archive._openUploadFile(self.BUILD_ID, ".tgz", False)

    def testExistsHttpError(self):
        # HEAD with an unexpected status
        archive = self._archive({"HEAD": ("status", 400)})
        with self.assertRaises(WebdavError):
            archive._openUploadFile(self.BUILD_ID, ".tgz", False)

    def testUploadConflict(self):
        # HEAD says "missing" but the PUT hits a 409 race. Gitea refuses to
        # overwrite an existing file with a conflict.
        archive = self._archive({"PUT": ("status", 409)})
        with self.assertRaises(WebdavAlreadyExistsError):
            self._upload(archive)

    def testUploadPreconditionFailed(self):
        archive = self._archive({"PUT": ("status", 412)})
        with self.assertRaises(WebdavAlreadyExistsError):
            self._upload(archive)

    def testUploadHttpError(self):
        archive = self._archive({"PUT": ("status", 400)})
        with self.assertRaises(WebdavError):
            self._upload(archive)

    def testUploadUnexpectedStatus(self):
        # a 2xx status that is not one of the accepted success codes
        archive = self._archive({"PUT": ("status", 205)})
        with self.assertRaises(WebdavError):
            self._upload(archive)

    def testUploadConnectionClosed(self):
        # server drops the connection during upload
        archive = self._archive({"PUT": ("close",)})
        with self.assertRaises(WebdavError):
            self._upload(archive)

    def testOverwriteDeletesFirst(self):
        # the file is removed before the upload because Gitea would refuse to
        # overwrite it
        archive = self._archive({"DELETE": ("status", 204)})
        self.assertIsNotNone(archive._openUploadFile(self.BUILD_ID, ".tgz", True))

    def testOverwriteDeleteMissing(self):
        # nothing to delete is not an error
        archive = self._archive({"DELETE": ("status", 404)})
        self.assertIsNotNone(archive._openUploadFile(self.BUILD_ID, ".tgz", True))

    def testOverwriteDeleteHttpError(self):
        archive = self._archive({"DELETE": ("status", 400)})
        with self.assertRaises(WebdavError):
            archive._openUploadFile(self.BUILD_ID, ".tgz", True)
