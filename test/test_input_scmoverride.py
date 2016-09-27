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

from unittest import TestCase

from bob.input import ScmOverride

class TestScmOverride(TestCase):

    def setUp(self):
        self.scm = {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        }

    def testDel(self):
        """Test to delete a key"""
        o = ScmOverride({ 'del' : [ 'branch' ] })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
        })

    def testAdd(self):
        """Test to add a new key"""
        o = ScmOverride({ 'set' : { 'commit' : '1234' } })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop",
            'commit' : "1234"
        })

    def testOverwrite(self):
        """Test to overwrite existing key"""
        o = ScmOverride({ 'set' : { 'branch' : "master" } })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

    def testReplace(self):
        """Test replacement via regex"""
        o = ScmOverride({
            'replace' : {
                'url' : {
                    'pattern'     : "@.*:",
                    'replacement' : "@acme.test:"
                }
            }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@acme.test:foo/bar.git",
            'branch' : "develop"
        })

    def testMatch(self):
        """Test matching (multiple) keys"""

        # match single key
        o = ScmOverride({
            'match' : { 'branch' : "develop" },
            'set' : { 'branch' : "master" }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

        # mismatch single key
        o = ScmOverride({
            'match' : { 'branch' : "upstream" },
            'set' : { 'branch' : "master" }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        })

        # match multiple keys
        o = ScmOverride({
            'match' : {
                'branch' : "develop",
                'url' : "git@git.com:foo/bar.git",
            },
            'set' : { 'branch' : "master" }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

        # mismatch one out of two keys
        o = ScmOverride({
            'match' : {
                'branch' : "develop",
                'url' : "asdfadgag",
            },
            'set' : { 'branch' : "master" }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        })

    def testMatchGlob(self):
        """Test that matching uses globbing"""
        o = ScmOverride({
            'match' : { 'url' : "*git.com*" },
            'set' : { 'url' : "mirror" }
        })
        self.assertEqual(o.mangle(self.scm), {
            'scm' : "git",
            'url' : "mirror",
            'branch' : "develop"
        })

