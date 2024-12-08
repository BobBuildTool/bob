# Bob build tool
# Copyright (C) 2016 BobBuildTool team
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock

import asyncio
import os
import subprocess
import tempfile

from bob.invoker import Invoker
from bob.scm import GitScm, ScmTaint, GitAudit
from bob.utils import removePath, runInEventLoop, getBashPath

class TestGitScmStatus(TestCase):
    repodir = ""
    repodir_local = ""

    def statusGitScm(self, spec = {}):
        s = { 'scm' : "git", 'url' : self.repodir, 'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo" }
        s.update(spec)
        return GitScm(s).status(self.repodir_local)

    def callGit(self, *arg, **kwargs):
        try:
            subprocess.check_output(*arg, shell=True, universal_newlines=True, stderr=subprocess.STDOUT, **kwargs)
        except subprocess.CalledProcessError as e:
            self.fail("git error: '{}' '{}'".format(arg, e.output))

    def tearDown(self):
        removePath(self.repodir)
        removePath(self.repodir_local)

    def setUp(self):
        self.repodir = tempfile.mkdtemp()
        self.repodir_local = tempfile.mkdtemp()

        self.callGit('git init', cwd=self.repodir)

        # setup user name and email for travis
        self.callGit('git config user.email "bob@bob.bob"', cwd=self.repodir)
        self.callGit('git config user.name test', cwd=self.repodir)

        f = open(os.path.join(self.repodir, "test.txt"), "w")
        f.write("hello world")
        f.close()
        self.callGit('git add test.txt', cwd=self.repodir)
        self.callGit('git commit -m "first commit"', cwd=self.repodir)

        # create a regular and a orphaned tag (one that is on no branch)
        self.callGit("git tag -a -m '1.0' v1.0", cwd=self.repodir)
        self.callGit("git checkout --detach", cwd=self.repodir)
        with open(os.path.join(self.repodir, "test.txt"), "w") as f:
            f.write("foo")
        self.callGit('git commit -a -m "second commit"', cwd=self.repodir)
        self.callGit("git tag -a -m '1.1' v1.1", cwd=self.repodir)

        # clone repository
        self.callGit('git init .', cwd=self.repodir_local)
        self.callGit('git remote add origin ' + self.repodir, cwd=self.repodir_local)
        self.callGit('git fetch origin', cwd=self.repodir_local)
        self.callGit('git checkout master', cwd=self.repodir_local)

        # setup user name and email for travis
        self.callGit('git config user.email "bob@bob.bob"', cwd=self.repodir_local)
        self.callGit('git config user.name test', cwd=self.repodir_local)

    def testBranch(self):
        s = self.statusGitScm({ 'branch' : 'anybranch' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testClean(self):
        s = self.statusGitScm()
        self.assertEqual(s.flags, set())
        self.assertTrue(s.clean)

    def testCommit(self):
        s = self.statusGitScm({ 'commit' : '0123456789012345678901234567890123456789' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testNonExisting(self):
        removePath(self.repodir_local)
        s = self.statusGitScm()
        self.assertEqual(s.flags, {ScmTaint.error})
        self.assertTrue(s.error)

    def testModified(self):
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        s = self.statusGitScm()
        self.assertEqual(s.flags, {ScmTaint.modified})
        self.assertTrue(s.dirty)

    def testTag(self):
        s = self.statusGitScm({ 'tag' : 'v0.1' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testUnpushedMain(self):
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        self.callGit('git commit -a -m "modified"', cwd=self.repodir_local)

        s = self.statusGitScm()
        self.assertEqual(s.flags, {ScmTaint.unpushed_main})
        self.assertTrue(s.dirty)

    def testUnpushedLocal(self):
        self.callGit('git checkout -b unrelated', cwd=self.repodir_local)
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        self.callGit('git commit -a -m "modified"', cwd=self.repodir_local)
        self.callGit('git checkout master', cwd=self.repodir_local)

        s = self.statusGitScm()
        self.assertEqual(s.flags, {ScmTaint.unpushed_local})
        self.assertFalse(s.dirty)

    def testUnpushedBoth(self):
        self.callGit('git checkout -b unrelated', cwd=self.repodir_local)
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("unrelated modified")
        self.callGit('git commit -a -m whatever', cwd=self.repodir_local)
        self.callGit('git checkout master', cwd=self.repodir_local)
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        self.callGit('git commit -a -m "modified"', cwd=self.repodir_local)

        s = self.statusGitScm()
        self.assertEqual(s.flags, {ScmTaint.unpushed_main, ScmTaint.unpushed_local})
        self.assertTrue(s.dirty)

    def testUrl(self):
        s = self.statusGitScm({ 'url' : 'anywhere' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testOrphanedOk(self):
        self.callGit('git fetch origin tag v1.1', cwd=self.repodir_local)
        self.callGit('git checkout tags/v1.1', cwd=self.repodir_local)
        s = self.statusGitScm({ 'tag' : 'v1.1' })
        self.assertEqual(s.flags, set())

    def testNestedTagOk(self):
        self.callGit('git fetch origin tag v1.1', cwd=self.repodir_local)
        self.callGit('git tag -a -m nested nested v1.1', cwd=self.repodir_local)
        self.callGit('git tag -a -m double double nested', cwd=self.repodir_local)
        self.callGit('git checkout tags/double', cwd=self.repodir_local)
        s = self.statusGitScm({ 'tag' : 'double' })
        self.assertEqual(s.flags, set())


class TestSubmodulesStatus(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.repodir = cls.__repodir.name

        cmds = """\
            mkdir -p main sub subsub sub2

            # make sub-submodule
            cd subsub
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo subsub > test.txt
            git add test.txt
            git commit -m import
            cd ..

            # setup first submodule
            cd sub
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo sub > test.txt
            git add test.txt
            mkdir -p some/deep
            git submodule add --name whatever ../subsub some/deep/path
            git commit -m import
            cd ..

            # setup second submodule
            cd sub2
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo sub2 > test.txt
            git add test.txt
            git commit -m import
            cd ..

            # setup main module
            cd main
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test
            echo main > test.txt
            git add test.txt
            git submodule add ../sub
            git submodule add ../sub2
            git commit -m import
            cd ..
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=cls.repodir)

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def setUp(self):
        self.__workspaceDir = tempfile.TemporaryDirectory()
        self.workspace = self.__workspaceDir.name

    def tearDown(self):
        self.__workspaceDir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : os.path.join(os.path.abspath(self.repodir), "main"),
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
            'submodules' : True,
        }
        s.update(spec)
        return GitScm(s)

    def invokeGit(self, scm):
        spec = MagicMock(workspaceWorkspacePath=self.workspace, envWhiteList=set())
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

    def statusGitScm(self, scm):
        status = scm.status(self.workspace)
        _git, dir, extra = scm.getAuditSpec()
        audit = runInEventLoop(GitAudit.fromDir(self.workspace, dir, extra)).dump()
        return status, audit

    def testUnmodifiedRegular(self):
        scm = self.createGitScm()
        self.invokeGit(scm)
        status, audit = self.statusGitScm(scm)

        self.assertEqual(status.flags, set())
        self.assertTrue(status.clean)
        self.assertFalse(audit["dirty"])

    def testUnmodifiedRecursive(self):
        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)
        status, audit = self.statusGitScm(scm)

        self.assertEqual(status.flags, set())
        self.assertTrue(status.clean)
        self.assertFalse(audit["dirty"])

    def testModifiedSubmodule(self):
        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            echo modified > test.txt
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testModifiedNotCloned(self):
        """Test that modifications in not cloned submodules are detected"""
        scm = self.createGitScm({'submodules':False})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            echo created > some.txt
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testModifiedSubSubModule(self):
        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)

        cmd = """\
            cd sub/some/deep/path
            echo modified > test.txt
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testSwitchedSubmodule(self):
        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            git config user.email "bob@bob.bob"
            git config user.name test
            echo modified > test.txt
            git add test.txt
            git commit -m modified
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.switched, ScmTaint.unpushed_local})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testSwitchedSubSubModule(self):
        """Modify submodule and add commit to sub-submodule"""

        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            echo modified > test.txt
            cd some/deep/path
            git config user.email "bob@bob.bob"
            git config user.name test
            echo modified > test.txt
            git add test.txt
            git commit -m modified
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified, ScmTaint.switched, ScmTaint.unpushed_local})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testMissingSubmodule(self):
        scm = self.createGitScm()
        self.invokeGit(scm)

        cmd = """\
            git submodule deinit -f sub
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testMissingSubSubModule(self):
        scm = self.createGitScm({'recurseSubmodules':True})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            git submodule deinit -f some/deep/path
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testUnexpectedSubmodule(self):
        """Detect populated submodules when they should not exist"""

        scm = self.createGitScm({'submodules':False})
        self.invokeGit(scm)

        cmd = """\
            git submodule update --init
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testUnexpectedSubSubModule(self):
        """Detect populated sub-submodules when they should not exist"""

        scm = self.createGitScm()
        self.invokeGit(scm)

        cmd = """\
            cd sub
            git submodule update --init
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testSpecificUnmodified(self):
        """Cloning a subset of submodules does not lead to dirty status"""
        scm = self.createGitScm({'submodules' : ["sub2"]})
        self.invokeGit(scm)
        status, audit = self.statusGitScm(scm)

        self.assertEqual(status.flags, set())
        self.assertTrue(status.clean)
        self.assertFalse(audit["dirty"])

    def testSpecificModifiedSubmodule(self):
        """Modifications are still detected if subset of submodules are cloned"""
        scm = self.createGitScm({'submodules' : ["sub2"]})
        self.invokeGit(scm)

        cmd = """\
            cd sub2
            echo modified > test.txt
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testSpecificModifiedUnrelated(self):
        """Test modifications in not checked out submodule are detected"""
        scm = self.createGitScm({'submodules' : ["sub2"]})
        self.invokeGit(scm)

        cmd = """\
            cd sub
            echo created > some.txt
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testSpecificUnexpectedSubmodule(self):
        """Detect populated submodule when it was ignored"""

        scm = self.createGitScm({'submodules':["sub2"]})
        self.invokeGit(scm)

        cmd = """\
            git submodule update --init sub
        """
        subprocess.check_call([getBashPath(), "-c", cmd], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])


class TestRefStatus(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.repodir = cls.__repodir.name

        cmds = """\
            git init .
            git config user.email "bob@bob.bob"
            git config user.name test

            echo -n "hello world" > test.txt
            git add test.txt
            git commit -m "first commit"
            git update-ref refs/bob/foo HEAD

            echo -n "update" > test.txt
            git commit -a -m "second commit"
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=cls.repodir)

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def setUp(self):
        self.__workspaceDir = tempfile.TemporaryDirectory()
        self.workspace = self.__workspaceDir.name

    def tearDown(self):
        self.__workspaceDir.cleanup()

    def createGitScm(self, spec = {}):
        s = {
            'scm' : "git",
            'url' : "file://" + os.path.abspath(self.repodir),
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return GitScm(s)

    def invokeGit(self, scm):
        spec = MagicMock(workspaceWorkspacePath=self.workspace, envWhiteList=set())
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker))

    def statusGitScm(self, scm):
        status = scm.status(self.workspace)
        _git, dir, extra = scm.getAuditSpec()
        audit = runInEventLoop(GitAudit.fromDir(self.workspace, dir, extra)).dump()
        return status, audit

    def testClean(self):
        scm = self.createGitScm({ "rev" : "refs/bob/foo" })
        self.invokeGit(scm)
        status, audit = self.statusGitScm(scm)

        self.assertEqual(status.flags, set())
        self.assertTrue(status.clean)
        self.assertFalse(audit["dirty"])

    def testModified(self):
        scm = self.createGitScm({ "rev" : "refs/bob/foo" })
        self.invokeGit(scm)

        with open(os.path.join(self.workspace, "test.txt"), "w") as f:
            f.write("modified")

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.modified})
        self.assertTrue(status.dirty)
        self.assertTrue(audit["dirty"])

    def testCommitted(self):
        scm = self.createGitScm({ "rev" : "refs/bob/foo" })
        self.invokeGit(scm)

        cmds = """\
            git config user.email "bob@bob.bob"
            git config user.name test

            echo "test changed" > test.txt
            git commit -a -m "user commit"
        """
        subprocess.check_call([getBashPath(), "-c", cmds], cwd=self.workspace)

        status, audit = self.statusGitScm(scm)
        self.assertEqual(status.flags, {ScmTaint.switched, ScmTaint.unpushed_local})
        self.assertTrue(status.dirty)
        self.assertFalse(audit["dirty"])

