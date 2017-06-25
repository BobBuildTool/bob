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
import os, os.path
import tarfile

from bob.archive import DummyArchive, LocalArchive, CustomArchive

class BaseTester:

    def __createArtifact(self, name):
        pax = { 'bob-archive-vsn' : "1" }
        with tarfile.open(name, "w|gz", format=tarfile.PAX_FORMAT, pax_headers=pax) as tar:
            with NamedTemporaryFile() as audit:
                audit.write(b'AUDIT')
                audit.flush()
                tar.add(audit.name, "meta/audit.json.gz")
            with TemporaryDirectory() as content:
                with open(os.path.join(content, "data"), "wb") as f:
                    f.write(b'DATA')
                tar.add(content, "content")

    def __testArtifact(self, bid):
        bid = hexlify(bid).decode("ascii")
        artifact = os.path.join(self.repo.name, bid[0:2], bid[2:4], bid[4:] + "-1.tgz")

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


    def setUp(self):
        # create repo
        self.repo = TemporaryDirectory()
        os.makedirs(os.path.join(self.repo.name, "00", "00"))

        # add dummy artifact
        self.__createArtifact(os.path.join(self.repo.name, "00", "00", "0"*36 + "-1.tgz"))

    def tearDown(self):
        self.repo.cleanup()

    # standard tests for options -> requires _getArchiveInstance
    def testOptions(self):
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


    # helper for local download tests
    def _doDownloadPackage(self, archive):
        archive.wantDownload(True)
        self.assertTrue(archive.canDownloadLocal())

        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            archive.downloadPackage(b'\x00'*20, audit, content, 0)

        with TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "audit.json.gz")
            content = os.path.join(tmp, "workspace")
            archive.downloadPackage(b'\x00'*20, audit, content, 1)

    # helper for local upload tests
    def _doUploadPackage(self, archive):
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

            bid = b'\x01'*20
            archive.uploadPackage(bid, audit, content, 0)
            self.__testArtifact(bid)

            bid = b'\x02'*20
            archive.uploadPackage(bid, audit, content, 1)
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
        spec["path"] = "/tmp"
        return LocalArchive(spec)

    def testDownloadJenkins(self):
        a = LocalArchive({"path" : "ASDF"})
        self.assertFalse(a.canDownloadJenkins())
        self.assertEqual(a.download(b'\x00'*20, "unused", "unused"), "")

        a = LocalArchive({"path" : "ASDF"})
        a.wantDownload(True)
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue("ASDF" in a.download(b'\x00'*20, "unused", "unused"))

        a = LocalArchive({"path" : "ASDF", "flags" : ["download", "nofail"]})
        a.wantDownload(True)
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue("ASDF" in a.download(b'\x00'*20, "unused", "unused"))

    def testUploadJenkins(self):
        a = LocalArchive({"path" : "ASDF"})
        self.assertFalse(a.canUploadJenkins())
        self.assertEqual(a.upload(b'\x00'*20, "unused", "unused"), "")

        a = LocalArchive({"path" : "ASDF"})
        a.wantUpload(True)
        self.assertTrue(a.canUploadJenkins())
        self.assertTrue("ASDF" in a.upload(b'\x00'*20, "unused", "unused"))

        a = LocalArchive({"path" : "ASDF", "flags" : ["upload", "nofail"]})
        a.wantUpload(True)
        self.assertTrue(a.canUploadJenkins())
        self.assertTrue("ASDF" in a.upload(b'\x00'*20, "unused", "unused"))

    def testDownloadLocal(self):
        a = LocalArchive({"path" : self.repo.name})
        self._doDownloadPackage(a)

    def testUploadLocal(self):
        a = LocalArchive({"path" : self.repo.name})
        self._doUploadPackage(a)


class TestCustomArchive(BaseTester, TestCase):

    def _getArchiveInstance(self, spec):
        spec["download"] = "DOWN"
        spec["upload"] = "UP"
        return CustomArchive(spec, [])

    def testDownloadJenkins(self):
        a = CustomArchive({}, [])
        a.wantDownload(True)
        self.assertFalse(a.canDownloadJenkins())
        self.assertEqual(a.download(b'\x00'*20, "unused", "unused"), "")

        a = CustomArchive({"download" : "ASDF"}, [])
        self.assertFalse(a.canDownloadJenkins())
        self.assertEqual(a.download(b'\x00'*20, "unused", "unused"), "")

        a = CustomArchive({"download" : "ASDF"}, [])
        a.wantDownload(True)
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue("ASDF" in a.download(b'\x00'*20, "unused", "unused"))

        a = CustomArchive({"download" : "ASDF", "flags" : ["download", "nofail"]}, [])
        a.wantDownload(True)
        self.assertTrue(a.canDownloadJenkins())
        self.assertTrue("ASDF" in a.download(b'\x00'*20, "unused", "unused"))

    def testDownloadLocal(self):
        a = CustomArchive(
            {
                "download" : "cp {}/$BOB_REMOTE_ARTIFACT $BOB_LOCAL_ARTIFACT".format(self.repo.name),
            }, [])
        self._doDownloadPackage(a)

    def testUploadJenkins(self):
        a = CustomArchive({}, [])
        a.wantUpload(True)
        self.assertFalse(a.canUploadJenkins())
        self.assertEqual(a.upload(b'\x00'*20, "unused", "unused"), "")

        a = CustomArchive({"upload" : "ASDF"}, [])
        self.assertFalse(a.canUploadJenkins())
        self.assertEqual(a.upload(b'\x00'*20, "unused", "unused"), "")

        a = CustomArchive({"upload" : "ASDF"}, [])
        a.wantUpload(True)
        self.assertTrue(a.canUploadJenkins())
        self.assertTrue("ASDF" in a.upload(b'\x00'*20, "unused", "unused"))

        a = CustomArchive({"upload" : "ASDF", "flags" : ["upload", "nofail"]}, [])
        a.wantUpload(True)
        self.assertTrue(a.canUploadJenkins())
        self.assertTrue("ASDF" in a.upload(b'\x00'*20, "unused", "unused"))

    def testUploadLocal(self):
        a = CustomArchive(
            {
                "upload" : "mkdir -p {P}/${{BOB_REMOTE_ARTIFACT%/*}} && cp $BOB_LOCAL_ARTIFACT {P}/$BOB_REMOTE_ARTIFACT".format(P=self.repo.name),
            }, [])
        self._doUploadPackage(a)
