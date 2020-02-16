# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import os, stat

from bob.utils import joinScripts, removePath, emptyDirectory, compareVersion, getPlatformTag
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

    def testPermission(self):
        with TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "dir")
            os.mkdir(d)
            with open(os.path.join(d, "file"), "w") as f:
                f.write("data")

            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)
            self.assertRaises(BuildError, removePath, tmp)
            os.chmod(d, stat.S_IRWXU)

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
    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'linux')
    def testPosix(self):
        self.assertEqual(getPlatformTag(), b'')

    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    @patch('bob.utils.os.symlink', symlinkFail)
    def testMSYSLinkFail(self):
        self.assertEqual(getPlatformTag(), b'm')

    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    @patch('bob.utils.os.symlink', copyAsSymlink)
    def testMSYSLinkCopy(self):
        self.assertEqual(getPlatformTag(), b'm')

    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'msys')
    def testMSYSLinkOk(self):
        self.assertEqual(getPlatformTag(), b'ml')

    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'win32')
    @patch('bob.utils.os.symlink', symlinkFail)
    def testWindowsLinkFail(self):
        self.assertEqual(getPlatformTag(), b'w')

    @patch('bob.utils.__platformTag', None)
    @patch('bob.utils.sys.platform', 'win32')
    def testWindowsLinkOk(self):
        self.assertEqual(getPlatformTag(), b'wl')
