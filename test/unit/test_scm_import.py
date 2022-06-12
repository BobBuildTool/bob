# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock, patch
import asyncio
import os
import tempfile

from bob.errors import BuildError
from bob.scm.imp import ImportScm, ImportAudit
from bob.invoker import Invoker, InvocationError
from bob.utils import hashDirectory, runInEventLoop

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()


class TestImportScm(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.url = os.path.abspath(cls.__repodir.name)

        cls.fn = os.path.join(cls.url, "test.txt")
        with open(cls.fn, "w") as f:
            f.write("Hello world!")
        os.symlink("test.txt", os.path.join(cls.url, "link.txt"))

        os.mkdir(os.path.join(cls.url, "sub"))
        with open(os.path.join(cls.url, "sub", "sub.txt"), "w") as f:
            f.write("Nested")

        cls.digest = hashDirectory(cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def createImportScm(self, spec = {}):
        s = {
            'scm' : 'import',
            'url' : self.url,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return ImportScm(s)

    def invokeScm(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

    def testProperties(self):
        """Query some static proerties of SCM"""
        s = self.createImportScm({"dir" : "subdir"})
        p = s.getProperties(False)
        self.assertEqual(p["scm"], "import")
        self.assertEqual(p["url"], self.url)

        self.assertEqual(s.asDigestScript(), self.url)
        self.assertEqual(s.getDirectory(), "subdir")
        self.assertEqual(s.isDeterministic(), False)
        self.assertEqual(s.hasLiveBuildId(), True)

    def testLiveBuildId(self):
        """Test prediction and calculation of live-build-id"""
        s = self.createImportScm()
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.digest)

        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, s.calcLiveBuildId(workspace))

    def testCopy(self):
        """Test straigt forward 'checkout'"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testCopyViaProperties(self):
        """Test Jenkins-like 'checkout' via properties"""
        s = ImportScm(self.createImportScm().getProperties(True))
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testObstruct(self):
        """Test that obstructed destination gracefully fails"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            os.mkdir(os.path.join(workspace, "test.txt"))
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, s)

    def testUpdate(self):
        """Test that updates of sources are copied"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            with open(self.fn, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            with open(os.path.join(workspace, "test.txt")) as f:
                self.assertEqual(f.read(), "Changed")

    def testNotUpdated(self):
        """Test that updates of destination are not overwriten"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            canary = os.path.join(workspace, "test.txt")
            with open(canary, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            with open(canary) as f:
                self.assertEqual(f.read(), "Changed")

    def testUpdateLink(self):
        """Test that symlinks are updated"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            os.symlink("notexist", os.path.join(workspace, "link.txt"))
            self.invokeScm(workspace, s)
            self.assertEqual(os.readlink(os.path.join(workspace, "link.txt")), "test.txt")

    def testCopyNoDirectory(self):
        """Test that source must be a directory"""
        s = self.createImportScm({"url":self.fn})
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, s)
            with self.assertRaises(BuildError):
                self.invokeScm(workspace, ImportScm(s.getProperties(True)))

    def testPrune(self):
        """Test that pruning destination works if requested"""
        s = self.createImportScm({"prune" : True})
        with tempfile.TemporaryDirectory() as workspace:
            canary = os.path.join(workspace, "test.txt")
            with open(canary, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testAudit(self):
        """Test audit record creation and import"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            audit = runInEventLoop(ImportAudit.fromDir(*s.getAuditSpec()))

            d = audit.dump()
            self.assertEqual(d["type"], "import")
            self.assertEqual(d["dir"], ".")
            self.assertEqual(d["url"], self.url)

            self.assertEqual(d, ImportAudit.fromData(d).dump())

