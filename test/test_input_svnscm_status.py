# Bob build tool
# Copyright (C) 2016 BobBuildTool team
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
        s = SvnScm({ 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk', 'recipe' : "foo.yaml#0" })
        self.assertEqual(s.status(self.repodir_local)[0], 'clean')

    def testEmpty(self):
        removePath(self.repodir_local)
        s = SvnScm({ 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk', 'recipe' : "foo.yaml#0" })
        self.assertEqual(s.status(self.repodir_local)[0], 'empty')

    def testModified(self):
        f = open(os.path.join(self.repodir_local, "test_input_svnscm_status.py"), "w")
        f.write("test modified")
        f.close()
        s = SvnScm({ 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk', 'recipe' : "foo.yaml#0" })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testRevision(self):
        s = SvnScm({ 'scm' : "svn", 'url' : 'file://'+self.repodir+'/trunk', 'revision' : '2', 'recipe' : "foo.yaml#0" })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

    def testUrl(self):
        s = SvnScm({ 'scm' : "svn", 'url' : 'file://'+self.repodir+'/branches/abc', 'recipe' : "foo.yaml#0" })
        self.assertEqual(s.status(self.repodir_local)[0], 'dirty')

