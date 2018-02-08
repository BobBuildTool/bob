# Bob build tool
# Copyright (C) 2016 BobBuildTool team
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase

import os
import subprocess
import tempfile

from bob.input import SvnScm
from bob.utils import removePath

class TestSvnScmStatus(TestCase):
    repodir = ""
    repodir_root = ""
    repodir_local = ""

    def createSvnScm(self, spec = {}):
        s = { 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk',
            'recipe' : "foo.yaml#0", '__source' : "Recipe foo" }
        s.update(spec)
        return SvnScm(s)

    def callSubversion(self, *arg, **kwargs):
        try:
            subprocess.check_output(*arg, shell=True, universal_newlines=True, stderr=subprocess.STDOUT, **kwargs)
        except subprocess.CalledProcessError as e:
            self.fail("svn error: '{}' '{}'".format(arg, e.output))

    def tearDown(self):
        removePath(self.repodir)
        removePath(self.repodir_local)

    def setUp(self):
        self.repodir_root = tempfile.mkdtemp()
        self.repodir = os.path.join(self.repodir_root, 'bobSvnTest')
        self.repodir_local = tempfile.mkdtemp()

        self.callSubversion('svnadmin create bobSvnTest', cwd=self.repodir_root)
        # import some files (bob's test director)
        self.callSubversion('svn import ' + os.getcwd() + ' file://'+self.repodir+'/trunk -m "Initial Import"', cwd='/tmp')

        # now clone the repo
        self.callSubversion('svn co file://' + self.repodir + '/trunk ' + self.repodir_local, cwd='/tmp')

    def testClean(self):
        s = self.createSvnScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'clean')

    def testEmpty(self):
        removePath(self.repodir_local)
        s = self.createSvnScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'empty')

    def testModified(self):
        with open(os.path.join(self.repodir_local, "test_input_svnscm_status.py"), "w") as f:
            f.write("test modified")
        s = self.createSvnScm()
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testRevision(self):
        s = self.createSvnScm({ 'revision' : '2' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testUrl(self):
        s = self.createSvnScm({ 'url' : 'file://'+self.repodir+'/branches/abc' })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

