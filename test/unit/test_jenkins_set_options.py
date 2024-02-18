# Bob build tool
# Copyright (C) 2022 Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from mocks.jenkins_tests import JenkinsTests
from unittest import TestCase

from bob.state import BobState
from bob.utils import getPlatformString


class TestJenkinsSetUrl(JenkinsTests, TestCase):
    """Verify handling of set-url command"""

    def testSetUrl(self):
        """Update URL is stored"""
        self.executeBobJenkinsCmd("set-url test http://change.test/")
        self.assertEqual("http://change.test/", BobState().getJenkinsConfig("test").url)

    def testSetUrlInvalidName(self):
        """Set URL of invalid Jenkins alias fails gracefully"""
        with self.assertRaises(SystemExit) as ex:
            self.executeBobJenkinsCmd("set-url unknown http://change.test/")
        self.assertEqual(ex.exception.code, 1)


class TestJenkinsSetOptions(JenkinsTests, TestCase):
    """Verify handling of set-options command"""

    def testUnkownName(self):
        """Set options of invalid Jenkins alias fails gracefully"""
        with self.assertRaises(SystemExit) as ex:
            self.executeBobJenkinsCmd("set-options unknown")
        self.assertEqual(ex.exception.code, 1)

    def testAllOptions(self):
        """Change all command line options"""
        opts = [
            "-n", "somenode",
            "--host-platform", "win32",
            "--prefix", "prefix",
            "--add-root", "root",
            "--add-root", "nonexist",
            "--add-root", "dummy",
            "--del-root", "unknown",
            "--del-root", "dummy",
            "-D", "FOO=bar", "-D", "DUMMY=baz",
            "-U", "DUMMY", "-U", "NEVERSET",
            "--credentials", "credentials",
            "--authtoken", "authtoken",
            "--shortdescription",
            "--keep", "--download", "--upload", "--no-sandbox", "--incremental",
        ]
        self.executeBobJenkinsCmd("set-options test " + " ".join(opts))

        c = BobState().getJenkinsConfig("test")
        self.assertEqual(c.nodes, "somenode")
        self.assertEqual(c.hostPlatform, "win32")
        self.assertTrue(c.windows)
        self.assertEqual(set(c.roots), {"root", "nonexist"})
        self.assertEqual(c.defines, { "FOO" : "bar" })
        self.assertEqual(c.credentials, "credentials")
        self.assertEqual(c.authtoken, "authtoken")
        self.assertTrue(c.shortdescription)
        self.assertTrue(c.keep)
        self.assertTrue(c.download)
        self.assertTrue(c.upload)
        self.assertFalse(c.sandbox)
        self.assertFalse(c.clean)

    def testReset(self):
        self.testAllOptions()
        self.executeBobJenkinsCmd("set-options test --reset")

        c = BobState().getJenkinsConfig("test")
        self.assertEqual(c.nodes, "")
        self.assertEqual(c.hostPlatform, getPlatformString())
        self.assertEqual(c.roots, [])
        self.assertEqual(c.defines, {})
        self.assertEqual(c.credentials, None)
        self.assertEqual(c.authtoken, None)
        self.assertFalse(c.shortdescription)
        self.assertFalse(c.keep)
        self.assertFalse(c.download)
        self.assertFalse(c.upload)
        self.assertTrue(c.sandbox)
        self.assertTrue(c.clean)
