# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pipes import quote
from unittest import TestCase
from unittest.mock import MagicMock
import asyncio
import os
import subprocess
import tempfile
import hashlib

from bob.input import UrlScm
from bob.invoker import Invoker
from bob.utils import asHexStr

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

class TestLiveBuildId(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        fn = os.path.join(cls.__repodir.name, "test.txt")
        cls.url = "file://" + fn

        with open(fn, "w") as f:
            f.write("Hello world!")

        with open(fn, "rb") as f:
            d = hashlib.sha1()
            d.update(f.read())
            cls.urlSha1 = asHexStr(d.digest())

        with open(fn, "rb") as f:
            d = hashlib.sha256()
            d.update(f.read())
            cls.urlSha256 = asHexStr(d.digest())

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def invokeScm(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        run(scm.invoke(invoker))

    def callCalcLiveBuildId(self, scm):
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            return scm.calcLiveBuildId(workspace)

    def processHashEngine(self, scm, expected):
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            spec = scm.getLiveBuildIdSpec(workspace)
            if spec is None:
                self.assertEqual(None, expected)
            else:
                self.assertTrue(spec.startswith('='))
                self.assertEqual(bytes.fromhex(spec[1:]), expected)

    def createUrlScm(self, spec = {}):
        s = {
            'scm' : 'url',
            'url' : self.url,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return UrlScm(s)

    def testHasLiveBuildId(self):
        """Only with digest we have live-build-ids"""
        s = self.createUrlScm()
        self.assertFalse(s.hasLiveBuildId())
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertTrue(s.hasLiveBuildId())
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertTrue(s.hasLiveBuildId())

    def testPredictLiveBildId(self):
        """Predict live-build-id"""
        s = self.createUrlScm()
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), bytes.fromhex(self.urlSha256))

    def testCalcLiveBuildId(self):
        s = self.createUrlScm()
        self.assertEqual(self.callCalcLiveBuildId(s), None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertEqual(self.callCalcLiveBuildId(s), bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertEqual(self.callCalcLiveBuildId(s), bytes.fromhex(self.urlSha256))

    def testHashEngine(self):
        s = self.createUrlScm()
        self.processHashEngine(s, None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.processHashEngine(s, bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.processHashEngine(s, bytes.fromhex(self.urlSha256))

