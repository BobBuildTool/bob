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
from bob.state import BobState
from unittest.mock import patch

from mocks.jenkins_mock import JenkinsMock

from bob.cmds.jenkins import doJenkins
from bob.state import finalize

class TestJenkinsPush(TestCase):
    def executeBobJenkinsCmd(self, arg):
        doJenkins(arg.split(' '), self.cwd)

    def tearDown(self):
        self.jenkinsMock.stop_mock_server()
        finalize()
        os.chdir(self.oldCwd)
        removePath(self.cwd)

    def setUp(self):
        self.oldCwd = os.getcwd()
        self.jenkinsMock = JenkinsMock()
        self.jenkinsMock.start_mock_server()
        self.jenkinsMock.getServerData()
        self.cwd = tempfile.mkdtemp()
        os.chdir(self.cwd)

    def testSimplePush(self):
        RECIPE = """
root: True

checkoutSCM:
    -
      scm: git
      url: git@mytest.de
      branch: test

checkoutScript: |
    TestCheckoutScript

buildScript: |
    TestBuildScript

packageScript: |
    TestPackageScript
        """
        os.mkdir("recipes")
        with open(os.path.join("recipes", "test.yaml"), "w") as f:
            print(RECIPE, file=f)

        # do bob jenkins add
        self.executeBobJenkinsCmd("add myTestJenkins http://localhost:{} -r test"
                                    .format(self.jenkinsMock.getServerPort()))
        # throw away server data (but there shouldn't by any...
        assert(len(self.jenkinsMock.getServerData()) == 0)

        self.executeBobJenkinsCmd("push myTestJenkins -q")

        send = self.jenkinsMock.getServerData()
        assert(len(send) == 2)

        assert( 'createItem?name=test' in send[0][0])
        jobconfig = ElementTree.fromstring(send[0][1])
        self.assertEqual( jobconfig.tag, 'project' )

        # test GitSCM
        for scm in jobconfig.iter('scm'):
            if ('git' in scm.attrib.get('class')):
                assert ( 'git@mytest.de' in [ url.text for url in scm.iter('url') ])
            for branch in scm.iter('branches'):
                assert ( 'refs/heads/test' in [ name.text for name in branch.iter('name') ])

        found = False
        for cmd in jobconfig.iter('command'):
            if (('TestCheckoutScript' in cmd.text) and
                ('TestBuildScript' in cmd.text) and
                ('TestPackageScript' in cmd.text)):
                    found = True
        self.assertEqual(found, True)
        self.assertEqual('/job/test/build', send[1][0])

        self.executeBobJenkinsCmd("prune -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert(len(send) == 1)
        assert( '/job/test/doDelete'  ==  send[0][0])

        self.executeBobJenkinsCmd("rm myTestJenkins")
        assert(len(self.jenkinsMock.getServerData()) == 0)

    def testStableJobConfig(self):
        # This test generates the following jobs with it's dependencies:
        #        --> app1 -> lib-a
        # root -|--> app2 -> lib-b
        #        --> app3 -> lib-c
        # Afterwards the app* jobs are modified. In this case the lib-* jobs shouldn't
        # be modified or triggered.
        RECIPE_LIB="""
buildScript:
    echo 'hello bob'
multiPackage:
    a:
        packageScript: '1'
    b:
        packageScript: '2'
    c:
        packageScript: '3'
        """
        RECIPE_APP="""
depends:
    - {DEPENDS}

buildScript: |
    {SCRIPT}
        """
        ROOT_RECIPE="""
root: True
depends:
    - app1
    - app2
    - app3
buildScript: |
    true
        """

        os.mkdir("recipes")
        with open(os.path.join("recipes", "root.yaml"), "w") as f:
           print(ROOT_RECIPE, file=f)
        with open(os.path.join("recipes", "lib.yaml"), "w") as f:
           print(RECIPE_LIB, file=f)
        with open(os.path.join("recipes", "app1.yaml"), "w") as f:
           print(RECIPE_APP.format(SCRIPT='test', DEPENDS='lib-a'), file=f)
        with open(os.path.join("recipes", "app2.yaml"), "w") as f:
           print(RECIPE_APP.format(SCRIPT='test1', DEPENDS='lib-b'), file=f)
        with open(os.path.join("recipes", "app3.yaml"), "w") as f:
           print(RECIPE_APP.format(SCRIPT='test2', DEPENDS='lib-c'), file=f)

        # do bob jenkins add
        self.executeBobJenkinsCmd("add myTestJenkins http://localhost:{} -r root"
                                    .format(self.jenkinsMock.getServerPort()))

        # throw away server data (but there shouldn't by any...
        assert(len(self.jenkinsMock.getServerData()) == 0)

        self.executeBobJenkinsCmd("push -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        self.assertEqual(len(send), 10) # 5 jobs, create + schedule

        # bob will try to receive the old job config. Put it on the server...
        for data in send:
            created = re.match(r"/createItem\?name=(.*)$", data[0])
            if created:
                self.jenkinsMock.addServerData('/job/{}/config.xml'.format(created.group(1)), data[1])
                if 'lib' in created.group(1):
                    oldTestConfig = data[1]

        testrun = 0
        while testrun < 3:
            testrun += 1

            with open(os.path.join("recipes", "app{}.yaml".format(testrun)), "w") as f:
                print(RECIPE_APP.format(DEPENDS='lib-{}'.format(chr(ord('a')-1+testrun)),
                    SCRIPT='test_'+str(testrun)), file=f)

            self.executeBobJenkinsCmd("push myTestJenkins -q")

            send = self.jenkinsMock.getServerData()
            # one of the app's were changed.
            # jenkins has to reconfigure the app  and the root job but not the lib job
            configsChanged = 0
            for data in send:
                if 'job' in data[0] and 'config.xml' in data[0]:
                    configsChanged +=1

            assert(configsChanged == 2)

        self.executeBobJenkinsCmd("prune -q myTestJenkins")
        send = self.jenkinsMock.getServerData()
        assert(len(send) == 5) # deleted 5 Jobs

        self.executeBobJenkinsCmd("rm myTestJenkins")
        assert(len(self.jenkinsMock.getServerData()) == 0)
