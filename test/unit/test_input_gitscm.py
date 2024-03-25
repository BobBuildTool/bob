# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from shlex import quote
from unittest import TestCase, skip
from unittest.mock import MagicMock
import asyncio
import os
import subprocess
import tempfile

from bob.input import GitScm
from bob.invoker import Invoker, CmdFailedError, InvocationError
from bob.errors import ParseError
from bob.utils import asHexStr, runInEventLoop, getBashPath

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()

class DummyConfig:
    def __init__(self):
        self.credentials = None
        self.scmGitShallow = False
        self.scmGitTimeout = None
        self.scmIgnoreHooks = False

def createGitScm(spec = {}):
    s = { 'scm' : "git", 'url' : "MyURL", 'recipe' : "foo.yaml#0",
        '__source' : "Recipe foo" }
    s.update(spec)
    return GitScm(s)

class TestGitScm(TestCase):

    def testDefault(self):
        """The default branch must be master"""
        s = createGitScm()
        p = s.getProperties(False)
        self.assertEqual(p['branch'], "master")
        self.assertEqual(p['dir'], ".")
        self.assertEqual(p['rev'], "refs/heads/master")

    def testRev(self):
        """Check variants of rev property"""
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567" })
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(p['commit'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({'rev' : "refs/tags/v1.2.3"})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")
        self.assertEqual(p['tag'], "v1.2.3")

        s = createGitScm({'rev' : "refs/heads/develop"})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/heads/develop")
        self.assertEqual(p['branch'], "develop")

    def testRevInverseMap(self):
        """Test that rev property reflects all possible specs"""
        s = createGitScm({'branch' : "foobar"})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/heads/foobar")

        s = createGitScm({'tag' : "asdf"})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

    def testRevLeastPriority(self):
        """Dedicated properties might override rev but still obey preference"""
        # commit
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'branch' : 'bar' })
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'tag' : 'foo' })
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # tag
        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'branch' : 'bar'})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")

        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'tag' : 'asdf'})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({ 'rev' : "refs/tags/v1.2.3",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # branch
        s = createGitScm({'rev' : "refs/heads/develop",
                          'branch' : 'bar'})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/heads/bar")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'tag' : 'asdf'})
        p = s.getProperties(False)
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties(False)
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

        s = createGitScm({'recursiveSubmodules' : True})
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/master .")

        s = createGitScm({'submodules' : True})
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/master . submodules")

        s = createGitScm({'submodules' : True, 'recurseSubmodules' : True})
        self.assertEqual(s.asDigestScript(), "MyURL refs/heads/master . submodules recursive")

    def testJenkinsXML(self):
        """Test Jenins XML generation"""
        c = DummyConfig()

        # TODO: validate XML
        s = createGitScm()
        s.asJenkins("workspace/sub/dir", c)
        c.credentials = "uuid"
        s.asJenkins("workspace/sub/dir", c)
        c.shallow = "42"
        s.asJenkins("workspace/sub/dir", c)
        c.scmGitTimeout = "42"
        s.asJenkins("workspace/sub/dir", c)
        c.scmIgnoreHooks = True
        s.asJenkins("workspace/sub/dir", c)

        c = DummyConfig()

        s = createGitScm({'branch' : "foobar", 'dir' : "sub/dir"})
        s.asJenkins("workspace/sub/dir", c)
        s = createGitScm({'tag' : "asdf"})
        s.asJenkins("workspace/sub/dir", c)
        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        s.asJenkins("workspace/sub/dir", c)
        s = createGitScm({'shallow' : 1})
        s.asJenkins("workspace/sub/dir", c)
        s = createGitScm({'submodules' : True})
        s.asJenkins("workspace/sub/dir", c)
        s = createGitScm({'submodules' : True, 'recurseSubmodules' : True})
        s.asJenkins("workspace/sub/dir", c)

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
        self.assertEqual(s1.getProperties(False)['remote-test_user'], "test/url")
        self.assertEqual(s1.getProperties(False)['remote-other_user'], "other/url")

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

        subprocess.check_call(['git', 'init', '--bare', '.'], cwd=cls.repodir)

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
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=tmp)

            def revParse(obj):
                return bytes.fromhex(subprocess.check_output(['git', 'rev-parse', obj],
                    universal_newlines=True, cwd=tmp).strip())

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
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))


