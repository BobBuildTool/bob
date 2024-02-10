# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from shlex import quote
from unittest import TestCase, skipIf
from unittest.mock import MagicMock, patch
import asyncio
import os, stat
import subprocess
import shutil
import tempfile
import hashlib
import sys

from mocks.http_server import HttpServerMock

from bob.input import UrlScm
from bob.scm.url import parseMode, dumpMode
from bob.invoker import Invoker, InvocationError
from bob.errors import ParseError, BuildError
from bob.utils import asHexStr, runInEventLoop, getProcessPoolExecutor, isWindows

INVALID_FILE = "C:\\does\\not\\exist" if sys.platform == "win32" else "/does/not/exist/"

def makeFileUrl(fn):
    if sys.platform == "win32":
        return "file:///" + fn.replace("\\", "/")
    else:
        return "file://" + fn

def escapeMirrorFileName(fn):
    return os.path.abspath(fn).replace('\\', '\\\\')

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()

class UrlScmExecutor:

    def invokeScm(self, workspace, scm, switch=False, oldScm=None):
        executor = getProcessPoolExecutor()
        try:
            spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
            invoker = Invoker(spec, False, True, True, True, True, False,
                              executor=executor)
            if switch:
                runInEventLoop(scm.switch(invoker, oldScm))
            else:
                runInEventLoop(scm.invoke(invoker))
        finally:
            executor.shutdown()

    def createUrlScm(self, spec = {}, preMirrors=[], fallbackMirrors=[],
                     defaultFileMode=None):
        s = {
            'scm' : 'url',
            'url' : self.url,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return UrlScm(s, preMirrors=preMirrors, fallbackMirrors=fallbackMirrors,
                      defaultFileMode=defaultFileMode)

class UrlScmTest(UrlScmExecutor):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.dir = cls.__repodir.name
        cls.fn = "test.txt"
        cls.path = os.path.join(cls.__repodir.name, cls.fn)
        cls.url = makeFileUrl(cls.path)

        with open(cls.path, "w") as f:
            f.write("Hello world!")

        with open(cls.path, "rb") as f:
            d = hashlib.sha1()
            d.update(f.read())
            cls.urlSha1 = asHexStr(d.digest())

        with open(cls.path, "rb") as f:
            d = hashlib.sha256()
            d.update(f.read())
            cls.urlSha256 = asHexStr(d.digest())

        with open(cls.path, "rb") as f:
            d = hashlib.sha512()
            d.update(f.read())
            cls.urlSha512 = asHexStr(d.digest())

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def assertContent(self, fn):
        with open(fn, "rb") as f:
            d = hashlib.sha1()
            d.update(f.read())
        self.assertEqual(self.urlSha1, asHexStr(d.digest()))

    def assertMode(self, fn, mode=(0o666 if isWindows() else 0o600)):
        fm = stat.S_IMODE(os.lstat(fn).st_mode)
        self.assertEqual(fm, mode)

class TestLiveBuildId(UrlScmTest, TestCase):

    def callCalcLiveBuildId(self, scm):
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            return scm.calcLiveBuildId(workspace)

    def testHasLiveBuildId(self):
        """Only with digest we have live-build-ids"""
        s = self.createUrlScm()
        self.assertFalse(s.hasLiveBuildId())
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertTrue(s.hasLiveBuildId())
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertTrue(s.hasLiveBuildId())
        s = self.createUrlScm({'digestSHA512' : self.urlSha512})
        self.assertTrue(s.hasLiveBuildId())

    def testPredictLiveBildId(self):
        """Predict live-build-id"""
        s = self.createUrlScm()
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), bytes.fromhex(self.urlSha256))
        s = self.createUrlScm({'digestSHA512' : self.urlSha512})
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), bytes.fromhex(self.urlSha512))

    def testCalcLiveBuildId(self):
        s = self.createUrlScm()
        self.assertEqual(self.callCalcLiveBuildId(s), None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertEqual(self.callCalcLiveBuildId(s), bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertEqual(self.callCalcLiveBuildId(s), bytes.fromhex(self.urlSha256))
        s = self.createUrlScm({'digestSHA512' : self.urlSha512})
        self.assertEqual(self.callCalcLiveBuildId(s), bytes.fromhex(self.urlSha512))

class TestWindowsPaths(TestCase):
    """The URL SCM supports fully qualified paths on Windows too."""

    @patch('sys.platform', "win32")
    def testValidDrive(self):
        from bob.scm.url import parseUrl
        self.assertEqual(parseUrl(r"C:\tmp.txt").path, r"C:\tmp.txt")
        self.assertEqual(parseUrl(r"C:/tmp.txt").path, r"C:\tmp.txt")

        self.assertEqual(parseUrl(r"file:///C:\tmp.txt").path, r"C:\tmp.txt")
        self.assertEqual(parseUrl(r"file:///C:/tmp.txt").path, r"C:\tmp.txt")

    @patch('sys.platform', "win32")
    def testValidUNC(self):
        from bob.scm.url import parseUrl
        self.assertEqual(parseUrl(r"\\server\path").path, r"\\server\path")
        self.assertEqual(parseUrl(r"file:///\\server\path").path, r"\\server\path")

    @patch('sys.platform', "win32")
    def testInvalid(self):
        from bob.scm.url import parseUrl
        with self.assertRaises(ValueError):
            parseUrl(r"C:tmp.txt") # Drive relative
        with self.assertRaises(ValueError):
            parseUrl(r"tmp.txt") # current drive relative
        with self.assertRaises(ValueError):
            parseUrl(r"\tmp.txt") # absolute path on current drive
        with self.assertRaises(ValueError):
            parseUrl(r"/tmp.txt") # ditto
        with self.assertRaises(ValueError):
            parseUrl(r"file:///C:tmp.txt") # Drive relative
        with self.assertRaises(ValueError):
            parseUrl(r"file:///tmp.txt") # absolute path on current drive
        with self.assertRaises(ValueError):
            parseUrl(r"file:///\tmp.txt") # absolute on current drive

    @patch('sys.platform', "win32")
    def testFileName(self):
        """fileName deduction on Windows must work with \\ too"""
        s = {
            'scm' : 'url',
            'url' : "C:/X/Y/my-pkg.zip",
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        self.assertEqual(UrlScm(s).getProperties(False)["fileName"], "my-pkg.zip")

        s["url"] = r"C:\X\Y\my-pkg.zip"
        self.assertEqual(UrlScm(s).getProperties(False)["fileName"], "my-pkg.zip")

class TestSpecs(UrlScmTest, TestCase):

    url = "/does/not/exist"

    def testInvalidSHA1(self):
        """Invalid SHA1 digest is rejected"""
        with self.assertRaises(ParseError):
            self.createUrlScm({ "digestSHA1" : "invalid" })

    def testInvalidSHA256(self):
        """Invalid SHA256 digest is rejected"""
        with self.assertRaises(ParseError):
            self.createUrlScm({ "digestSHA256" : "invalid" })

    def testInvalidSHA512(self):
        """Invalid SHA512 digest is rejected"""
        with self.assertRaises(ParseError):
            self.createUrlScm({ "digestSHA512" : "invalid" })

class TestDigestMatch(UrlScmTest, TestCase):

    def testSHA1Match(self):
        scm = self.createUrlScm({ "digestSHA1" : self.urlSha1 })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)

    def testSHA1Mismatch(self):
        scm = self.createUrlScm({ "digestSHA1" : "0"*40 })
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)

    def testSHA256Match(self):
        scm = self.createUrlScm({ "digestSHA256" : self.urlSha256 })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)

    def testSHA256Mismatch(self):
        scm = self.createUrlScm({ "digestSHA256" : "0"*64 })
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)

    def testSHA512Match(self):
        scm = self.createUrlScm({ "digestSHA512" : self.urlSha512 })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)

    def testSHA512Mismatch(self):
        scm = self.createUrlScm({ "digestSHA512" : "0"*128 })
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)

