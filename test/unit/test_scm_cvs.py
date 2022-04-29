# Bob build tool
# Copyright (C) 2019 Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase

import os
import subprocess
import tempfile

from bob.scm import CvsScm, ScmTaint
from bob.utils import emptyDirectory

class TestCvsScmStatus(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__cvsroot = tempfile.TemporaryDirectory()
        cls.cvsroot = cls.__cvsroot.name

        with tempfile.TemporaryDirectory() as tmp:
            # create template that is imported into svn repo
            with open(os.path.join(tmp, "test.txt"), "w") as f:
                f.write("dummy")
            # setup repo
            subprocess.check_call(['cvs', '-q', '-d', cls.cvsroot, 'init'], cwd="/tmp")
            # import some files
            subprocess.check_call(['cvs', '-q', '-d', cls.cvsroot, 'import', '-m',
                "Initial Import", 'testmod', 'bob', 'start'], cwd=tmp)

    @classmethod
    def tearDownClass(cls):
        cls.__cvsroot.cleanup()

    def setUp(self):
        self.__repodir = tempfile.TemporaryDirectory()
        self.repodir = self.__repodir.name

        # clone the repo
        subprocess.check_call(['cvs', '-q', '-d', self.cvsroot, 'co', '-d',
            self.repodir, 'testmod'], cwd='/tmp')

    def tearDown(self):
        self.__repodir.cleanup()

    def statusCvsScm(self, spec = {}):
        s = { 'scm' : "cvs", 'cvsroot' : self.cvsroot, 'module' : "testmod",
            'recipe' : "foo.yaml#0", '__source' : "Recipe foo" }
        s.update(spec)
        return CvsScm(s).status(self.repodir)

    def testClean(self):
        s = self.statusCvsScm()
        self.assertEqual(s.flags, set())
        self.assertTrue(s.clean)

    def testNonExisting(self):
        emptyDirectory(self.repodir)
        s = self.statusCvsScm()
        self.assertEqual(s.flags, {ScmTaint.error})
        self.assertTrue(s.error)

    def testOtherRoot(self):
        s = self.statusCvsScm({ 'cvsroot' : '/nonexisting' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testOtherModule(self):
        s = self.statusCvsScm({ 'module' : 'nonexisting' })
        self.assertEqual(s.flags, {ScmTaint.switched})
        self.assertTrue(s.dirty)

    def testModified(self):
        with open(os.path.join(self.repodir, "test.txt"), "w") as f:
            f.write("test modified")
        s = self.statusCvsScm()
        self.assertEqual(s.flags, {ScmTaint.modified})
        self.assertTrue(s.dirty)
