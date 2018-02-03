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

from pipes import quote
from unittest import TestCase
import os
import subprocess
import tempfile

from bob.input import GitScm
from bob.errors import ParseError
from bob.utils import asHexStr

def createGitScm(spec = {}):
    s = { 'scm' : "git", 'url' : "MyURL", 'recipe' : "foo.yaml#0",
        '__source' : "Recipe foo" }
    s.update(spec)
    return GitScm(s)

class TestGitScm(TestCase):

    def testDefault(self):
        """The default branch must be master"""
        s = createGitScm()
        p = s.getProperties()
        self.assertEqual(p['branch'], "master")
        self.assertEqual(p['dir'], ".")
        self.assertEqual(p['rev'], "refs/heads/master")

    def testRev(self):
        """Check variants of rev property"""
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567" })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(p['commit'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({'rev' : "refs/tags/v1.2.3"})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")
        self.assertEqual(p['tag'], "v1.2.3")

        s = createGitScm({'rev' : "refs/heads/develop"})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/heads/develop")
        self.assertEqual(p['branch'], "develop")

    def testRevInverseMap(self):
        """Test that rev property reflects all possible specs"""
        s = createGitScm({'branch' : "foobar"})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/heads/foobar")

        s = createGitScm({'tag' : "asdf"})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        p = s.getProperties()
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

    def testRevLeastPriority(self):
        """Dedicated properties might override rev but still obey preference"""
        # commit
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'branch' : 'bar' })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'tag' : 'foo' })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # tag
        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'branch' : 'bar'})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")

        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'tag' : 'asdf'})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({ 'rev' : "refs/tags/v1.2.3",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # branch
        s = createGitScm({'rev' : "refs/heads/develop",
                          'branch' : 'bar'})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/heads/bar")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'tag' : 'asdf'})
        p = s.getProperties()
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

    def testScripts(self):
        """Test script generation"""
        s = createGitScm({'branch' : "foobar"})
        self.assertIsInstance(s.asScript(), str)

        s = createGitScm({'tag' : "asdf"})
        self.assertIsInstance(s.asScript(), str)

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertIsInstance(s.asScript(), str)

        s = createGitScm({'remote-test' : "test/url.git"})
        self.assertRegexpMatches(s.asScript(), ".*test.*test/url.git.*")

    def testDigestScripts(self):
        """Test digest script stable representation"""
        s = createGitScm()
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/master .")
        self.assertEqual(s.getDirectories(),
            { '.' : b'o\xe5\xadn\xc2\xb9w\x9f`\x1b\x19\x9e\x88\xd3\x11t' })

        s = createGitScm({'branch' : "foobar", 'dir' : "sub/dir"})
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/foobar sub/dir")
        self.assertEqual(s.getDirectories(),
            { 'sub/dir' : b'W\xe7!\xed\xe8\x11\x15\xda\xe8\xc9G\xa4]\xc6\xdb\xc1' })

        s = createGitScm({'tag' : "asdf"})
        self.assertEqual(s.asDigestScript(), "MyURL refs/tags/asdf .")
        self.assertEqual(s.getDirectories(),
            { '.' : b'\xfd\xef,5\xc0it\xa3\x8cRTj\x90\x11\xce\x92' })

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertEqual(s.asDigestScript(), "0123456789abcdef0123456789abcdef01234567 .")
        self.assertEqual(s.getDirectories(),
            { '.' : b'QU\xa9;\x00N\x00(\x90\x0c\xe5\xd3\x01y\xa0a' })

    def testJenkinsXML(self):
        """Test Jenins XML generation"""
        # TODO: validate XML
        s = createGitScm()
        s.asJenkins("workspace/sub/dir", "uuid", {})
        s = createGitScm({'branch' : "foobar", 'dir' : "sub/dir"})
        s.asJenkins("workspace/sub/dir", "uuid", {})
        s = createGitScm({'tag' : "asdf"})
        s.asJenkins("workspace/sub/dir", "uuid", {})
        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        s.asJenkins("workspace/sub/dir", "uuid", {})

    def testMisc(self):
        s1 = createGitScm()
        self.assertEqual(s1.hasJenkinsPlugin(), True)
        self.assertEqual(s1.isDeterministic(), False)

        s2 = createGitScm({'branch' : "foobar", 'dir' : "sub/dir"})
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), False)
        s2 = createGitScm({'tag' : "asdf"})
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), True)
        s2 = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), True)

    def testRemotesSetAndGet(self):
        """Test setting and getting remotes as they are stored in a different format internally"""
        s1 = createGitScm({'remote-test_user' : "test/url", 'remote-other_user' : "other/url"})
        self.assertEqual(s1.getProperties()['remote-test_user'], "test/url")
        self.assertEqual(s1.getProperties()['remote-other_user'], "other/url")

    def testRemotesSetOrigin(self):
        """A remote calle origin should result in an error, because this is the default remote name"""
        self.assertRaises(ParseError, createGitScm, {'remote-origin' : "test/url.git"})


