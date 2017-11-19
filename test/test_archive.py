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

from binascii import hexlify
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import http.server
import os, os.path
import socketserver
import subprocess
import tarfile
import threading

from bob.archive import DummyArchive, LocalArchive, CustomArchive, SimpleHttpArchive
from bob.errors import BuildError

DOWNLOAD_ARITFACT = b'\x00'*20
NOT_EXISTS_ARTIFACT = b'\x01'*20
WRONG_VERSION_ARTIFACT = b'\x02'*20
BROKEN_ARTIFACT = b'\xba\xdc\x0f\xfe'*5

UPLOAD1_ARTIFACT = b'\x10'*20
UPLOAD2_ARTIFACT = b'\x11'*20

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
                audit.flush()
                tar.add(audit.name, "meta/audit.json.gz")
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

    def __testWorkspace(self, audit, workspace):
        with open(audit, "rb") as f:
            self.assertEqual(f.read(), b'AUDIT')
        with open(os.path.join(workspace, "data"), "rb") as f:
            self.assertEqual(f.read(), b'DATA')


    def setUp(self):
        # create repo
        self.repo = TemporaryDirectory()

        # add artifacts
        self.dummyFileName = self.__createArtifact(DOWNLOAD_ARITFACT)
        self.__createArtifact(WRONG_VERSION_ARTIFACT, "0")
        self.__createBuildId(DOWNLOAD_ARITFACT)

        # create broken artifact
        bid = hexlify(BROKEN_ARTIFACT).decode("ascii")
        name = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")
        os.makedirs(os.path.dirname(name), exist_ok=True)
        with open(name, "wb") as f:
            f.write(b'\x00')

    def tearDown(self):
        self.repo.cleanup()

    # standard tests for options -> requires _getArchiveInstance
    def testOptions(self):
        """Test that wantDownload/wantUpload options work"""

        a = self._getArchiveInstance({})
        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canUploadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertFalse(a.canUploadJenkins())

        a = self._getArchiveInstance({})
        a.wantDownload(True)
        self.assertTrue(a.canDownloadLocal())
        self.assertFalse(a.canUploadLocal())
        self.assertTrue(a.canDownloadJenkins())
        self.assertFalse(a.canUploadJenkins())

        a = self._getArchiveInstance({})
        a.wantUpload(True)
        self.assertFalse(a.canDownloadLocal())
        self.assertTrue(a.canUploadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertTrue(a.canUploadJenkins())

        a = self._getArchiveInstance({})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertTrue(a.canDownloadLocal())
        self.assertTrue(a.canUploadLocal())
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue(a.canUploadJenkins())

    def testFlags(self):
        """Test that standard flags work"""

        a = self._getArchiveInstance({"flags":[]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canUploadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertFalse(a.canUploadJenkins())

        a = self._getArchiveInstance({"flags":["download"]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertTrue(a.canDownloadLocal())
        self.assertTrue(a.canDownloadJenkins())
        self.assertFalse(a.canUploadLocal())
        self.assertFalse(a.canUploadJenkins())

        a = self._getArchiveInstance({"flags":["upload"]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertTrue(a.canUploadLocal())
        self.assertTrue(a.canUploadJenkins())

        a = self._getArchiveInstance({"flags":["upload"]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertTrue(a.canUploadLocal())
        self.assertTrue(a.canUploadJenkins())

        a = self._getArchiveInstance({"flags":["download", "upload", "nolocal"]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canUploadLocal())
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue(a.canUploadJenkins())

        a = self._getArchiveInstance({"flags":["download", "upload", "nojenkins"]})
        a.wantDownload(True)
        a.wantUpload(True)
        self.assertTrue(a.canDownloadLocal())
        self.assertTrue(a.canUploadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertFalse(a.canUploadJenkins())

    def testDisabledLocal(self):
        """Disabled local must not do anything"""
        a = self._getArchiveInstance({})
        self.assertFalse(a.downloadPackage(b'\xcc'*20, "unused", "unused", 0))
        self.assertFalse(a.uploadPackage(b'\xcc'*20, "unused", "unused", 0))
        self.assertEqual(a.downloadLocalLiveBuildId(b'\xcc'*20, 0), None)
        a.uploadLocalLiveBuildId(b'\xcc'*20, b'\xcc', 0)

    def testDisabledJenkins(self):
        """Disabled Jenkins must produce empty strings"""
        a = self._getArchiveInstance({})
        ret = a.download(None, "unused", "unused")
        self.assertEqual(ret, "")
        ret = a.upload(None, "unused", "unused")
        self.assertEqual(ret, "")

    def testdoDownloadPackage(self):
        """Local download tests"""

        archive = self._getArchiveInstance({})
        archive.wantDownload(True)
        self.assertTrue(archive.canDownloadLocal())

        # normal verbosity
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            self.assertTrue(archive.downloadPackage(DOWNLOAD_ARITFACT, audit, content, 0))
            self.__testWorkspace(audit, content)
            self.assertEqual(archive.downloadLocalLiveBuildId(DOWNLOAD_ARITFACT, 0), b'\x00'*20)

        # verbose prints
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            self.assertTrue(archive.downloadPackage(DOWNLOAD_ARITFACT, audit, content, 1))
            self.__testWorkspace(audit, content)
            self.assertEqual(archive.downloadLocalLiveBuildId(DOWNLOAD_ARITFACT, 1), b'\x00'*20)

        # non-existent and erro cases
        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            self.assertFalse(archive.downloadPackage(NOT_EXISTS_ARTIFACT, audit, content, 1))
            self.assertEqual(archive.downloadLocalLiveBuildId(NOT_EXISTS_ARTIFACT, 1), None)
            with self.assertRaises(BuildError):
                archive.downloadPackage(BROKEN_ARTIFACT, audit, content, 1)
            with self.assertRaises(BuildError):
                archive.downloadPackage(WRONG_VERSION_ARTIFACT, audit, content, 0)

    def testUploadPackage(self):
        """Local upload tests"""

        archive = self._getArchiveInstance({})
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
            archive.wantUpload(True)
            self.assertTrue(archive.canUploadLocal())

            archive.uploadPackage(DOWNLOAD_ARITFACT, audit, content, 0) # exists alread
            archive.uploadPackage(DOWNLOAD_ARITFACT, audit, content, 1) # exists alread

            bid = UPLOAD1_ARTIFACT
            archive.uploadPackage(bid, audit, content, 0)
            self.__testArtifact(bid)

            bid = UPLOAD2_ARTIFACT
            archive.uploadPackage(bid, audit, content, 1)
            self.__testArtifact(bid)

        archive.uploadLocalLiveBuildId(DOWNLOAD_ARITFACT, b'\x00', 0) # exists already
        archive.uploadLocalLiveBuildId(DOWNLOAD_ARITFACT, b'\x00', 1) # exists already
        archive.uploadLocalLiveBuildId(UPLOAD1_ARTIFACT, b'\x00', 0)
        archive.uploadLocalLiveBuildId(UPLOAD2_ARTIFACT, b'\x00', 1)

    def testDownloadJenkins(self):
        """Jenkins download tests"""

        archive = self._getArchiveInstance({})
        archive.wantDownload(True)
        self.assertTrue(archive.canDownloadJenkins())

        with TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, "test.buildid"), "wb") as f:
                f.write(b'\x00'*20)
            script = archive.download(None, "test.buildid", "result.tgz")
            subprocess.check_call(['/bin/bash', '-x', '-c', script],
                universal_newlines=True, stderr=subprocess.STDOUT, cwd=workspace)
            with open(self.dummyFileName, "rb") as f:
                with open(os.path.join(workspace, "result.tgz"), "rb") as g:
                    self.assertEqual(f.read(), g.read())

    def testUploadJenkins(self):
        """Jenkins upload tests"""

        archive = self._getArchiveInstance({})
        archive.wantUpload(True)
        self.assertTrue(archive.canUploadLocal())

        with TemporaryDirectory() as tmp:
            bid = b'\x01'*20
            with open(os.path.join(tmp, "test.buildid"), "wb") as f:
                f.write(bid)
            dummy = self.__createArtifactByName(os.path.join(tmp, "result.tgz"))

            script = archive.upload(None, "test.buildid", "result.tgz")
            env = os.environ.copy()
            env["WORKSPACE"] = tmp
            subprocess.check_call(['/bin/bash', '-x', '-c', script],
                universal_newlines=True, stderr=subprocess.STDOUT, cwd=tmp, env=env)

            self.__testArtifact(bid)

class TestDummyArchive(TestCase):

    def testOptions(self):
        a = DummyArchive()
        a.wantDownload(True)
        a.wantUpload(True)

        self.assertFalse(a.canDownloadLocal())
        self.assertFalse(a.canUploadLocal())
        self.assertFalse(a.canDownloadJenkins())
        self.assertFalse(a.canUploadJenkins())

    def testDownloadJenkins(self):
        ret = DummyArchive().download(b'\x00'*20, "unused", "unused")
        self.assertEqual(ret, "")

    def testDownloadLocal(self):
        DummyArchive().downloadPackage(b'\x00'*20, "unused", "unused", 0)

    def testUploadJenkins(self):
        ret = DummyArchive().upload(b'\x00'*20, "unused", "unused")
        self.assertEqual(ret, "")

    def testUploadLocal(self):
        DummyArchive().uploadPackage(b'\x00'*20, "unused", "unused", 0)


class TestLocalArchive(BaseTester, TestCase):
    def _getArchiveInstance(self, spec):
        spec["path"] = self.repo.name
        return LocalArchive(spec)


def createHttpHandler(repoPath):

    class Handler(http.server.BaseHTTPRequestHandler):

        def getCommon(self):
            path = repoPath + self.path
            try:
                f = open(path, "rb")
            except OSError:
                self.send_error(404, "not found")
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
            exists = False
            path = repoPath + self.path
            if os.path.exists(path):
                if "If-None-Match" in self.headers:
                    self.send_response(412)
                    self.end_headers()
                    return
                else:
                    exists = True

            os.makedirs(os.path.dirname(path), exist_ok=True)
            length = int(self.headers['Content-Length'])
            with open(path, "wb") as f:
                f.write(self.rfile.read(length))
            self.send_response(200 if exists else 201)
            self.end_headers()

    return Handler

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

    def _getArchiveInstance(self, spec):
        spec["url"] = "http://{}:{}".format(self.ip, self.port)
        return SimpleHttpArchive(spec)


class TestCustomArchive(BaseTester, TestCase):

    def _getArchiveInstance(self, spec):
        spec["download"] = "cp {}/$BOB_REMOTE_ARTIFACT $BOB_LOCAL_ARTIFACT".format(self.repo.name)
        spec["upload"] = "mkdir -p {P}/${{BOB_REMOTE_ARTIFACT%/*}} && cp $BOB_LOCAL_ARTIFACT {P}/$BOB_REMOTE_ARTIFACT".format(P=self.repo.name)
        return CustomArchive(spec, [])

