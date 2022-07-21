from unittest import TestCase
from mocks.jenkins_tests import JenkinsTests

class JenkinsPrune(JenkinsTests, TestCase):

    def testPruneUnknown(self):
        """Prune unkown Jenkins fails gracefully"""
        with self.assertRaises(SystemExit) as ex:
            self.executeBobJenkinsCmd("prune doesnotexist")
        self.assertEqual(ex.exception.code, 1)

    def testPruneAll(self):
        """Prune with default config deletes all jobs"""
        self.writeRecipe("root", """\
            root: True
            packageScript: "true"
            """)
        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.executeBobJenkinsCmd("prune test")
        self.assertFalse(self.jenkinsMock.getJobExists("root"))

    def testPruneObsolete(self):
        """With --obsolete only Jobs that are disabled are removed.

        Implicitly tests that --keep does not delete jobs by its own.
        """

        self.writeRecipe("root", """\
            root: True
            depends:
                - lib
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("lib", "")

        self.executeBobJenkinsCmd("set-options test --keep")
        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("lib"))

        self.writeRecipe("root", """\
            root: True
            packageScript: "true"
            """)
        self.executeBobJenkinsCmd("push test")
        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertTrue(self.jenkinsMock.getJobEnabled("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib"))
        self.assertFalse(self.jenkinsMock.getJobEnabled("lib"))

        self.executeBobJenkinsCmd("prune test --obsolete")
        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertFalse(self.jenkinsMock.getJobExists("lib"))

    def testPruneIntermediate(self):
        """Prune --intermediate deletes everything except root jobs"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - lib1
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("lib1", """\
            depends:
                - lib2
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("lib2", "")
        self.executeBobJenkinsCmd("push test")

        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib1"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib2"))

        self.executeBobJenkinsCmd("prune test --intermediate")

        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertFalse(self.jenkinsMock.getJobExists("lib1"))
        self.assertFalse(self.jenkinsMock.getJobExists("lib2"))


class JenkinsRemove(JenkinsTests, TestCase):

    def setUp(self):
        super().setUp()
        try:
            self.writeRecipe("root", """\
                root: True
                depends:
                    - lib1
                buildScript: "true"
                packageScript: "true"
                """)
            self.writeRecipe("lib1", """\
                depends:
                    - lib2
                buildScript: "true"
                packageScript: "true"
                """)
            self.writeRecipe("lib2", "")
        except:
            super().tearDown()
            raise

    def testRemoveUnknown(self):
        """Remove unkown Jenkins fails gracefully"""
        with self.assertRaises(SystemExit) as ex:
            self.executeBobJenkinsCmd("rm doesnotexist")
        self.assertEqual(ex.exception.code, 1)

    def testRemoveEmpty(self):
        """Remove without push is ok"""
        self.executeBobJenkinsCmd("rm test")

    def testRemovePopulated(self):
        """Remove with jobs is denied"""
        self.executeBobJenkinsCmd("push test")
        with self.assertRaises(SystemExit) as ex:
            self.executeBobJenkinsCmd("rm test")
        self.assertEqual(ex.exception.code, 1)

        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib1"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib2"))

    def testRemovePopulatedForce(self):
        """Remove with jobs can be forced but jobs are not deleted"""
        self.executeBobJenkinsCmd("push test")
        self.executeBobJenkinsCmd("rm test -f")

        self.assertTrue(self.jenkinsMock.getJobExists("root"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib1"))
        self.assertTrue(self.jenkinsMock.getJobExists("lib2"))