class RealGitRepositoryTestCase(TestCase):
    """
    Helper class that provides a "remote" git repository and some facilities to
    acutally run the checkout script.
    """

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.repodir = cls.__repodir.name

        subprocess.check_call('git init --bare .', shell=True, cwd=cls.repodir)

        with tempfile.TemporaryDirectory() as tmp:
            cmds = "\n".join([
                'git init .',
                'git config user.email "bob@bob.bob"',
                'git config user.name test',
                'echo "hello world" > test.txt',
                'git add test.txt',
                'git commit -m "first commit"',
                'git tag -a -m "First Tag" annotated',
                'git checkout -b foobar',
                'echo "changed" > test.txt',
                'git commit -a -m "second commit"',
                'git tag lightweight',
                'git remote add origin ' + quote(cls.repodir),
                'git push origin master foobar annotated lightweight',
            ])
            subprocess.check_call(cmds, shell=True, cwd=tmp)

            def revParse(obj):
                return bytes.fromhex(subprocess.check_output('git rev-parse ' + obj,
                    universal_newlines=True, shell=True, cwd=tmp).strip())

            cls.commit_master = revParse('master')
            cls.commit_foobar = revParse('foobar')
            cls.commit_annotated = revParse('annotated^{}')
            cls.commit_lightweight = revParse('lightweight')

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : self.repodir,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return GitScm(s)


class TestGitRemotes(RealGitRepositoryTestCase):

    def callAndGetRemotes(self, workspace, scm):
        subprocess.check_call(['/bin/bash', '-c', scm.asScript()],
            universal_newlines=True, stderr=subprocess.STDOUT, cwd=workspace)
        remotes = subprocess.check_output(["git", "remote", "-v"],
            cwd=os.path.join(workspace, scm.getProperties()['dir']),
            universal_newlines=True).split("\n")
        remotes = (r[:-8].split("\t") for r in remotes if r.endswith("(fetch)"))
        return { remote:url for (remote,url) in remotes }

    def testPlainCheckout(self):
        """Do regular checkout and verify origin"""
        s = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            remotes = self.callAndGetRemotes(workspace, s)
            self.assertEqual(remotes, { "origin" : self.repodir })

    def testAdditionalRemoteCheckout(self):
        """Initial checkout with two more remotes"""
        s = self.createGitScm({
            'remote-foo' : '/does/not/exist',
            'remote-bar' : 'http://bar.test/baz.git',
        })
        with tempfile.TemporaryDirectory() as workspace:
            remotes = self.callAndGetRemotes(workspace, s)
            self.assertEqual(remotes, {
                "origin" : self.repodir,
                'foo' : '/does/not/exist',
                'bar' : 'http://bar.test/baz.git',
            })

    def testSubDirCheckout(self):
        """Regression test for sub-directory checkouts"""
        s = self.createGitScm({'dir' : 'sub/dir'})
        with tempfile.TemporaryDirectory() as workspace:
            remotes = self.callAndGetRemotes(workspace, s)
            self.assertEqual(remotes, { "origin" : self.repodir })

        s = self.createGitScm({'dir' : 'sub/dir', 'tag' : 'annotated'})
        with tempfile.TemporaryDirectory() as workspace:
            remotes = self.callAndGetRemotes(workspace, s)
            self.assertEqual(remotes, { "origin" : self.repodir })

    def testChangeRemote(self):
        """Test that changed remotes in recipe are updated in the working copy"""
        s1 = self.createGitScm({
            'remote-bar' : 'http://bar.test/baz.git',
        })
        s2 = self.createGitScm({
            'remote-bar' : 'http://bar.test/foo.git',
        })
        with tempfile.TemporaryDirectory() as workspace:
            remotes = self.callAndGetRemotes(workspace, s1)
            self.assertEqual(remotes, {
                "origin" : self.repodir,
                'bar' : 'http://bar.test/baz.git',
            })
            remotes = self.callAndGetRemotes(workspace, s2)
            self.assertEqual(remotes, {
                "origin" : self.repodir,
                'bar' : 'http://bar.test/foo.git',
            })


