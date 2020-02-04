# Bob build tool
# Copyright (C) 2016 BobBuildToolTeam
#
# SPDX-License-Identifier: GPL-3.0-or-later

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

    def tearDown(self):
        self.jenkinsMock.stop_mock_server()
        finalize()
        os.chdir(self.oldCwd)
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
        self.oldCwd = os.getcwd()
        self.cwd = tempfile.mkdtemp()
        self.jenkinsMock = JenkinsMock()
        self.jenkinsMock.start_mock_server()
        self.jenkinsMock.getServerData()
        os.chdir(self.cwd)
        os.mkdir("recipes")
        with open(os.path.join("recipes", "test.yaml"), "w") as f:
            print(RECIPE, file=f)

        # do bob jenkins add
        self.executeBobJenkinsCmd("add myTestJenkins http://localhost:{} -r test"
                                    .format(self.jenkinsMock.getServerPort()))
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        self.jenkinsMock.getServerData()

    def createComplexRecipes(self):
        ROOT = """
root: True

depends:
    - dependency-one
    - dependency-two

checkoutSCM:
    -
      scm: git
      url: git@mytest.de/root.git
      branch: test

buildScript: |
    echo 'build'
packageScript: |
    echo 'package'
        """

        DEPENDENCYONE = """
depends:
    - dependency-two

checkoutSCM:
    -
      scm: git
      url: git@mytest.de/dependency-one.git
      branch: test

buildScript: |
    echo 'build'
packageScript: |
    echo 'package'
        """

        DEPENDENCYTWO = """
checkoutSCM:
    -
      scm: git
      url: git@mytest.de/dependency-two.git
      branch: test

buildScript: |
    echo 'build'
packageScript: |
    echo 'package'
        """

        with open(os.path.join("recipes", "root.yaml"), "w") as f:
            print(ROOT, file=f)
        with open(os.path.join("recipes", "dependency-one.yaml"), "w") as f:
            print(DEPENDENCYONE, file=f)
        with open(os.path.join("recipes", "dependency-two.yaml"), "w") as f:
            print(DEPENDENCYTWO, file=f)

        self.executeBobJenkinsCmd("add myTestJenkinsComplex http://localhost:{} -r root"
                                    .format(self.jenkinsMock.getServerPort()))

    def testSetNode(self):
        self.executeBobJenkinsCmd("set-options -n testSlave myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('<assignedNode>testSlave</assignedNode>' in send[0][1].decode('utf-8'))

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
        assert('BOB_DOWNLOAD_URL=http://localhost:8001/upload' in send[0][1].decode('utf-8'))
        self.executeBobJenkinsCmd("set-options --reset --add-root test myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('BOB_DOWNLOAD_URL=http://localhost:8001/upload' not in send[0][1].decode('utf-8'))
        self.executeBobJenkinsCmd("set-options --upload myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert('BOB_UPLOAD_URL=http://localhost:8001/upload' in send[0][1].decode('utf-8'))

    def testSetURL(self):
        self.newJenkinsMock = JenkinsMock()
        self.newJenkinsMock.start_mock_server()

        self.executeBobJenkinsCmd("set-url myTestJenkins http://localhost:{}"
                                    .format(self.newJenkinsMock.getServerPort()))
        self.executeBobJenkinsCmd("push -q myTestJenkins")

        send = self.jenkinsMock.getServerData()
        sendNew = self.newJenkinsMock.getServerData()
        assert(len(send) == 0)
        assert(len(sendNew) != 0)
        self.newJenkinsMock.stop_mock_server()

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

    def testSetGitTimeoutClone(self):
        self.executeBobJenkinsCmd("set-options -o scm.git.timeout=42 myTestJenkins")
        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        config = ElementTree.fromstring(send[0][1])
        for clone in config.iter('hudson.plugins.git.extensions.impl.CloneOption'):
            found = 0
            for a in clone.getiterator():
                if a.tag == 'timeout':
                    assert(a.text == '42')
                    found += 1
            assert(found == 1)
        self.executeBobJenkinsCmd("set-options -o scm.git.timeout=-10 myTestJenkins")
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

    def testShortDescription(self):
        self.createComplexRecipes()
        self.executeBobJenkinsCmd("set-options --shortdescription myTestJenkinsComplex")
        self.executeBobJenkinsCmd("push -q myTestJenkinsComplex")
        send = self.jenkinsMock.getServerData()
        result_set = set()
        try:
            for i in send:
                if i[0] == '/createItem?name=dependency-two':
                    for items in ElementTree.fromstring(i[1]).iter('description'):

                        for line in [x for x in items.itertext()][0].splitlines():
                            if line.startswith('<li>') and line.endswith('</li>'):
                                result_set.add(line[4:-5])
        except:
            print("Malformed Data Recieved")

        self.assertEqual(result_set, {'root/dependency-one/dependency-two'})

    def testLongDescription(self):

        self.createComplexRecipes()
        self.executeBobJenkinsCmd("set-options --longdescription myTestJenkinsComplex")
        self.executeBobJenkinsCmd("push -q myTestJenkinsComplex")
        send = self.jenkinsMock.getServerData()
        result_set = set()
        try:
            for i in send:
                if i[0] == '/createItem?name=dependency-two':
                    for items in ElementTree.fromstring(i[1]).iter('description'):

                        for line in [x for x in items.itertext()][0].splitlines():
                            if line.startswith('<li>') and line.endswith('</li>'):
                                result_set.add(line[4:-5])
        except:
            print("Malformed Data Recieved")

        self.assertEqual(result_set, {'root/dependency-two', 'root/dependency-one/dependency-two'})
