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
        self.assertEqual(c.sandbox.mode, "no")
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
        self.assertEqual(c.sandbox.mode, "yes")
        self.assertTrue(c.clean)

    def testSandboxModes(self):
        self.executeBobJenkinsCmd("set-options test --no-sandbox")
        self.assertEqual(BobState().getJenkinsConfig("test").sandbox.mode, "no")
        self.executeBobJenkinsCmd("set-options test --sandbox")
        self.assertEqual(BobState().getJenkinsConfig("test").sandbox.mode, "yes")
        self.executeBobJenkinsCmd("set-options test --slim-sandbox")
        self.assertEqual(BobState().getJenkinsConfig("test").sandbox.mode, "slim")
        self.executeBobJenkinsCmd("set-options test --dev-sandbox")
        self.assertEqual(BobState().getJenkinsConfig("test").sandbox.mode, "dev")
        self.executeBobJenkinsCmd("set-options test --strict-sandbox")
        self.assertEqual(BobState().getJenkinsConfig("test").sandbox.mode, "strict")

    def testPostBuildClean(self):
        self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=never")
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanSuccess)
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanFailure)

        self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=on-success")
        self.assertTrue(BobState().getJenkinsConfig("test").postBuildCleanSuccess)
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanFailure)

        self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=on-failure")
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanSuccess)
        self.assertTrue(BobState().getJenkinsConfig("test").postBuildCleanFailure)

        self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=always")
        self.assertTrue(BobState().getJenkinsConfig("test").postBuildCleanSuccess)
        self.assertTrue(BobState().getJenkinsConfig("test").postBuildCleanFailure)

        self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=")
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanSuccess)
        self.assertFalse(BobState().getJenkinsConfig("test").postBuildCleanFailure)

        with self.assertRaises(SystemExit):
            self.executeBobJenkinsCmd("set-options test -o jobs.clean.post-build=foo")
