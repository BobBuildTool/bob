# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase, skipIf
from unittest.mock import MagicMock
import os
import subprocess
import sys
import tempfile

from bob.input import SvnScm
from bob.invoker import Invoker, InvocationError
from bob.utils import isMsys, runInEventLoop

if sys.platform == "win32":
    def makeUrl(path):
        return 'file:///' + path.replace("\\", "/")
else:
    def makeUrl(path):
        return 'file://' + path

def createSvnRepo(root, name, content):
    repodir = os.path.join(root, name)
    subprocess.check_call(['svnadmin', 'create', name], cwd=root)
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "test.txt"), "w") as f:
            f.write(content)
        subprocess.check_call(['svn', 'import', tmp, makeUrl(repodir) + '/trunk',
            '-m', "Initial Import"], cwd=tempfile.gettempdir())
    return makeUrl(repodir) + '/trunk'


@skipIf(isMsys(), "svnadmin fails on MSYS")
class TestSvnScmMirrors(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir_root = tempfile.TemporaryDirectory()
        root = cls.__repodir_root.name
        cls.mainUrl = createSvnRepo(root, "main", "main")
        cls.mirrorUrl = createSvnRepo(root, "mirror", "mirror")
        cls.invalidUrl = makeUrl(os.path.join(root, "does-not-exist")) + '/trunk'

    @classmethod
    def tearDownClass(cls):
        cls.__repodir_root.cleanup()

    def createSvnScm(self, spec={}, preMirrors=[], fallbackMirrors=[]):
        s = {
            'scm' : 'svn',
            'url' : self.mainUrl,
            'revision' : 1,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return SvnScm(s, preMirrors=preMirrors, fallbackMirrors=fallbackMirrors)

    def invokeScm(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, True, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker, False))

    def assertContent(self, workspace, content):
        with open(os.path.join(workspace, "test.txt")) as f:
            self.assertEqual(f.read(), content)

    def testPlainCheckout(self):
        """Sanity check: a plain checkout still works"""
        scm = self.createSvnScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "main")

    def testPreMirrorUsed(self):
        """A pre-mirror is used before the primary URL"""
        scm = self.createSvnScm(
            { 'url' : self.invalidUrl },
            preMirrors=[{ 'scm' : 'svn', 'url' : r".+", 'mirror' : self.mirrorUrl }])
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "mirror")

    def testFallbackMirrorUsed(self):
        """A fallback mirror is used if the primary URL fails"""
        scm = self.createSvnScm(
            { 'url' : self.invalidUrl },
            fallbackMirrors=[{ 'scm' : 'svn', 'url' : r".+", 'mirror' : self.mirrorUrl }])
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "mirror")

    def testGracefulMirrorFallback(self):
        """A failing mirror is ignored and the next candidate is used"""
        scm = self.createSvnScm(
            { 'url' : self.invalidUrl },
            preMirrors=[
                { 'scm' : 'svn', 'url' : r".+", 'mirror' : self.invalidUrl },
                { 'scm' : 'svn', 'url' : r".+", 'mirror' : self.mirrorUrl },
            ])
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "mirror")

    def testAllCandidatesFail(self):
        """If no candidate works the checkout fails"""
        scm = self.createSvnScm(
            { 'url' : self.invalidUrl },
            fallbackMirrors=[{ 'scm' : 'svn', 'url' : r".+", 'mirror' : self.invalidUrl }])
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)

    def testOtherScmMirrorIgnored(self):
        """Mirrors of other SCM types are not applied to svn checkouts"""
        scm = self.createSvnScm(
            { 'url' : self.invalidUrl },
            preMirrors=[{ 'scm' : 'url', 'url' : r".+", 'mirror' : self.mirrorUrl }])
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, scm)

    def testMirrorKeptOnUpdate(self):
        """A chosen mirror is kept across invocations"""
        scm = self.createSvnScm(
            preMirrors=[{ 'scm' : 'svn', 'url' : r".+", 'mirror' : self.mirrorUrl }])
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "mirror")

            # Update without mirror. The existing checkout must stay on the
            # mirror even though the primary URL is available.
            scm = self.createSvnScm()
            self.invokeScm(workspace, scm)
            self.assertContent(workspace, "mirror")
            info = subprocess.check_output(['svn', 'info', '--show-item', 'url'],
                cwd=workspace, universal_newlines=True)
            self.assertEqual(info.strip(), self.mirrorUrl)