class TestDownloads(UrlScmTest, TestCase):

    def testDownload(self):
        """Simple download via HTTP"""
        with HttpServerMock(self.dir) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/test.txt".format(srv.port),
            })
            with tempfile.TemporaryDirectory() as workspace:
                self.invokeScm(workspace, scm)
                fn = os.path.join(workspace, "test.txt")
                self.assertContent(fn)
                self.assertMode(fn)

    def testDownloadAgain(self):
        """Download existing file again. Should not transfer the file again"""
        with HttpServerMock(self.dir) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/test.txt".format(srv.port),
            })
            with tempfile.TemporaryDirectory() as workspace:
                fn = os.path.join(workspace, "test.txt")
                self.invokeScm(workspace, scm)
                self.assertContent(fn)
                self.assertMode(fn)
                fs1 = os.stat(fn)
                self.invokeScm(workspace, scm)
                fs2 = os.stat(fn)

        # Only compare mtime and ctime because atime is updated even by
        # open()+fstat() on Windows.
        self.assertEqual(fs1.st_mtime_ns, fs2.st_mtime_ns)
        self.assertEqual(fs1.st_ctime_ns, fs2.st_ctime_ns)

    def testDownloadRetry(self):
        """Test HTTP retry"""
        with HttpServerMock(self.dir, retries=1) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/test.txt".format(srv.port),
                "retries" : 2
            })
            with tempfile.TemporaryDirectory() as workspace:
                self.invokeScm(workspace, scm)
                self.assertContent(os.path.join(workspace, "test.txt"))

    def testDownloadRetryFailing(self):
        """Test HTTP retry"""
        with HttpServerMock(self.dir, retries=2) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/test.txt".format(srv.port),
                "retries" : 1
            })
            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaises(InvocationError):
                    self.invokeScm(workspace, scm)


    def testDownloadNotExisting(self):
        """Try to download an invalid file -> 404"""
        with HttpServerMock(self.dir) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/invalid.txt".format(srv.port),
            })
            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaises(InvocationError):
                    self.invokeScm(workspace, scm)

    def testNoResponse(self):
        """Remote server does not send a response."""
        with HttpServerMock(self.dir, noResponse=True) as srv:
            scm = self.createUrlScm({
                "url" : "http://localhost:{}/test.txt".format(srv.port),
            })
            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaises(InvocationError):
                    self.invokeScm(workspace, scm)

