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

from bob.input import GitScm

def createGitScm(spec = {}):
    s = { 'scm' : "git", 'url' : "MyURL", 'recipe' : "foo.yaml#0" }
    s.update(spec)
    return GitScm(s)

class TestGitScm(TestCase):

    def testDefault(self):
        """The default branch must be master"""
        s = createGitScm()
        p = s.getProperties()[0]
        self.assertEqual(p['branch'], "master")
        self.assertEqual(p['dir'], ".")
        self.assertEqual(p['rev'], "refs/heads/master")

    def testRev(self):
        """Check variants of rev property"""
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567" })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(p['commit'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({'rev' : "refs/tags/v1.2.3"})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")
        self.assertEqual(p['tag'], "v1.2.3")

        s = createGitScm({'rev' : "refs/heads/develop"})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/heads/develop")
        self.assertEqual(p['branch'], "develop")

    def testRevInverseMap(self):
        """Test that rev property reflects all possible specs"""
        s = createGitScm({'branch' : "foobar"})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/heads/foobar")

        s = createGitScm({'tag' : "asdf"})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

    def testRevLeastPriority(self):
        """Dedicated properties might override rev but still obey preference"""
        # commit
        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'branch' : 'bar' })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'tag' : 'foo' })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0123456789abcdef0123456789abcdef01234567")

        s = createGitScm({ 'rev' : "0123456789abcdef0123456789abcdef01234567",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # tag
        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'branch' : 'bar'})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/tags/v1.2.3")

        s = createGitScm({'rev' : "refs/tags/v1.2.3",
                          'tag' : 'asdf'})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({ 'rev' : "refs/tags/v1.2.3",
                           'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

        # branch
        s = createGitScm({'rev' : "refs/heads/develop",
                          'branch' : 'bar'})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/heads/bar")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'tag' : 'asdf'})
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "refs/tags/asdf")

        s = createGitScm({'rev' : "refs/heads/develop",
                          'commit' : '0000000000000000000000000000000000000000' })
        p = s.getProperties()[0]
        self.assertEqual(p['rev'], "0000000000000000000000000000000000000000")

    def testScripts(self):
        """Test script generation"""
        s = createGitScm({'branch' : "foobar"})
        self.assertIsInstance(s.asScript(), str)

        s = createGitScm({'tag' : "asdf"})
        self.assertIsInstance(s.asScript(), str)

        s = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertIsInstance(s.asScript(), str)

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
        self.assertEqual(s2.merge(...), False)
        self.assertEqual(s2.merge(s1), False)
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), False)
        s2 = createGitScm({'tag' : "asdf"})
        self.assertEqual(s2.merge(...), False)
        self.assertEqual(s2.merge(s1), False)
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), True)
        s2 = createGitScm({'commit' : "0123456789abcdef0123456789abcdef01234567"})
        self.assertEqual(s2.merge(...), False)
        self.assertEqual(s2.merge(s1), False)
        self.assertEqual(s2.hasJenkinsPlugin(), True)
        self.assertEqual(s2.isDeterministic(), True)
