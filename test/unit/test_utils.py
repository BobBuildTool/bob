# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase, skipIf, skipUnless
from unittest.mock import patch, MagicMock
import os, stat
import asyncio
import sys

from bob.utils import joinScripts, removePath, emptyDirectory, compareVersion, \
    getPlatformTag, run, check_output, removeUserFromUrl, runInEventLoop, \
    _replacePathWin32, isWindows, updateDicRecursive
from bob.errors import BuildError, ParseError

class TestJoinScripts(TestCase):

    def testEmpty(self):
        self.assertEqual(joinScripts([], "not used"), None)

    def testSingle(self):
        self.assertEqual(joinScripts(["asdf"], "not used"), "asdf")
        self.assertEqual(joinScripts([None], "not used"), None)

    def testDual(self):
        self.assertEqual(joinScripts(["asdf", "qwer"], "\n"), "asdf\nqwer")

        self.assertEqual(joinScripts(["asdf", None], "unused"), "asdf")
        self.assertEqual(joinScripts([None, "asdf"], "unused"), "asdf")

        self.assertEqual(joinScripts([None, None], "unused"), None)

class TestRemove(TestCase):

    def testFile(self):
        with TemporaryDirectory() as tmp:
            fn = os.path.join(tmp, "file")
            with open(fn, "w") as f:
                f.write("data")
            removePath(fn)
            assert not os.path.exists(fn)

    def testDir(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            removePath(d)
            assert not os.path.exists(d)

    @skipIf(isWindows(), "requires Unix platform")
    def testPermissionUnix(self):
        """Cleaning a read-only directory shall fail"""
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
            self.assertRaises(BuildError, removePath, tmp)
            os.chmod(d, stat.S_IRWXU)

    @skipUnless(isWindows(), "requires Windows platform")
    def testPermissionWindows(self):
        """Cleaning a directory with read only files does not fail"""
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            fn = os.path.join(d, "file")
            with open(fn, "w") as f:
                f.write("data")

            os.chmod(fn, 0)
            removePath(tmp)
            self.assertFalse(os.path.exists(fn))

class TestEmpty(TestCase):

    def testFile(self):
        with TemporaryDirectory() as tmp:
            fn = os.path.join(tmp, "file")
            with open(fn, "w") as f:
                f.write("data")

            emptyDirectory(tmp)
            assert os.path.exists(tmp)
            assert not os.path.exists(fn)

    def testDir(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            emptyDirectory(tmp)
            assert os.path.exists(tmp)
            assert not os.path.exists(d)

    @skipIf(isWindows(), "requires Unix platform")
    def testPermission(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
            self.assertRaises(BuildError, emptyDirectory, tmp)
            os.chmod(d, stat.S_IRWXU)

class TestVersions(TestCase):
    def testRegular(self):
        self.assertTrue(compareVersion("0.1", "0.1.0") == 0)
        self.assertTrue(compareVersion("0.2.0", "0.2") == 0)

        self.assertTrue(compareVersion("0.2", "0.1.0") > 0)
        self.assertTrue(compareVersion("0.2.1", "0.3") < 0)

    def testRc(self):
        self.assertTrue(compareVersion("0.1rc1", "0.1") < 0)
        self.assertTrue(compareVersion("0.1.0rc1", "0.1") < 0)
        self.assertTrue(compareVersion("0.2.0rc1", "0.2rc1") == 0)
        self.assertTrue(compareVersion("0.2.0rc2", "0.2.0rc1") > 0)
        self.assertTrue(compareVersion("0.4.0", "0.4rc4") > 0)

    def testDev(self):
        self.assertTrue(compareVersion("0.15", "0.15.0.dev4") > 0)
        self.assertTrue(compareVersion("0.15.dev5", "0.15.0.dev4") > 0)
        self.assertTrue(compareVersion("0.15.0rc1", "0.15.0rc1.dev4") > 0)

    def testInvalid(self):
        self.assertRaises(ParseError, compareVersion, "v0.15", "0.15")
        self.assertRaises(ParseError, compareVersion, "0.15", "0.15a4")
        self.assertRaises(ParseError, compareVersion, "0.15.devv4", "0.15")


def copyAsSymlink(target, name):
    import shutil
    shutil.copy(target, name)

def symlinkFail(target, name):
    raise OSError

class TestPlatformTag(TestCase):
    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'linux')
    def testPosix(self):
        self.assertEqual(getPlatformTag(), b'')

    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    @patch('bob.utils.os.symlink', symlinkFail)
    def testMSYSLinkFail(self):
        self.assertEqual(getPlatformTag(), b'm')

    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    @patch('bob.utils.os.symlink', copyAsSymlink)
    def testMSYSLinkCopy(self):
        self.assertEqual(getPlatformTag(), b'm')

    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    def testMSYSLinkOk(self):
        self.assertEqual(getPlatformTag(), b'ml')

    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'win32')
    @patch('bob.utils.os.symlink', symlinkFail)
    def testWindowsLinkFail(self):
        self.assertEqual(getPlatformTag(), b'w')

    @patch('bob.utils.__canSymlink', None)
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'win32')
    def testWindowsLinkOk(self):
        self.assertEqual(getPlatformTag(), b'wl')