class TestExtraction:

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.dir = cls.__repodir.name

        src = os.path.join(cls.dir, "src")
        os.mkdir(src)

        with open(os.path.join(src, "test.txt"), "w") as f:
            f.write("Hello world!")

        cls.tarGzFile = os.path.join(cls.dir, "test.tar.gz")
        subprocess.run(["tar", "-zcf", cls.tarGzFile, src],
            cwd=cls.dir, check=True)
        with open(cls.tarGzFile, "rb") as f:
            cls.tarGzDigestSha1 = hashlib.sha1(f.read()).digest().hex()

        cls.gzFile = os.path.join(cls.dir, "test.txt.gz")
        subprocess.run("gzip -k src/test.txt && mv src/test.txt.gz .",
            shell=True, cwd=cls.dir, check=True)
        with open(cls.gzFile, "rb") as f:
            cls.gzDigestSha256 = hashlib.sha256(f.read()).digest().hex()

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def invokeScm(self, workspace, scm):
        executor = getProcessPoolExecutor()
        try:
            spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
            invoker = Invoker(spec, False, True, True, True, True, False,
                              executor=executor)
            runInEventLoop(scm.invoke(invoker))
        finally:
            executor.shutdown()

    def createUrlScm(self, spec = {}):
        s = {
            'scm' : 'url',
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return UrlScm(s)

    def assertExists(self, fn):
        self.assertTrue(os.path.exists(fn), "file "+fn+" does not exist")

    def assertNotExists(self, fn):
        self.assertFalse(os.path.exists(fn), "file "+fn+" does exist")

    def testTarGz(self):
        scm = self.createUrlScm({
            "url" : self.tarGzFile,
            "digestSHA1" : self.tarGzDigestSha1,
        })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertExists(os.path.join(workspace, "src", "test.txt"))

    def testTarGzStripComponents(self):
        scm = self.createUrlScm({
            "url" : self.tarGzFile,
            "digestSHA1" : self.tarGzDigestSha1,
            "stripComponents" : 1,
        })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertNotExists(os.path.join(workspace, "src"))
            self.assertExists(os.path.join(workspace, "test.txt"))

    def testTarGzNoExtract(self):
        scm = self.createUrlScm({
            "url" : self.tarGzFile,
            "digestSHA1" : self.tarGzDigestSha1,
            "extract" : False,
        })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertExists(os.path.join(workspace, "test.tar.gz"))
            self.assertNotExists(os.path.join(workspace, "src"))
            self.assertNotExists(os.path.join(workspace, "src", "test.txt"))

    def testGz(self):
        scm = self.createUrlScm({
            "url" : self.gzFile,
            "digestSHA256" : self.gzDigestSha256,
        })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertExists(os.path.join(workspace, "test.txt.gz"))
            self.assertExists(os.path.join(workspace, "test.txt"))

    def testGzStripComponentsNotSupported(self):
        scm = self.createUrlScm({
            "url" : self.gzFile,
            "digestSHA256" : self.gzDigestSha256,
            "stripComponents" : 1,
        })
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)


