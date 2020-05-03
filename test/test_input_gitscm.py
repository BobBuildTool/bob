# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pipes import quote
from unittest import TestCase
from unittest.mock import MagicMock
import asyncio
import os
import subprocess
import tempfile

from bob.input import GitScm
from bob.invoker import Invoker
from bob.errors import ParseError
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

    def testDigestScripts(self):
        """Test digest script stable representation"""
        s = createGitScm()
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/master .")

        s = createGitScm({'branch' : "foobar", 'dir' : "sub/dir"})
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/foobar sub/dir")

        s = createGitScm({'tag' : "asdf"})
        self.assertEqual(s.asDigestScript(), "MyURL refs/tags/asdf .")

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertEqual(s.asDigestScript(), "0123456789abcdef0123456789abcdef01234567 .")

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

    def invokeGit(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        run(scm.invoke(invoker))


class TestGitRemotes(RealGitRepositoryTestCase):

    def callAndGetRemotes(self, workspace, scm):
        self.invokeGit(workspace, scm)
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
            self.invokeGit(workspace, scm)
            return scm.calcLiveBuildId(workspace)

    def processHashEngine(self, scm, expected):
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
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
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), self.commit_master)

        s = self.createGitScm({ 'branch' : 'foobar' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), self.commit_foobar)

    def testPredictLightweightTags(self):
        """Lightweight tags are just like branches"""
        s = self.createGitScm({ 'tag' : 'lightweight' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), self.commit_lightweight)

    def testPredictAnnotatedTags(self):
        """Predict commit object of annotated tags.

        Annotated tags are separate git objects that point to a commit object.
        We have to predict the commit object, not the tag object."""
        s = self.createGitScm({ 'tag' : 'annotated' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), self.commit_annotated)

    def testPredictCommit(self):
        """Predictions of explicit commit-ids are easy."""
        s = self.createGitScm({ 'commit' : asHexStr(self.commit_foobar) })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), self.commit_foobar)

    def testPredictBroken(self):
        """Predictions of broken URLs must not fail"""
        s = self.createGitScm({ 'url' : '/does/not/exist' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), None)

    def testPredictDeleted(self):
        """Predicting deleted branches/tags must not fail"""
        s = self.createGitScm({ 'branch' : 'nx' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), None)
        s = self.createGitScm({ 'tag' : 'nx' })
        self.assertEqual(run(s.predictLiveBuildId(DummyStep())), None)

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


class TestShallow(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.repodir = cls.__repodir.name

        cmds = """\
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test

            for i in $(seq 3) ; do
                echo "#$i" > test.txt
                git add test.txt
                GIT_AUTHOR_DATE="2020-01-0${i}T01:02:03" GIT_COMMITTER_DATE="2020-01-0${i}T01:02:03" git commit -m "commit $i"
            done

            git checkout -b feature

            for i in $(seq 4 6) ; do
                echo "#$i" > test.txt
                git add test.txt
                git commit -m "commit $i"
            done
        """
        subprocess.check_call(cmds, shell=True, cwd=cls.repodir)

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : "file://" + os.path.abspath(self.repodir),
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return GitScm(s)

    def invokeGit(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        run(scm.invoke(invoker))

        log = subprocess.check_output(["git", "log", "--oneline"],
            cwd=workspace, universal_newlines=True).strip().split("\n")

        branches = subprocess.check_output(["git", "branch", "-r"],
            cwd=workspace, universal_newlines=True).strip().split("\n")
        branches = set(b.strip() for b in branches)

        return (len(log), branches)


    def testShallowNum(self):
        """Verify that shallow clones the right number of commits.

        Also verify that it implies singleBranch as expected.
        """
        scm = self.createGitScm({ 'shallow' : 1 })
        with tempfile.TemporaryDirectory() as workspace:
            commits, branches = self.invokeGit(workspace, scm)
            self.assertEqual(commits, 1)
            self.assertEqual(branches, set(['origin/master']))

    def testShallowDate(self):
        """Verify that shallow clones the right number of commits.

        Also verify that it implies singleBranch as expected.
        """
        scm = self.createGitScm({ 'shallow' : "2020-01-02T00:00:00" })
        with tempfile.TemporaryDirectory() as workspace:
            commits, branches = self.invokeGit(workspace, scm)
            # Expect two commits 2020-01-03, 2020-01-02
            self.assertEqual(commits, 2)
            self.assertEqual(branches, set(['origin/master']))

    def testShallowNumAllBranches(self):
        """Verify that all branches can be fetched on shallow clones if requested"""
        scm = self.createGitScm({ 'shallow' : 1, 'singleBranch' : False })
        with tempfile.TemporaryDirectory() as workspace:
            commits, branches = self.invokeGit(workspace, scm)
            self.assertEqual(commits, 1)
            self.assertEqual(branches, set(['origin/master', 'origin/feature']))

    def testSingleBranch(self):
        """Check that singleBranch attribute works independently"""
        scm = self.createGitScm({ 'singleBranch' : True })
        with tempfile.TemporaryDirectory() as workspace:
            commits, branches = self.invokeGit(workspace, scm)
            self.assertEqual(branches, set(['origin/master']))

