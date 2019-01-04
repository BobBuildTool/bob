# Bob build tool
# Copyright (C) 2016 BobBuildTool team
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase

import os
import subprocess
import tempfile

from bob.scm import SvnScm, ScmTaint
from bob.utils import emptyDirectory

class TestSvnScmStatus(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.repodir_root = tempfile.TemporaryDirectory()
        cls.repodir = os.path.join(cls.repodir_root.name, 'bobSvnTest')

        with tempfile.TemporaryDirectory() as tmp:
            # create template that is imported into svn repo
            with open(os.path.join(tmp, "test.txt"), "w") as f:
                f.write("dummy")
            # setup repo
            subprocess.check_call(['svnadmin', 'create', 'bobSvnTest'],
                cwd=cls.repodir_root.name)
            # import some files (bob's test director)
            subprocess.check_call(['svn', 'import', tmp,
                'file://'+cls.repodir+'/trunk', '-m', "Initial Import"],
                cwd='/tmp')

    @classmethod
    def tearDownClass(cls):
        cls.repodir_root.cleanup()

    def setUp(self):
        self.__repodir_local = tempfile.TemporaryDirectory()
        self.repodir_local = self.__repodir_local.name

        # clone the repo
        subprocess.check_call(['svn', 'co', 'file://' + self.repodir + '/trunk',
            self.repodir_local], cwd='/tmp')

    def tearDown(self):
        self.__repodir_local.cleanup()

    def statusSvnScm(self, spec = {}):
        s = { 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk',
            'recipe' : "foo.yaml#0", '__source' : "Recipe foo" }
        s.update(spec)
        return SvnScm(s).status(self.repodir_local)

    def testClean(self):
        s = self.statusSvnScm()
        self.assertEqual(s.flags, set())
        self.assertTrue(s.clean)

    def testNonExisting(self):
        emptyDirectory(self.repodir_local)
        s = self.statusSvnScm()
        self.assertEqual(s.flags, {ScmTaint.error})
        self.assertTrue(s.error)

    def testModified(self):
        with open(os.path.join(self.repodir_local, "test.txt"), "w") as f:
            f.write("test modified")
        s = self.statusSvnScm()
        self.assertEqual(s.flags, {ScmTaint.modified})
        self.assertTrue(s.dirty)

    def testRevision(self):
        s = self.statusSvnScm({ 'revision' : '2' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testUrl(self):
        s = self.statusSvnScm({ 'url' : 'file://'+self.repodir+'/branches/abc' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