class TestMirrors(UrlScmTest, TestCase):

    def assertExists(self, fn):
        self.assertTrue(os.path.exists(fn), "file "+fn+" does not exist")

    def assertNotExists(self, fn):
        self.assertFalse(os.path.exists(fn), "file "+fn+" does exist")

    def testFileMirrorFailed(self):
        """A missing file in a local mirror is tolerated"""
        scm = self.createUrlScm(
            { "digestSHA1" : self.urlSha1 },
            preMirrors=[ { 'scm' : 'url',
                           'url' : r".+",
                           'mirror' : escapeMirrorFileName("/does/not/exist"),
                         }
                       ])

        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)

    def testHttpMirrorFailed(self):
        """A missing file in an HTTP mirror is tolerated"""
        with tempfile.TemporaryDirectory() as mirror:
            with HttpServerMock(mirror) as srv:
                scm = self.createUrlScm(
                    { "digestSHA1" : self.urlSha1 },
                    preMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

                self.assertEqual(0, srv.headRequests)
                self.assertEqual(1, srv.getRequests)
                self.assertEqual(0, srv.putRequests)

    def testNoMirrorsIfIndeterministic(self):
        """Mirrors are only consulted for deterministic SCMs"""
        with tempfile.TemporaryDirectory() as mirror:
            with HttpServerMock(mirror) as srv:
                scm = self.createUrlScm(
                    preMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                        "upload" : True,
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

                self.assertEqual(0, srv.getRequests)
                self.assertEqual(0, srv.putRequests)

    def testPreMirrorFileUsed(self):
        """Test that pre-mirror is used before looking at primary URL"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorFile = os.path.join(mirror, "mirror.txt")
            shutil.copy(self.path, mirrorFile)

            # Make sure to fail if primary URL is used
            rogueFile = os.path.join(mirror, "evil.txt")
            with open(rogueFile, "w") as f:
                f.write("bad")

            scm = self.createUrlScm(
                { "url" : makeFileUrl(rogueFile),
                  "digestSHA1" : self.urlSha1 },
                preMirrors=[ { 'scm' : 'url',
                               'url' : r".+",
                               'mirror' : escapeMirrorFileName(mirrorFile),
                             }
                           ])

            with tempfile.TemporaryDirectory() as workspace:
                self.invokeScm(workspace, scm)
                self.assertExists(os.path.join(workspace, "evil.txt"))

    def testFallbackMirrorFileUsed(self):
        """Test that fallback mirror is used in case primary URL is unavailable."""
        with tempfile.TemporaryDirectory() as mirror:
            shutil.copy(self.path, os.path.join(mirror, self.fn))

            scm = self.createUrlScm(
                { "url" : makeFileUrl(os.path.join(INVALID_FILE, self.fn)),
                  "digestSHA1" : self.urlSha1 },
                fallbackMirrors=[ { 'scm' : 'url',
                                    'url' : r".*/(.*)",
                                    'mirror' : escapeMirrorFileName(mirror) + r"/\1",
                                  }
                                ])

            with tempfile.TemporaryDirectory() as workspace:
                self.invokeScm(workspace, scm)
                self.assertExists(os.path.join(workspace, self.fn))

    def testHttpMirrorUsed(self):
        """Test HTTP mirror"""
        with tempfile.TemporaryDirectory() as mirror:
            shutil.copy(self.path, os.path.join(mirror, self.fn))
            with HttpServerMock(mirror) as srv:
                scm = self.createUrlScm(
                    { "url" : makeFileUrl(os.path.join(INVALID_FILE, self.fn)),
                      "digestSHA1" : self.urlSha1, },
                    preMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

    def testGracefulMirrorFallback(self):
        """A failing mirror is ignored and the next mirror is used"""
        with tempfile.TemporaryDirectory() as m:
            firstMirror = os.path.join(m, "first")
            firstMirrorPath = os.path.join(firstMirror, self.fn)
            os.makedirs(firstMirror)
            secondMirror = os.path.join(m, "second")
            secondMirrorPath = os.path.join(secondMirror, self.fn)
            os.makedirs(secondMirror)

            with HttpServerMock(firstMirror, noResponse=True) as m1:
                with HttpServerMock(secondMirror) as m2:
                    scm = self.createUrlScm(
                        { "digestSHA1" : self.urlSha1 },
                        preMirrors=[
                            {
                                'scm' : 'url',
                                'url' : r".*/(.*)",
                                'mirror' : r"http://localhost:{}/\1".format(m1.port),
                            },
                            {
                                'scm' : 'url',
                                'url' : r".*/(.*)",
                                'mirror' : r"http://localhost:{}/\1".format(m2.port),
                                'upload' : True,
                            }
                        ],
                    )

                    with tempfile.TemporaryDirectory() as workspace:
                        self.invokeScm(workspace, scm)

                    self.assertEqual(1, m1.getRequests)
                    self.assertEqual(0, m1.putRequests)
                    self.assertEqual(1, m2.getRequests)
                    self.assertEqual(1, m2.putRequests)

            self.assertNotExists(firstMirrorPath)
            self.assertExists(secondMirrorPath)

    def testHttpMirrorUpload(self):
        """If requrested, a HTTP mirror is filled"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorPath = os.path.join(mirror, self.fn)
            self.assertNotExists(mirrorPath)

            with HttpServerMock(mirror) as srv:
                scm = self.createUrlScm(
                    { "digestSHA1" : self.urlSha1 },
                    preMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                        "upload" : True,
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

            self.assertExists(mirrorPath)
            self.assertContent(mirrorPath)

    def testHttpMirrorUploadRetry(self):
        """A HTTP mirror upload is retried if configured"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorPath = os.path.join(mirror, self.fn)
            self.assertNotExists(mirrorPath)

            with HttpServerMock(mirror, retries=1) as srv:
                scm = self.createUrlScm(
                    {
                        "digestSHA1" : self.urlSha1,
                        "retries" : 2,
                    },
                    fallbackMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                        "upload" : True,
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

                self.assertEqual(2, srv.putRequests)

            self.assertExists(mirrorPath)
            self.assertContent(mirrorPath)

    def testHttpMirrorNoReplaceExisting(self):
        """Existing files on an HTTP mirror are not replaced"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorPath = os.path.join(mirror, self.fn)
            shutil.copy(self.path, mirrorPath)
            with HttpServerMock(mirror) as srv:
                scm = self.createUrlScm(
                    { "digestSHA1" : self.urlSha1 },
                    fallbackMirrors = [{
                        "scm" : "url",
                        "url" : r".*/(.*)",
                        "mirror" : r"http://localhost:{}/\1".format(srv.port),
                        "upload" : True,
                    }]
                )
                with tempfile.TemporaryDirectory() as workspace:
                    self.invokeScm(workspace, scm)

                self.assertEqual(1, srv.headRequests)
                self.assertEqual(0, srv.getRequests)
                self.assertEqual(0, srv.putRequests)

            self.assertContent(mirrorPath)

    def testUploadIfDownloadedFromMirror(self):
        """Mirrors are uploaded if downloaded from another mirror"""
        with tempfile.TemporaryDirectory() as m:
            firstMirror = os.path.join(m, "first")
            firstMirrorPath = os.path.join(firstMirror, self.fn)
            os.makedirs(firstMirror)
            secondMirror = os.path.join(m, "second")
            secondMirrorPath = os.path.join(secondMirror, self.fn)
            os.makedirs(secondMirror)

            shutil.copy(self.path, firstMirrorPath)
            self.assertExists(firstMirrorPath)
            self.assertNotExists(secondMirrorPath)

            scm = self.createUrlScm(
                { "digestSHA1" : self.urlSha1 },
                preMirrors=[{
                    'scm' : 'url',
                    'url' : r".*/(.*)",
                    'mirror' : escapeMirrorFileName(firstMirror) + r"/\1",
                    'upload' : True,
                }],
                fallbackMirrors=[{
                    'scm' : 'url',
                    'url' : r".*/(.*)",
                    'mirror' : escapeMirrorFileName(secondMirror) + r"/\1",
                    'upload' : True,
                }],
            )

            with tempfile.TemporaryDirectory() as workspace:
                self.invokeScm(workspace, scm)

            self.assertExists(secondMirrorPath)

    def testRogueMirrorFails(self):
        """Broken files on mirrors fail the build"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorPath = os.path.join(mirror, self.fn)
            with open(mirrorPath, "w") as f:
                f.write("bad")
            scm = self.createUrlScm(
                { "digestSHA1" : self.urlSha1 },
                preMirrors=[ { 'scm' : 'url',
                               'url' : r".*/(.*)",
                               'mirror' : escapeMirrorFileName(mirror) + r"/\1",
                             }
                           ])

            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaises(InvocationError):
                    self.invokeScm(workspace, scm)

    def testNoUploadBroken(self):
        """Broken artifacts are not uploaded"""
        with tempfile.TemporaryDirectory() as mirror:
            mirrorPath = os.path.join(mirror, self.fn)
            scm = self.createUrlScm(
                { "digestSHA1" : "0"*40 },
                preMirrors=[ { 'scm' : 'url',
                               'url' : r".*/(.*)",
                               'mirror' : escapeMirrorFileName(mirror) + r"/\1",
                               'upload' : True
                             }
                           ])

            with tempfile.TemporaryDirectory() as workspace:
                with self.assertRaises(InvocationError):
                    self.invokeScm(workspace, scm)

            self.assertNotExists(mirrorPath)


@skipIf(isWindows(), "requires UNIX platform")
class TestFileMode(UrlScmTest, TestCase):

    def testOldDefaultFileMode(self):
        """Test old behaviour of defaultFileMode policy"""
        os.chmod(self.path, 0o764)
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, self.createUrlScm())
            self.assertMode(os.path.join(workspace, self.fn), 0o764)

    def testNewDefaultFileMode(self):
        """Test new behaviour of defaultFileMode policy"""
        os.chmod(self.path, 0o764)
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, self.createUrlScm(defaultFileMode=True))
            self.assertMode(os.path.join(workspace, self.fn), 0o600)

    def testFileModeOverride(self):
        """Test that fileMode attribute takes precedence"""
        os.chmod(self.path, 0o777)
        with tempfile.TemporaryDirectory() as workspace:
            scm = self.createUrlScm({ "fileMode" : 0o640 },
                                    defaultFileMode=True)
            self.invokeScm(workspace, scm)
            self.assertMode(os.path.join(workspace, self.fn), 0o640)

    def testSwitch(self):
        os.chmod(self.path, 0o777)
        with tempfile.TemporaryDirectory() as workspace:
            oldScm = self.createUrlScm({ "fileMode" : 0o640 }, defaultFileMode=True)

            self.invokeScm(workspace, oldScm)
            self.assertMode(os.path.join(workspace, self.fn), 0o640)

            newScm = self.createUrlScm({ "fileMode" : 0o444 })

            self.assertTrue(newScm.canSwitch(oldScm))
            self.invokeScm(workspace, newScm, switch=True, oldScm=oldScm)
            self.assertMode(os.path.join(workspace, self.fn), 0o444)


class TestFileModeParsing(TestCase):
    def testParseInvalid(self):
        with self.assertRaises(ValueError):
            parseMode({})
        with self.assertRaises(ValueError):
            parseMode(None)

        with self.assertRaises(ValueError):
            parseMode("r+x")
        with self.assertRaises(KeyError):
            parseMode("f=rw")
        with self.assertRaises(ValueError):
            parseMode("u,g")
        with self.assertRaises(ValueError):
            parseMode("g=rw,")
        with self.assertRaises(ValueError):
            parseMode("u=rw=x")

    def testParse(self):
        self.assertEqual(parseMode(42), 42)

        self.assertEqual(parseMode("u="), 0)
        self.assertEqual(parseMode("u=rw"), 0o600)
        self.assertEqual(parseMode("u=rw,u=r"), 0o400)
        self.assertEqual(parseMode("u=rwx,g=rx,o=rx"), 0o755)

    def testDump(self):
        self.assertEqual(dumpMode(None), None)
        self.assertEqual(dumpMode(0), "")
        self.assertEqual(dumpMode(0o600), "u=rw")
        self.assertEqual(dumpMode(0o400), "u=r")
        self.assertEqual(dumpMode(0o755), "u=rwx,g=rx,o=rx")


class TestExtraction(UrlScmExecutor, TestCase):

    def assertFileMd5(self, fn, digest):
        with open(fn, "rb") as f:
            d = hashlib.md5()
            d.update(f.read())
        self.assertEqual(digest, asHexStr(d.digest()))

    def assertTree(self, workspace, prefix="foo-1.2.3"):
        self.assertFileMd5(os.path.join(workspace, prefix, "configure"),
                           "394cded5228db39cb4d040e866134252")
        self.assertFileMd5(os.path.join(workspace, prefix, "src/main.c"),
                           "0790c8c6a3871d0c3bed428b4ae240c4")

    def testArchive(self):
        for ext in ("tar", "tar.bz2", "tar.gz", "tar.xz", "zip"):
            with self.subTest(extension=ext):
                self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3." + ext))
                with tempfile.TemporaryDirectory() as workspace:
                    scm = self.createUrlScm()
                    self.invokeScm(workspace, scm)
                    self.assertTree(workspace)

    def testStripComponents(self):
        self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3.tar"))
        with tempfile.TemporaryDirectory() as workspace:
            scm = self.createUrlScm({ "stripComponents" : 1 })
            self.invokeScm(workspace, scm)
            self.assertTree(workspace, "")

    def testStripComponentsUnsupported(self):
        self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3.zip"))
        with tempfile.TemporaryDirectory() as workspace:
            scm = self.createUrlScm({ "stripComponents" : 1 })
            with self.assertRaises(BuildError):
                self.invokeScm(workspace, scm)

    @skipIf(isWindows(), "requires UNIX platform")
    def testSingleFile(self):
        for ext in ("gz", "xz"):
            with self.subTest(extension=ext):
                self.url = makeFileUrl(os.path.abspath("data/url-scm/test.txt." + ext))
                with tempfile.TemporaryDirectory() as workspace:
                    scm = self.createUrlScm()
                    self.invokeScm(workspace, scm)
                    self.assertTrue(os.path.exists(os.path.join(workspace, "test.txt." + ext)))
                    self.assertFileMd5(os.path.join(workspace, "test.txt"),
                                       "d3b07384d113edec49eaa6238ad5ff00")

    def testNoExtrace(self):
        self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3.tar"))
        for extract in ("no", False):
            with self.subTest(mode=extract):
                with tempfile.TemporaryDirectory() as workspace:
                    scm = self.createUrlScm({ "extract" : extract })
                    self.invokeScm(workspace, scm)
                    self.assertTrue(os.path.exists(os.path.join(workspace, "foo-1.2.3.tar")))
                    self.assertFalse(os.path.isdir(os.path.join(workspace, "foo-1.2.3")))

    def testSpecificExtract(self):
        self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3.tar"))
        with tempfile.TemporaryDirectory() as workspace:
            scm = self.createUrlScm({ "extract" : "tar" })
            self.invokeScm(workspace, scm)
            self.assertTree(workspace)

    def testWrongSpecificExtract(self):
        self.url = makeFileUrl(os.path.abspath("data/url-scm/foo-1.2.3.tar"))
        with tempfile.TemporaryDirectory() as workspace:
            scm = self.createUrlScm({ "extract" : "zip" })
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)
