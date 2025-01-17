from mocks.jenkins_tests import JenkinsTests
from shlex import quote
from unittest import TestCase, expectedFailure, skipUnless
import os, os.path
import tempfile
import subprocess
import sys

from bob.utils import removePath

class JenkinsBuilds(JenkinsTests):
    OPTIONS = ""

    def setUp(self):
        super().setUp()
        try:
            self.executeBobJenkinsCmd("set-options test " + self.OPTIONS)
        except:
            self.tearDown()
            raise

    def testSimpleBuild(self):
        """Test simple recipe"""
        self.writeRecipe("root", """\
            root: True
            packageScript: |
                echo testSimpleBuild > result.txt
            """)

        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "testSimpleBuild\n")

    def testIdenticalRebuild(self):
        """Rebuild job without changes"""
        self.writeRecipe("root", """\
            root: True
            packageScript: |
                echo testSimpleBuild > result.txt
            """)

        self.executeBobJenkinsCmd("push test")

        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "testSimpleBuild\n")

        self.jenkinsMock.run(["root"])
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "testSimpleBuild\n")

    def testTwoJobs(self):
        """Build two dependent jobs"""
        self.writeRecipe("root", """\
            root: True
            depends: [ "lib" ]
            buildScript: |
                cp $2/result.txt .
            packageScript: |
                cp $1/result.txt .
                echo root >> result.txt
            """)
        self.writeRecipe("lib", """\
            packageScript: |
                echo lib > result.txt
            """)

        self.executeBobJenkinsCmd("push test")

        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "lib\nroot\n")

    def testDependencyJobUpdate(self):
        """Update of dependency triggers upstream job build"""
        self.writeRecipe("root", """\
            root: True
            depends: [ "lib" ]
            buildScript: |
                cp $2/result.txt .
            packageScript: |
                cp $1/result.txt .
                echo root >> result.txt
            """)
        self.writeRecipe("lib", """\
            packageScript: |
                echo lib > result.txt
            """)

        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "lib\nroot\n")

        self.writeRecipe("lib", """\
            packageScript: |
                echo lib-update > result.txt
            """)
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "lib-update\nroot\n")

    # TODO: Git job

class JenkinsBuildsJduc(JenkinsBuilds, TestCase):
    pass

class JenkinsBuildsJDuc(JenkinsBuilds, TestCase):
    OPTIONS = "--download"

class JenkinsBuildsJdUc(JenkinsBuilds, TestCase):
    OPTIONS = "--upload"

class JenkinsBuildsJDUc(JenkinsBuilds, TestCase):
    OPTIONS = "--download --upload"

class JenkinsBuildsJduC(JenkinsBuilds, TestCase):
    OPTIONS = "--clean"

class JenkinsBuildsJDuC(JenkinsBuilds, TestCase):
    OPTIONS = "--clean --download"

class JenkinsBuildsJdUC(JenkinsBuilds, TestCase):
    OPTIONS = "--clean --upload"

class JenkinsBuildsJDUC(JenkinsBuilds, TestCase):
    OPTIONS = "--clean --download --upload"

class JenkinsBuildsADUc(JenkinsBuilds, TestCase):
    OPTIONS = "-o artifacts.copy=archive --upload --download"

class JenkinsBuildsADUC(JenkinsBuilds, TestCase):
    OPTIONS = "-o artifacts.copy=archive --upload --download --clean"


class JenkinsCleanIncremental(JenkinsTests, TestCase):
    """Make sure --incremental and --clean do what they should"""

    def setUp(self):
        super().setUp()
        try:
            self.writeRecipe("root", """\
                root: True
                checkoutDeterministic: false
                checkoutScript: |
                    if [ -e result.txt ] ; then
                        read -r COUNTER < result.txt
                    else
                        COUNTER=0
                    fi
                    : $(( COUNTER++ ))
                    echo "$COUNTER" > result.txt
                buildScript: cp $1/result.txt .
                packageScript: cp $1/result.txt .
                """)
        except:
            super().tearDown()
            raise

    def testClean(self):
        """Clean builds always wipe workspace"""
        self.executeBobJenkinsCmd("set-options test --clean")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "1\n")

        self.jenkinsMock.run(["root"])
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "1\n")

    def testIncremental(self):
        """Incremental builds retain workspace"""
        self.executeBobJenkinsCmd("set-options test --incremental")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "1\n")

        self.jenkinsMock.run(["root"])
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "2\n")


class JenkinsAuditExtra(JenkinsTests, TestCase):

    def testAuditMeta(self):
        """Verify that audit.meta.* is honored"""
        self.writeRecipe("root", """\
            root: True
            packageScript: "true"
            """)
        self.executeBobJenkinsCmd("set-options test -o audit.meta.FOO=bar")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        audit = self.getJobAudit("root")
        self.assertEqual(audit["artifact"]["meta"].get("FOO"), "bar")

    def testRecipeAudit(self):
        """Recipe audit data is forwarded to audit trail"""
        self.writeRecipe("root", """\
            root: True
            packageScript: "true"
            """)

        subprocess.run("git init .", shell=True, check=True)
        subprocess.run("git config user.email bob@bob.bob", shell=True, check=True)
        subprocess.run("git config user.name test", shell=True, check=True)
        subprocess.run("git add *", shell=True, check=True)
        subprocess.run("git commit -m import", shell=True, check=True)
        subprocess.run("git tag -a -m tagged tagged", shell=True, check=True)

        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        audit = self.getJobAudit("root")
        self.assertEqual(audit["artifact"]["recipes"]["description"], "tagged",
                         "Description equals the git tag")


