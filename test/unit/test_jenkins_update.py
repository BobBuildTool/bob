from mocks.jenkins_tests import JenkinsTests
from unittest import TestCase
import time
import xml.etree.ElementTree as ET
import subprocess

def clearDescription(x):
    x = ET.fromstring(x)
    x.find('./description').text = ""
    return ET.tostring(x, encoding="UTF-8")

class JenkinsUpdates(JenkinsTests, TestCase):

    def setUp(self):
        super().setUp()
        try:
            self.writeRecipe("root", """\
                root: True
                depends: [ "lib1" ]
                buildScript: "true"
                packageScript: "true"
                """)
            self.writeRecipe("lib1", """\
                depends: [ "lib2" ]
                buildScript: "true"
                packageScript: "true"
                """)
            self.writeRecipe("lib2", """\
                packageScript: "true"
                """)

            # Make sure we have some recipe audit record too
            subprocess.run("git init .", shell=True, check=True)
            subprocess.run("git config user.email bob@bob.bob", shell=True, check=True)
            subprocess.run("git config user.name test", shell=True, check=True)
            subprocess.run("git add recipes", shell=True, check=True)
            subprocess.run("git commit -m import", shell=True, check=True)
        except:
            super().tearDown()
            raise

    def testAlways(self):
        """Normally every push updates the description"""
        self.executeBobJenkinsCmd("set-options test -o jobs.update=always")

        self.executeBobJenkinsCmd("push test")
        x1 = self.jenkinsMock.getJobConfig("root")
        x2 = self.jenkinsMock.getJobConfig("lib1")
        x3 = self.jenkinsMock.getJobConfig("lib2")

        time.sleep(1)

        self.executeBobJenkinsCmd("push test")
        self.assertNotEqual(x1, self.jenkinsMock.getJobConfig("root"))
        self.assertNotEqual(x2, self.jenkinsMock.getJobConfig("lib1"))
        self.assertNotEqual(x3, self.jenkinsMock.getJobConfig("lib2"))

    def testDescription(self):
        """Mode "description" always updates description but leaves rest
           unchanged unless necessary."""

        self.executeBobJenkinsCmd("set-options test -o jobs.update=description")

        self.executeBobJenkinsCmd("push test")
        x11 = self.jenkinsMock.getJobConfig("root")
        x21 = self.jenkinsMock.getJobConfig("lib1")
        x31 = self.jenkinsMock.getJobConfig("lib2")

        time.sleep(1)

        self.executeBobJenkinsCmd("push test")
        x12 = self.jenkinsMock.getJobConfig("root")
        x22 = self.jenkinsMock.getJobConfig("lib1")
        x32 = self.jenkinsMock.getJobConfig("lib2")

        self.assertNotEqual(x11, x12)
        self.assertNotEqual(x21, x22)
        self.assertNotEqual(x31, x32)

        self.assertEqual(clearDescription(x11), clearDescription(x12))
        self.assertEqual(clearDescription(x21), clearDescription(x22))
        self.assertEqual(clearDescription(x31), clearDescription(x32))

    def testLazy(self):
        """Lazy updates only update if something different is built"""
        self.executeBobJenkinsCmd("set-options test -o jobs.update=lazy")

        self.executeBobJenkinsCmd("push test")
        x1 = self.jenkinsMock.getJobConfig("root")
        x2 = self.jenkinsMock.getJobConfig("lib1")
        x3 = self.jenkinsMock.getJobConfig("lib2")

        time.sleep(1)

        self.executeBobJenkinsCmd("push test")
        self.assertEqual(x1, self.jenkinsMock.getJobConfig("root"))
        self.assertEqual(x2, self.jenkinsMock.getJobConfig("lib1"))
        self.assertEqual(x3, self.jenkinsMock.getJobConfig("lib2"))

        self.writeRecipe("lib1", """\
            depends: [ "lib2" ]
            buildScript: "somethingelse"
            packageScript: "true"
            """)
        subprocess.run("git commit -a -m update", shell=True, check=True)

        self.executeBobJenkinsCmd("push test")
        self.assertNotEqual(x1, self.jenkinsMock.getJobConfig("root"))
        self.assertNotEqual(x2, self.jenkinsMock.getJobConfig("lib1"))
        self.assertEqual(x3, self.jenkinsMock.getJobConfig("lib2"))

    def testInvalid(self):
        """Invalid update mode is rejected"""
        with self.assertRaises(SystemExit):
            self.executeBobJenkinsCmd("set-options test -o jobs.update=invalid")

    def testPruneReenable(self):
        """Disabled job is re-enabled if appers again"""

        self.executeBobJenkinsCmd("set-options test --keep")
        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobEnabled("root"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("lib1"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("lib2"))

        self.writeRecipe("root", """\
            root: True
            packageScript: "true"
            """)

        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobEnabled("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib1"))
        self.assertFalse(self.jenkinsMock.getJobEnabled("lib1"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib1"))
        self.assertFalse(self.jenkinsMock.getJobEnabled("lib2"))

        self.writeRecipe("root", """\
            root: True
            depends: [ "lib1" ]
            buildScript: "true"
            packageScript: "true"
            """)

        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobEnabled("root"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("lib1"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("lib2"))


class JenkinsJobsIsolate(JenkinsTests, TestCase):

    def setUp(self):
        super().setUp()
        try:
            self.writeRecipe("root", """\
                root: True
                depends: ["lib-foo", "lib-bar", "lib-baz"]
                buildScript: "true"
                packageScript: "true"
                """)
            self.writeRecipe("lib", """\
                multiPackage:
                    foo:
                        packageScript: "foo"
                    bar:
                        packageScript: "bar"
                    baz:
                        packageScript: "baz"
                """)
        except:
            super().tearDown()
            raise

    def testIsolateNone(self):
        """Normally all multiPackage's end up in one job"""
        self.executeBobJenkinsCmd("push test")
        self.assertEqual(sorted(self.jenkinsMock.getJobs()), ["lib", "root"])

    def testIsolateSingle(self):
        """Isolating one package extracts only this one"""
        self.executeBobJenkinsCmd("set-options test -o jobs.isolate=lib-bar")
        self.executeBobJenkinsCmd("push test")
        self.assertEqual(sorted(self.jenkinsMock.getJobs()),
                         ["lib", "lib-bar", "root"])

    def testIsolateMany(self):
        self.executeBobJenkinsCmd("set-options test -o jobs.isolate=lib-.*")
        self.executeBobJenkinsCmd("push test")
        self.assertEqual(sorted(self.jenkinsMock.getJobs()),
                         ["lib-bar", "lib-baz", "lib-foo", "root"])