class TestLiveBuildId(RealGitRepositoryTestCase):
    """Test live-build-id support of git scm"""

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
            if spec.startswith('='):
                self.assertEqual(bytes.fromhex(spec[1:]), expected)
            else:
                self.assertTrue(spec.startswith('g'))
                self.assertEqual(bytes.fromhex(GitScm.processLiveBuildIdSpec(spec[1:])),
                    expected)

    def testHasLiveBuildId(self):
        """GitScm's always support live-build-ids"""
        s = self.createGitScm()
        self.assertTrue(s.hasLiveBuildId())

    def testPredictBranch(self):
        """See if we can predict remote branches correctly"""
        s = self.createGitScm()
        self.assertEqual(s.predictLiveBuildId(), self.commit_master)

        s = self.createGitScm({ 'branch' : 'foobar' })
        self.assertEqual(s.predictLiveBuildId(), self.commit_foobar)

    def testPredictLightweightTags(self):
        """Lightweight tags are just like branches"""
        s = self.createGitScm({ 'tag' : 'lightweight' })
        self.assertEqual(s.predictLiveBuildId(), self.commit_lightweight)

    def testPredictAnnotatedTags(self):
        """Predict commit object of annotated tags.

        Annotated tags are separate git objects that point to a commit object.
        We have to predict the commit object, not the tag object."""
        s = self.createGitScm({ 'tag' : 'annotated' })
        self.assertEqual(s.predictLiveBuildId(), self.commit_annotated)

    def testPredictCommit(self):
        """Predictions of explicit commit-ids are easy."""
        s = self.createGitScm({ 'commit' : asHexStr(self.commit_foobar) })
        self.assertEqual(s.predictLiveBuildId(), self.commit_foobar)

    def testPredictBroken(self):
        """Predictions of broken URLs must not fail"""
        s = self.createGitScm({ 'url' : '/does/not/exist' })
        self.assertEqual(s.predictLiveBuildId(), None)

    def testPredictDeleted(self):
        """Predicting deleted branches/tags must not fail"""
        s = self.createGitScm({ 'branch' : 'nx' })
        self.assertEqual(s.predictLiveBuildId(), None)
        s = self.createGitScm({ 'tag' : 'nx' })
        self.assertEqual(s.predictLiveBuildId(), None)

    def testCalcBranch(self):
        """Clone branch and calculate live-build-id"""
        s = self.createGitScm()
        self.assertEqual(self.callCalcLiveBuildId(s), self.commit_master)
        s = self.createGitScm({ 'branch' : 'foobar' })
        self.assertEqual(self.callCalcLiveBuildId(s), self.commit_foobar)

    def testCalcTags(self):
        """Clone tag and calculate live-build-id"""
        s = self.createGitScm({ 'tag' : 'annotated' })
        self.assertEqual(self.callCalcLiveBuildId(s), self.commit_annotated)
        s = self.createGitScm({ 'tag' : 'lightweight' })
        self.assertEqual(self.callCalcLiveBuildId(s), self.commit_lightweight)

    def testCalcCommit(self):
        """Clone commit and calculate live-build-id"""
        s = self.createGitScm({ 'commit' : asHexStr(self.commit_foobar) })
        self.assertEqual(self.callCalcLiveBuildId(s), self.commit_foobar)

    def testHashEngine(self):
        """Calculate live-build-id via bob-hash-engine spec"""
        s = self.createGitScm()
        self.processHashEngine(s, self.commit_master)
        s = self.createGitScm({ 'branch' : 'foobar' })
        self.processHashEngine(s, self.commit_foobar)
        s = self.createGitScm({ 'tag' : 'annotated' })
        self.processHashEngine(s, self.commit_annotated)
        s = self.createGitScm({ 'tag' : 'lightweight' })
        self.processHashEngine(s, self.commit_lightweight)
        s = self.createGitScm({ 'commit' : asHexStr(self.commit_foobar) })
        self.processHashEngine(s, self.commit_foobar)