class JenkinsSharedPackage(JenkinsTests, TestCase):
    """Verify handling of shared packages"""

    def findSharedRecord(self, audit):
        for i in audit['references']:
            if i['meta']['step'] != 'dist': continue
            if i['meta']['recipe'] != 'shared': continue
            return i
        self.fail("audit record not found")

    def testBuildShared(self):
        """Build two projects using a common shared package"""
        self.writeRecipe("root", """\
            root: True
            depends: [ "shared" ]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("shared", """\
            shared: True
            packageScript: "true"
            """)

        self.executeBobJenkinsCmd("set-options test -p test-")
        self.executeBobJenkinsCmd("add try http://localhost:{} -r root -p try-"
                                    .format(self.jenkinsMock.getServerPort()))

        self.executeBobJenkinsCmd("push test")
        self.executeBobJenkinsCmd("push try")
        self.jenkinsMock.run()

        self.assertTrue(os.path.isdir(
            os.path.join(self.jenkinsMock.getJenkinsHome(), "bob")))

        # They must have used the build of the "shared" package
        self.assertEqual(self.findSharedRecord(self.getJobAudit("test-root")),
                         self.findSharedRecord(self.getJobAudit("try-root")))

    def testRebuildShared(self):
        """Rebuild will use shared location"""
        self.writeRecipe("root", """\
            root: True
            shared: True
            packageScript: echo shared > result.txt
            """)

        self.executeBobJenkinsCmd("set-options test --clean")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        firstAudit = self.getJobAudit("root")
        firstBuild = self.jenkinsMock.getJobBuildNumber("root")

        self.jenkinsMock.run(['root'])
        secondAudit = self.getJobAudit("root")
        secondBuild = self.jenkinsMock.getJobBuildNumber("root")

        self.assertEqual(firstAudit, secondAudit)
        self.assertNotEqual(firstBuild, secondBuild)

    def testRebuildSharedDeletedClean(self):
        """Deleting shared location does not fail build"""
        self.writeRecipe("root", """\
            root: True
            shared: True
            packageScript: echo shared > result.txt
            """)

        self.executeBobJenkinsCmd("set-options test --clean")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        firstAudit = self.getJobAudit("root")
        firstBuild = self.jenkinsMock.getJobBuildNumber("root")

        sharedDir = os.path.join(self.jenkinsMock.getJenkinsHome(), "bob")
        self.assertTrue(os.path.isdir(sharedDir))
        removePath(sharedDir)

        self.jenkinsMock.run(['root'])
        secondAudit = self.getJobAudit("root")
        secondBuild = self.jenkinsMock.getJobBuildNumber("root")

        self.assertTrue(os.path.isdir(sharedDir))
        self.assertNotEqual(firstAudit, secondAudit)
        self.assertNotEqual(firstBuild, secondBuild)

    def testRebuildSharedDeletedIncremental(self):
        """Deleting shared location does not fail build"""
        self.writeRecipe("root", """\
            root: True
            shared: True
            packageScript: echo shared > result.txt
            """)

        self.executeBobJenkinsCmd("set-options test --incremental")
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        firstAudit = self.getJobAudit("root")
        firstBuild = self.jenkinsMock.getJobBuildNumber("root")

        sharedDir = os.path.join(self.jenkinsMock.getJenkinsHome(), "bob")
        self.assertTrue(os.path.isdir(sharedDir))
        removePath(sharedDir)

        self.jenkinsMock.run(['root'])
        secondAudit = self.getJobAudit("root")
        secondBuild = self.jenkinsMock.getJobBuildNumber("root")

        self.assertTrue(os.path.isdir(sharedDir))
        self.assertNotEqual(firstAudit, secondAudit)
        self.assertNotEqual(firstBuild, secondBuild)

    def testCustomSharedLocation(self):
        """Custom shared locations can be set"""
        self.writeRecipe("root", """\
            root: True
            shared: True
            packageScript: echo shared > result.txt
            """)

        with tempfile.TemporaryDirectory() as tmp:
            # Mind that the backslash needs to be escaped
            s = os.path.join(tmp, "canary").replace("\\", "\\\\")
            self.executeBobJenkinsCmd("set-options test -o shared.dir=" + quote(s))
            self.executeBobJenkinsCmd("push test")
            self.assertFalse(os.path.isdir(s))
            self.jenkinsMock.run()
            self.assertTrue(os.path.isdir(s))
        
# Build with tool

# Build with sandbox

class JenkinsSandboxBuilds(JenkinsTests):
    OPTIONS = "--no-sandbox"
    OUTSIDE_ISOLATED = 0
    OUTSIDE_STABLE_PATH = 0
    INSIDE_ISOLATED = 0
    INSIDE_IMAGE_USED = 0
    INSIDE_STABLE_PATH = 0

    def testBuild(self):
        """Test sandbox build"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - test-outside
                - name: sandbox
                  use: [sandbox]
                  forward: True
                - test-inside

            buildScript: "true"
            packageScript: |
                echo ok > result.txt
            """)
        self.writeRecipe("sandbox", """\
            packageScript: |
                echo "canary" > canary.txt
            provideSandbox:
                paths: ["/usr/local/bin", "/usr/local/sbin", "/usr/bin", "/usr/sbin",
                        "/bin", "/sbin"]
                mount:
                    - /bin
                    - /etc
                    - /lib
                    - /run
                    - /usr
                    - /var
                    - ["/lib32", "/lib32", [nofail]]
                    - ["/lib64", "/lib64", [nofail]]
            """)

        # The canary needs to be somewhere in the project path. The slim sandbox
        # mode only restricts those paths...
        CANARY = os.path.join(self.cwd, "config.yaml")
        self.writeRecipe("test", f"""\
            packageScript: |
                verifyIsolated()
                {{
                    if [[ $1 -ne 0 ]] ; then
                        if [[ -e "{CANARY}" ]] ; then
                            echo "{CANARY} exists in isolated environment" >&2
                            exit 1
                        fi
                    else
                        if [[ ! -e "{CANARY}" ]] ; then
                            echo "{CANARY} does not exists in host environment" >&2
                            exit 1
                        fi
                    fi
                }}

                verifyImageUsed()
                {{
                    if [[ $1 -ne 0 ]] ; then
                        if [[ ! -e /canary.txt ]] ; then
                            echo "Sandbox image not used?" >&2
                            exit 1
                        fi
                    else
                        if [[ -e /canary.txt ]] ; then
                            echo "Canary found in host environment" >&2
                            exit 1
                        fi
                    fi
                }}

                verifyStablePath()
                {{
                    if [[ $1 -ne 0 ]] ; then
                        if [[ $PWD != /bob/* ]] ; then
                            echo "No stable path inside sandbox" >&2
                            exit 1
                        fi
                    else
                        if [[ $PWD == /bob/* ]] ; then
                            echo "Stable path used in host environment" >&2
                            exit 1
                        fi
                    fi
                }}

            multiPackage:
                outside:
                    packageScript: |
                        verifyIsolated {self.OUTSIDE_ISOLATED}
                        verifyImageUsed 0
                        verifyStablePath {self.OUTSIDE_STABLE_PATH}

                inside:
                    packageScript: |
                        verifyIsolated {self.INSIDE_ISOLATED}
                        verifyImageUsed {self.INSIDE_IMAGE_USED}
                        verifyStablePath {self.INSIDE_STABLE_PATH}
            """)
        self.executeBobJenkinsCmd("set-options test " + self.OPTIONS)
        self.executeBobJenkinsCmd("push test")
        self.jenkinsMock.run()
        with self.getJobResult("root") as d:
            with open(os.path.join(d, "result.txt")) as f:
                self.assertEqual(f.read(), "ok\n")

@skipUnless(sys.platform == "linux", "Sandbox requires Linux")
class JenkinsSandboxBuildDisabled(JenkinsSandboxBuilds, TestCase):
    pass

@skipUnless(sys.platform == "linux", "Sandbox requires Linux")
class JenkinsSandboxBuildPartial(JenkinsSandboxBuilds, TestCase):
    OPTIONS = "--sandbox"
    OUTSIDE_ISOLATED=0
    OUTSIDE_STABLE_PATH=0
    INSIDE_ISOLATED=1
    INSIDE_STABLE_PATH=1
    INSIDE_IMAGE_USED=1

@skipUnless(sys.platform == "linux", "Sandbox requires Linux")
class JenkinsSandboxBuildSlim(JenkinsSandboxBuilds, TestCase):
    OPTIONS = "--slim-sandbox"
    OUTSIDE_ISOLATED=1
    OUTSIDE_STABLE_PATH=0
    INSIDE_ISOLATED=1
    INSIDE_STABLE_PATH=0
    INSIDE_IMAGE_USED=0

@skipUnless(sys.platform == "linux", "Sandbox requires Linux")
class JenkinsSandboxBuildDev(JenkinsSandboxBuilds, TestCase):
    OPTIONS = "--dev-sandbox"
    OUTSIDE_ISOLATED=1
    OUTSIDE_STABLE_PATH=0
    INSIDE_ISOLATED=1
    INSIDE_STABLE_PATH=0
    INSIDE_IMAGE_USED=1

@skipUnless(sys.platform == "linux", "Sandbox requires Linux")
class JenkinsSandboxBuildStrict(JenkinsSandboxBuilds, TestCase):
    OPTIONS = "--strict-sandbox"
    OUTSIDE_ISOLATED=1
    OUTSIDE_STABLE_PATH=1
    INSIDE_ISOLATED=1
    INSIDE_STABLE_PATH=1
    INSIDE_IMAGE_USED=1

# Smoke tests:
# - Set a node
# - Set jobs.policy
