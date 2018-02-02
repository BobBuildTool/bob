# Bob build tool
# Copyright (C) 2017  Jan Klötzke
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

from pipes import quote
from unittest import TestCase
import os
import subprocess
import tempfile
import hashlib

from bob.input import UrlScm
from bob.utils import asHexStr


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

    def callCalcLiveBuildId(self, scm):
        with tempfile.TemporaryDirectory() as workspace:
            subprocess.check_call(['/bin/bash', '-c', scm.asScript()],
                universal_newlines=True, stderr=subprocess.STDOUT, cwd=workspace)
            return scm.calcLiveBuildId(workspace)

    def processHashEngine(self, scm, expected):
        with tempfile.TemporaryDirectory() as workspace:
            subprocess.check_call(['/bin/bash', '-c', scm.asScript()],
                universal_newlines=True, stderr=subprocess.STDOUT, cwd=workspace)
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
        self.assertEqual(s.predictLiveBuildId(), None)
        s = self.createUrlScm({'digestSHA1' : self.urlSha1})
        self.assertEqual(s.predictLiveBuildId(), bytes.fromhex(self.urlSha1))
        s = self.createUrlScm({'digestSHA256' : self.urlSha256})
        self.assertEqual(s.predictLiveBuildId(), bytes.fromhex(self.urlSha256))

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