class TestGitRemotes(RealGitRepositoryTestCase):

    def callAndGetRemotes(self, workspace, scm):
        self.invokeGit(workspace, scm)
        remotes = subprocess.check_output(["git", "remote", "-v"],
            cwd=os.path.join(workspace, scm.getProperties(False)['dir']),
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

    def testHasLiveBuildId(self):
        """GitScm's always support live-build-ids"""
        s = self.createGitScm()
        self.assertTrue(s.hasLiveBuildId())

    def testPredictBranch(self):
        """See if we can predict remote branches correctly"""
        s = self.createGitScm()
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.commit_master)

        s = self.createGitScm({ 'branch' : 'foobar' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.commit_foobar)

    def testPredictLightweightTags(self):
        """Lightweight tags are just like branches"""
        s = self.createGitScm({ 'tag' : 'lightweight' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.commit_lightweight)

    def testPredictAnnotatedTags(self):
        """Predict commit object of annotated tags.

        Annotated tags are separate git objects that point to a commit object.
        We have to predict the commit object, not the tag object."""
        s = self.createGitScm({ 'tag' : 'annotated' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.commit_annotated)

    def testPredictCommit(self):
        """Predictions of explicit commit-ids are easy."""
        s = self.createGitScm({ 'commit' : asHexStr(self.commit_foobar) })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.commit_foobar)

    def testPredictBroken(self):
        """Predictions of broken URLs must not fail"""
        s = self.createGitScm({ 'url' : '/does/not/exist' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), None)

    def testPredictDeleted(self):
        """Predicting deleted branches/tags must not fail"""
        s = self.createGitScm({ 'branch' : 'nx' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), None)
        s = self.createGitScm({ 'tag' : 'nx' })
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), None)

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
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=cls.repodir)

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
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

        log = subprocess.check_output(["git", "log", "--oneline"],
            cwd=workspace, universal_newlines=True).strip().split("\n")

        branches = subprocess.check_output(["git", "branch", "-r"],
            cwd=workspace, universal_newlines=True).strip().split("\n")
        branches = set(b.strip() for b in branches)

        return (len(log), branches)

    def testShallowFail(self):
        scm = self.createGitScm({ 'shallow' : 1,
            'commit' : 'aabfa2e71de48ce8ed4dc51816572935593e6f04'})
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                commits, branches = self.invokeGit(workspace, scm)

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

