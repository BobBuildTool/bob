# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
import os, stat

from bob.utils import joinScripts, removePath, emptyDirectory, compareVersion
from bob.errors import BuildError, ParseError

class TestJoinScripts(TestCase):

    def testEmpty(self):
        assert joinScripts([]) == None

    def testSingle(self):
        assert joinScripts(["asdf"]) == "asdf"
        assert joinScripts([None]) == None

    def testDual(self):
        s = joinScripts(["asdf", "qwer"]).splitlines()
        assert "asdf" in s
        assert "qwer" in s
        assert s.index("asdf") < s.index("qwer")

        assert joinScripts(["asdf", None]) == "asdf"
        assert joinScripts([None, "asdf"]) == "asdf"

        assert joinScripts([None, None]) == None

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
