# Bob build tool
# Copyright (C) 2016 BobBuildToolTeam
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

import os, re
import tempfile
from xml.etree import ElementTree
from unittest import TestCase
from bob.utils import removePath
from bob.errors import BuildError

from jenkins.jenkins_mock import JenkinsMock

from bob.cmds.jenkins import doJenkins
from bob.state import finalize

class TestJenkinsSetOptions(TestCase):
    def executeBobJenkinsCmd(self, arg):
        doJenkins(arg.split(' '), self.cwd)
        try:
            finalize()
        except FileNotFoundError:
            pass

    def tearDown(self):
        self.executeBobJenkinsCmd("prune -q myTestJenkins")
        self.executeBobJenkinsCmd("rm myTestJenkins")

        # do bob jenkins prune & remove
        self.jenkinsMock.stop_mock_server(8080)

        removePath(self.cwd)

    def setUp(self):
        RECIPE = """
root: True

checkoutSCM:
    -
      scm: git
      url: git@mytest.de
      branch: test

buildScript: |
    echo 'build'
packageScript: |
    echo 'package'
        """
        self.cwd = tempfile.mkdtemp()
        self.jenkinsMock = JenkinsMock()
        self.jenkinsMock.start_mock_server(8080)
        self.jenkinsMock.getServerData()
        os.chdir(self.cwd)
        os.mkdir("recipes")
        with open(os.path.join("recipes", "test.yaml"), "w") as f:
            print(RECIPE, file=f)

        # do bob jenkins add
        self.executeBobJenkinsCmd("add myTestJenkins http://localhost:8080 -r test")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        self.jenkinsMock.getServerData()

    def testSetNode(self):
        self.executeBobJenkinsCmd("set-options -n testSlave myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('<assignedNode>testSlave</assignedNode>' in send[0][1].decode('utf-8'))

    def testSetSandBox(self):
        self.executeBobJenkinsCmd("set-options --sandbox myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('sandbox' in send[0][1].decode('utf-8'))

    def testUpDownload(self):
        DEFAULT="""
archive:
   backend: http
   url: "http://localhost:8001/upload"
        """
        with open("default.yaml", "w") as f:
            print(DEFAULT, file=f)
        self.executeBobJenkinsCmd("set-options --download myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('BOB_DOWNLOAD_URL="http://localhost:8001/upload/' in send[0][1].decode('utf-8'))
        self.executeBobJenkinsCmd("set-options --reset --add-root test myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('BOB_DOWNLOAD_URL="http://localhost:8001/upload/' not in send[0][1].decode('utf-8'))
        self.executeBobJenkinsCmd("set-options --upload myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('BOB_UPLOAD_URL="http://localhost:8001/upload/' in send[0][1].decode('utf-8'))

    def testSetURL(self):
        self.newJenkinsMock = JenkinsMock()
        self.newJenkinsMock.start_mock_server(8081)

        self.executeBobJenkinsCmd("set-url myTestJenkins http://localhost:8081")
        self.executeBobJenkinsCmd("push -q myTestJenkins")

        send = self.jenkinsMock.getServerData()
        sendNew = self.newJenkinsMock.getServerData()
        assert(len(send) == 0)
        assert(len(sendNew) != 0)

    def testSetGitShallowClone(self):
        self.executeBobJenkinsCmd("set-options -o scm.git.shallow=42 myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        config = ElementTree.fromstring(send[0][1])
        for clone in config.iter('hudson.plugins.git.extensions.impl.CloneOption'):
            found = 0
            for a in clone.getiterator():
                if a.tag == 'shallow':
                    assert(a.text == 'true')
                    found += 1
                if a.tag == 'depth':
                    assert(a.text == '42')
                    found += 1
            assert(found == 2)
        self.executeBobJenkinsCmd("set-options -o scm.git.shallow=-1 myTestJenkins")
        with self.assertRaises(Exception) as c:
            self.executeBobJenkinsCmd("push -q myTestJenkins")
        assert(type(c.exception) == BuildError)

    def testSetPrefix(self):
        self.executeBobJenkinsCmd("set-options -p abcde- myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert(send[0][0] == '/createItem?name=abcde-test')
        assert(send[1][0] == '/job/test/doDelete')
        assert(send[2][0] == '/job/abcde-test/build')

    def testDelRoot(self):
        self.executeBobJenkinsCmd("set-options --del-root test myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert(send[0][0] == '/job/test/doDelete')