class TestSubmodules(TestCase):

    def setUp(self):
        self.__repodir = tempfile.TemporaryDirectory()
        self.repodir = self.__repodir.name

        cmds = """\
            mkdir -p main sub1 subsub1 sub2

            # make sub-submodule
            cd subsub1
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo subsub > subsub.txt
            git add subsub.txt
            git commit -m import
            cd ..

            # setup first submodule
            cd sub1
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo 1 > test.txt
            git add test.txt
            mkdir -p some/deep
            git submodule add --name whatever ../subsub1 some/deep/path
            git commit -m "commit 1"
            echo 2 > test.txt
            git commit -a -m "commit 2"
            cd ..

            # setup main module and add first submodule
            cd main
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo 1 > test.txt
            git add test.txt
            git submodule add ../sub1
            git commit -m "commit 1"
            git tag -a -m 'Tag 1' tag1
            cd ..
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

    def tearDown(self):
        self.__repodir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : "file://" + os.path.abspath(self.repodir) + "/main",
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return GitScm(s)

    def invokeGit(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

    def updateSub1(self):
        # update sub- and main-module
        cmds = """\
            cd sub1
            echo 2 > test2.txt
            git add test2.txt
            git commit -m "commit 2"
            cd ..

            cd main/sub1
            git pull
            cd ..
            git add sub1
            git commit -m "commit 2"
            cd ..
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

    def updateSub1Sub(self):
        # update sub-sub-, sub- and main-module
        cmds = """\
            cd subsub1
            echo canary > canary.txt
            git add canary.txt
            git commit -m canary
            cd ..

            cd sub1/some/deep/path
            git pull
            cd ../../..
            git add some/deep/path
            git commit -m update
            cd ..

            cd main/sub1
            git pull
            cd ..
            git add sub1
            git commit -m update
            cd ..
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

    def addSub2(self):
        # Add 2nd submodule
        cmds = """\
            cd sub2
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo 2 > test.txt
            git add test.txt
            git commit -m "commit"
            cd ..

            cd main
            git submodule add ../sub2
            git commit -m "commit 2"
            cd ..
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

    def testSubmoduleIgnoreDefault(self):
        """Test that submodules are ignored by default"""
        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))

    def testSubmoduleClone(self):
        """Test cloning of submodules

        Make sure sub-submodules are not cloned by default.
        """

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))

        scm = self.createGitScm({ 'submodules' : True, 'tag' : 'tag1' })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))

    def testSubmoduleUpdate(self):
        """Test update of submodule

        A regular update should fetch updated submodules too."""

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))

            self.updateSub1()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))

    def testSubmoduleUpdateSwitched(self):
        """Test update of switched submodule

        If the submodule was switched to a branch it must not be updated."""

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))

            subprocess.check_call(["git", "-C", "sub1", "checkout", "master"], cwd=workspace)
            self.updateSub1()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))

    def testSubmoduleUpdateCommitted(self):
        """Test update of submodule that is on other commit

        If the submodule commit does not match the parent tree it must not be
        updated.
        """

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))

            cmds = """\
                cd sub1
                git config user.email "bob@bob.bob"
                git config user.name test
                echo canary > canary.txt
                git add canary.txt
                git commit -m canary
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=workspace)
            self.updateSub1()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/canary.txt")))

    def testSubmoduleAdded(self):
        """Test addition of submodules on update"""

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub2/test.txt")))

            # update sub- and main module
            self.addSub2()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub2/test.txt")))

    @skip("Seems unsupported by git. Leaves an unversioned directory.")
    def testSubmoduleRemove(self):
        """Test removal of submodules on update"""

        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))

            # update sub- and main module
            cmds = """\
                cd main
                git config user.email "bob@bob.bob"
                git config user.name test
                git rm sub1
                git commit -m "commit 2"
                cd ..
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

            self.invokeGit(workspace, scm)
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test.txt")))

    def testSubmoduleCloneRecursive(self):
        """Test recursive cloning of submodules"""

        scm = self.createGitScm({ 'submodules' : True, "recurseSubmodules" : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))

        scm = self.createGitScm({ 'tag' : 'tag1', 'submodules' : True,
                                   "recurseSubmodules" : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))

    def testSubSubmoduleUpdate(self):
        """Test update of sub-submodule

        A regular update should fetch updated sub-submodules too."""

        scm = self.createGitScm({ 'submodules' : True, "recurseSubmodules" : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/canary.txt")))

            self.updateSub1Sub()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/canary.txt")))

    def testSubSubmoduleUpdateSwitched(self):
        """Test update of switched sub-submodule

        Like a submodule a sub-submodule that was switched to a branch must not
        be updated.
        """

        scm = self.createGitScm({ 'submodules' : True, "recurseSubmodules" : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/canary.txt")))

            subprocess.check_call(["git", "-C", "sub1/some/deep/path", "checkout", "master"], cwd=workspace)
            self.updateSub1Sub()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/subsub.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/some/deep/path/canary.txt")))

    def testSubmodulesShallow(self):
        """Test that submodules are cloned shallowly by default"""
        scm = self.createGitScm({ 'submodules' : True })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            log = subprocess.check_output(["git", "-C", "sub1", "log", "--oneline"],
                cwd=workspace, universal_newlines=True).splitlines()
            self.assertEqual(len(log), 1)

    def testSubmodulesFullHistory(self):
        """Test that submodules can be cloned with full history"""
        scm = self.createGitScm({ 'submodules' : True, 'shallowSubmodules' : False })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            log = subprocess.check_output(["git", "-C", "sub1", "log", "--oneline"],
                cwd=workspace, universal_newlines=True).splitlines()
            self.assertTrue(len(log) > 1)

    def testSubmoduleCloneSpecific(self):
        """Test cloning of a subset of submodules"""
        self.addSub2()
        scm = self.createGitScm({ 'submodules' : ["sub2"] })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub2/test.txt")))

    def testSubmoduleUpdateSpecific(self):
        """Test update of a subset of submodules"""
        self.addSub2()
        scm = self.createGitScm({ 'submodules' : ["sub1"] })
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub2")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub2/test.txt")))

            self.updateSub1()

            self.invokeGit(workspace, scm)
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub1/test2.txt")))
            self.assertTrue(os.path.exists(os.path.join(workspace, "sub2")))
            self.assertFalse(os.path.exists(os.path.join(workspace, "sub2/test.txt")))

    def testSubmoduleCloneSpecificMissing(self):
        """Trying to clone a specific submodule that does not exist fails"""
        scm = self.createGitScm({ 'submodules' : ["sub42"] })
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(CmdFailedError):
                self.invokeGit(workspace, scm)


class TestRebase(TestCase):

    def setUp(self):
        self.__repodir = tempfile.TemporaryDirectory()
        self.repodir = self.__repodir.name

        cmds = """\
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo -n "hello world" > test.txt
            git add test.txt
            git commit -m "first commit"
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

    def tearDown(self):
        self.__repodir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : "file://" + os.path.abspath(self.repodir),
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
            "rebase" : True,
        }
        s.update(spec)
        return GitScm(s)

    def invokeGit(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

    def verify(self, workspace, content, file="test.txt"):
        with open(os.path.join(workspace, file)) as f:
            self.assertEqual(f.read(), content)

    def testNoChange(self):
        """Test rebase without upstream changes"""
        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")

    def testFastForwardRebase(self):
        """Test fast forward upstream movement"""
        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")

            # update upstream repository
            cmds = """\
                echo -n changed > test.txt
                git commit -a -m "commit 2"
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

            self.invokeGit(workspace, scm)
            self.verify(workspace, "changed")

    def testRebaseNoLocalChange(self):
        """Test update if upstream rebased without local commits"""

        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")

            # update upstream repository
            cmds = """\
                echo -n changed > test.txt
                git commit -a --amend --no-edit
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

            self.invokeGit(workspace, scm)
            self.verify(workspace, "changed")

    def testRebaseWithLocalChange(self):
        """Test update if upstream rebased with additional local commits"""

        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")

            # make some local commit
            cmds = """\
                git config user.email "bob@bob.bob"
                git config user.name test
                echo -n foo > additional.txt
                git add additional.txt
                git commit -m 'local commit'
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=workspace)

            # update upstream repository
            cmds = """\
                echo -n changed > test.txt
                git commit -a --amend --no-edit
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

            self.invokeGit(workspace, scm)
            self.verify(workspace, "changed")
            self.verify(workspace, "foo", "additional.txt")

    def testFastForwardUnknownTrackingOldState(self):
        """Test update if upstream ff'ed *and* old upstream commit is unknown"""

        scm = self.createGitScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeGit(workspace, scm)
            self.verify(workspace, "hello world")

            # delete local remote tracking branch
            cmds = """\
                git branch -d -r origin/master
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=workspace)

            # update upstream repository
            cmds = """\
                echo -n changed > test.txt
                git commit -a -m 'new commit'
            """
            subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.repodir)

            self.invokeGit(workspace, scm)
            self.verify(workspace, "changed")
