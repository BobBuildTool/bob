# Bob build tool
# Copyright (C) 2016 BobBuildTool team
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase

import os
import subprocess
import tempfile

from bob.input import GitScm
from bob.utils import removePath

class TestGitScmStatus(TestCase):
    repodir = ""
    repodir_local = ""

    def createGitScm(self, spec = {}):
        s = { 'scm' : "git", 'url' : self.repodir, 'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo" }
        s.update(spec)
        return GitScm(s)

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

        self.callGit('git clone ' + self.repodir + ' ' + self.repodir_local, cwd='/tmp')

        # setup user name and email for travis
        self.callGit('git config user.email "bob@bob.bob"', cwd=self.repodir_local)
        self.callGit('git config user.name test', cwd=self.repodir_local)

    def testBranch(self):
        s = self.createGitScm({ 'branch' : 'anybranch' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testClean(self):
        s = self.createGitScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'clean')

    def testCommit(self):
        s = self.createGitScm({ 'commit' : '0123456789012345678901234567890123456789' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testEmpty(self):
        removePath(self.repodir_local)
        s = self.createGitScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'empty')

    def testModified(self):
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        s = self.createGitScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testTag(self):
        s = self.createGitScm({ 'tag' : 'v0.1' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testUnpushed(self):
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        self.callGit('git commit -a -m "modified"', cwd=self.repodir_local)

        s = self.createGitScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testUrl(self):
        s = self.createGitScm({ 'url' : 'anywhere' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