class TestAsyncSubprocess(TestCase):
    """Test our asyncio subprocess convenience wrappers"""

    def testRunSuccess(self):
        coro = run("echo ok", shell=True)
        proc = runInEventLoop(coro)
        self.assertEqual(proc.returncode, 0)

    def testRunCaptureStr(self):
        import subprocess
        coro = run("echo ok&& echo err>&2", shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        proc = runInEventLoop(coro)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "ok\n")
        self.assertEqual(proc.stderr, "err\n")

    def testRunFail(self):
        coro = run(["false"])
        proc = runInEventLoop(coro)
        self.assertNotEqual(proc.returncode, 0)

    def testCheckOutputBin(self):
        coro = check_output("echo ok", shell=True)
        stdout = runInEventLoop(coro)
        if sys.platform == "win32":
            self.assertEqual(stdout, b'ok\r\n')
        else:
            self.assertEqual(stdout, b'ok\n')

    def testCheckOutputStr(self):
        coro = check_output("echo ok", shell=True, universal_newlines=True)
        stdout = runInEventLoop(coro)
        self.assertEqual(stdout, "ok\n")

    def testCheckOutputFail(self):
        from subprocess import CalledProcessError
        coro = check_output(["false"])
        self.assertRaises(CalledProcessError,
            runInEventLoop, coro)

class TestRemoveUserFromUrl(TestCase):
    """Test removal of user from URL"""

    def testRegular(self):
        self.assertEqual(removeUserFromUrl("scheme://host.xz/path/to/repo.git"),
            "scheme://host.xz/path/to/repo.git")

    def testRegularWithAt(self):
        self.assertEqual(removeUserFromUrl("scheme://host.xz/path/t@/repo.git"),
            "scheme://host.xz/path/t@/repo.git")

    def testRegularTrailingSlash(self):
        self.assertEqual(removeUserFromUrl("scheme://host.xz/path/to/repo.git/"),
            "scheme://host.xz/path/to/repo.git/")

    def testRegularWithPort(self):
        self.assertEqual(removeUserFromUrl("scheme://host.xz:8080/path/to/repo.git/"),
            "scheme://host.xz:8080/path/to/repo.git/")

    def testRegularUser(self):
        self.assertEqual(removeUserFromUrl("scheme://user@host.xz/path/to/repo.git/"),
            "scheme://host.xz/path/to/repo.git/")

    def testRegulserUserAndPort(self):
        self.assertEqual(removeUserFromUrl("scheme://user@host.xz:8080/path/to/repo.git/"),
            "scheme://host.xz:8080/path/to/repo.git/")

    def testScpLike(self):
        self.assertEqual(removeUserFromUrl("host.xz:path/to/repo.git/"),
            "host.xz:path/to/repo.git/")

    def testScpLikeUser(self):
        self.assertEqual(removeUserFromUrl("user@host.xz:path/to/repo.git/"),
            "host.xz:path/to/repo.git/")

    def testAbsPath(self):
        self.assertEqual(removeUserFromUrl("/path/to/repo.git/"),
            "/path/to/repo.git/")

    def testAbsPathWithColon(self):
        self.assertEqual(removeUserFromUrl("/foo:bar/repo.git/"),
            "/foo:bar/repo.git/")

    def testRelPath(self):
        self.assertEqual(removeUserFromUrl("repo.git"),
            "repo.git")

    def testAbsPathWithColon(self):
        self.assertEqual(removeUserFromUrl("./foo:bar"),
            "./foo:bar")

    def testAbsPathWithColonAndAt(self):
        self.assertEqual(removeUserFromUrl("./foo@bar:baz"),
            "./foo@bar:baz")

    def testFileUrl(self):
        self.assertEqual(removeUserFromUrl("file:///path/to/repo.git/"),
            "file:///path/to/repo.git/")

    def testInvalid(self):
      self.assertEqual(removeUserFromUrl("invalid"),
        "invalid")

    def testWinPath(self):
        self.assertEqual(removeUserFromUrl(r"C:\foo.bar"),
            r"C:\foo.bar")

    def testWinPathForwardSlash(self):
        self.assertEqual(removeUserFromUrl(r"C:/foo.bar"),
            r"C:/foo.bar")

    def testWinPathFileUrl(self):
        self.assertEqual(removeUserFromUrl(r"file:///C:/foo.bar"),
            r"file:///C:/foo.bar")

    def testWinPathFileUrlBackslash(self):
        self.assertEqual(removeUserFromUrl(r"file:///C:\foo.bar"),
            r"file:///C:\foo.bar")

    def testWinUncPath(self):
        self.assertEqual(removeUserFromUrl(r"\\server\path"),
            r"\\server\path")

    def testWinUncFileUrl(self):
        self.assertEqual(removeUserFromUrl(r"file:///\\server\path"),
            r"file:///\\server\path")

class TestReplacePath(TestCase):
    def testWin32Ok(self):
        m = MagicMock(return_value=None)
        with patch('os.replace', m):
            _replacePathWin32("foo", "bar")
        m.assert_called_with("foo", "bar")

    def testWin32Spurious(self):
        """One PermissionError is discarded"""
        m = MagicMock(side_effect=[PermissionError(42), None])
        with patch('os.replace', m):
            _replacePathWin32("foo", "bar")
        m.assert_called_with("foo", "bar")

    @patch('time.sleep', MagicMock())
    def testWin32Fail(self):
        """Sticky PermissionError is still raised"""
        m = MagicMock(side_effect=PermissionError(42))
        with self.assertRaises(PermissionError):
            with patch('os.replace', m):
                _replacePathWin32("foo", "bar")
        m.assert_called_with("foo", "bar")

class TestUpdateDictRecursive(TestCase):
    def testSameSchema(self):
        """Verify updates of dictionary keys"""
        d = { "a" : 1, "b" : { "foo" : "bar" }, "c" : [ 42 ] }
        u = { "b" : { "foo" : "baz", "x" : 2 }, "c" : [ 11 ] }
        self.assertEqual(updateDicRecursive(d, u),
                         { "a" : 1, "b" : { "foo" : "baz", "x" : 2 },
                           "c" : [ 42, 11 ] })

    def testChangeSchema(self):
        d = { "a" : 1, "b" : { "foo" : "bar" }, "c" : [ 42 ] }
        u = { "a" : { "foo" : "bar" }, "b" : [42], "c" : 1 }
        self.assertEqual(updateDicRecursive(d, u),
                         { "a" : { "foo" : "bar" }, "b" : [42], "c" : 1 })
