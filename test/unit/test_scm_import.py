# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import MagicMock, patch
import asyncio
import os
import schema
import tempfile

from bob.errors import BuildError, ParseError
from bob.scm.imp import ImportScm, ImportAudit
from bob.invoker import Invoker, InvocationError
from bob.utils import asHexStr, hashDirectory, runInEventLoop

class DummyPackage:
    def getName(self):
        return "dummy"
    def getStack(self):
        return [ "a", "b" ]

class DummyStep:
    def getPackage(self):
        return DummyPackage()


class TestImportScm(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.url = os.path.abspath(cls.__repodir.name)

        cls.fn = os.path.join(cls.url, "test.txt")
        with open(cls.fn, "w") as f:
            f.write("Hello world!")
        os.symlink("test.txt", os.path.join(cls.url, "link.txt"))

        os.mkdir(os.path.join(cls.url, "sub"))
        with open(os.path.join(cls.url, "sub", "sub.txt"), "w") as f:
            f.write("Nested")

        cls.digest = hashDirectory(cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def createImportScm(self, spec = {}, **kwargs):
        s = {
            'scm' : 'import',
            'url' : self.url,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return ImportScm(s, **kwargs)

    def invokeScm(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker, False))

    def testProperties(self):
        """Query some static proerties of SCM"""
        s = self.createImportScm({"dir" : "subdir"})
        p = s.getProperties(False)
        self.assertEqual(p["scm"], "import")
        self.assertEqual(p["url"], self.url)

        self.assertEqual(s.asDigestScript(), self.url)
        self.assertEqual(s.getDirectory(), "subdir")
        self.assertEqual(s.isDeterministic(), False)
        self.assertEqual(s.hasLiveBuildId(), True)

    def testLiveBuildId(self):
        """Test prediction and calculation of live-build-id"""
        s = self.createImportScm()
        self.assertEqual(runInEventLoop(s.predictLiveBuildId(DummyStep())), self.digest)

        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, s.calcLiveBuildId(workspace))

    def testCopy(self):
        """Test straigt forward 'checkout'"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testCopyViaProperties(self):
        """Test Jenkins-like 'checkout' via properties"""
        s = ImportScm(self.createImportScm().getProperties(True))
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testObstruct(self):
        """Test that obstructed destination gracefully fails"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            os.mkdir(os.path.join(workspace, "test.txt"))
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, s)

    def testUpdate(self):
        """Test that updates of sources are copied"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            with open(self.fn, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            with open(os.path.join(workspace, "test.txt")) as f:
                self.assertEqual(f.read(), "Changed")

    def testNotUpdated(self):
        """Test that updates of destination are not overwriten"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            canary = os.path.join(workspace, "test.txt")
            with open(canary, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            with open(canary) as f:
                self.assertEqual(f.read(), "Changed")

    def testUpdateLink(self):
        """Test that symlinks are updated"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, "foo.txt"), "w") as f:
                pass
            os.symlink("foo.txt", os.path.join(workspace, "link.txt"))
            self.invokeScm(workspace, s)
            self.assertEqual(os.readlink(os.path.join(workspace, "link.txt")), "test.txt")

    def testCopyNoDirectory(self):
        """Test that source must be a directory"""
        s = self.createImportScm({"url":self.fn})
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, s)
            with self.assertRaises(BuildError):
                self.invokeScm(workspace, ImportScm(s.getProperties(True)))

    def testPrune(self):
        """Test that pruning destination works if requested"""
        s = self.createImportScm({"prune" : True})
        with tempfile.TemporaryDirectory() as workspace:
            canary = os.path.join(workspace, "test.txt")
            with open(canary, "w") as f:
                f.write("Changed")
            self.invokeScm(workspace, s)
            self.assertEqual(self.digest, hashDirectory(workspace))

    def testAudit(self):
        """Test audit record creation and import"""
        s = self.createImportScm()
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            audit = runInEventLoop(ImportAudit.fromDir(*s.getAuditSpec()))

            d = audit.dump()
            self.assertEqual(d["type"], "import")
            self.assertEqual(d["dir"], ".")
            self.assertEqual(d["url"], self.url)

            self.assertEqual(d, ImportAudit.fromData(d).dump())

    def testOutOfTree(self):
        """Out of tree builds are supported"""
        digest = hashDirectory(os.path.join(self.url, "sub"))
        s = self.createImportScm({ 'url' : 'sub' }, projectRoot=self.url)
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            self.assertEqual(digest, hashDirectory(workspace))


class TestImportScmFilter(TestCase):
    """Test rsync-like filter rules of import SCM"""

    @classmethod
    def setUpClass(cls):
        cls.__repodir = tempfile.TemporaryDirectory()
        cls.url = os.path.abspath(cls.__repodir.name)

        def mkfile(*path):
            os.makedirs(os.path.join(cls.url, *path[:-1]), exist_ok=True)
            with open(os.path.join(cls.url, *path), "w") as f:
                f.write("/".join(path))

        mkfile("a_defconfig")
        mkfile("b_defconfig")
        mkfile("readme.txt")
        os.symlink("a_defconfig", os.path.join(cls.url, "link"))
        mkfile("patches", "a", "0001.patch")
        mkfile("patches", "a", "0001.patch.orig")
        mkfile("patches", "b", "0001.patch")
        mkfile("x", "a_defconfig")
        os.mkdir(os.path.join(cls.url, "empty"))

    @classmethod
    def tearDownClass(cls):
        cls.__repodir.cleanup()

    def createImportScm(self, spec = {}, **kwargs):
        s = {
            'scm' : 'import',
            'url' : self.url,
            'recipe' : "foo.yaml#0",
            '__source' : "Recipe foo",
        }
        s.update(spec)
        return ImportScm(s, **kwargs)

    def invokeScm(self, workspace, scm):
        spec = MagicMock(workspaceWorkspacePath=workspace, envWhiteList=set())
        invoker = Invoker(spec, False, True, True, True, True, False)
        runInEventLoop(scm.invoke(invoker, False))

    @staticmethod
    def listTree(root):
        ret = set()
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            for n in dirnames + filenames:
                ret.add(os.path.normpath(os.path.join(rel, n)).replace(os.sep, "/"))
        return ret

    def checkout(self, rules, pruneEmptyDirs=False):
        """Checkout locally and via Jenkins properties. Must be identical."""
        spec = { "filter" : rules }
        if pruneEmptyDirs: spec["pruneEmptyDirs"] = True
        s = self.createImportScm(spec)
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            local = self.listTree(workspace)
            digest = hashDirectory(workspace)
            self.assertEqual(digest, s.calcLiveBuildId(workspace))
            self.assertEqual(digest, runInEventLoop(s.predictLiveBuildId(DummyStep())))
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, ImportScm(s.getProperties(True)))
            self.assertEqual(local, self.listTree(workspace))
            self.assertEqual(digest, hashDirectory(workspace))
        return local

    ALL = { "a_defconfig", "b_defconfig", "readme.txt", "link", "patches",
            "patches/a", "patches/a/0001.patch", "patches/a/0001.patch.orig",
            "patches/b", "patches/b/0001.patch", "x", "x/a_defconfig", "empty" }

    def testNoFilter(self):
        """Without filter, everything is imported"""
        self.assertEqual(self.checkout([]), self.ALL)

    def testSingleFile(self):
        """Single files can be imported. Short and long form are equivalent."""
        self.assertEqual(self.checkout(["+ /a_defconfig", "- *"]), {"a_defconfig"})
        self.assertEqual(self.checkout(["include /a_defconfig", "exclude *"]), {"a_defconfig"})

    def testDefaultInclude(self):
        """Entries that match no rule are included"""
        self.assertEqual(self.checkout(["- *.orig"]),
                         self.ALL - {"patches/a/0001.patch.orig"})

    def testFirstMatchWins(self):
        """The first matching rule decides"""
        # Directory "x" is excluded by the last rule and never traversed
        self.assertEqual(self.checkout(["- b_defconfig", "+ *_defconfig", "- *"]),
                         {"a_defconfig"})
        self.assertEqual(self.checkout(["- b_defconfig", "+ *_defconfig", "+ */", "- *"]),
                         {"a_defconfig", "x", "x/a_defconfig", "patches", "patches/a",
                          "patches/b", "empty"})

    def testAnchoring(self):
        """Patterns are anchored by a leading slash"""
        self.assertEqual(self.checkout(["- a_defconfig"]),
                         self.ALL - {"a_defconfig", "x/a_defconfig"})
        self.assertEqual(self.checkout(["- /a_defconfig"]),
                         self.ALL - {"a_defconfig"})
        # Unanchored patterns with slashes match trailing path components
        self.assertEqual(self.checkout(["- a/0001.patch"]),
                         self.ALL - {"patches/a/0001.patch"})

    def testExcludedDirNotTraversed(self):
        """Like rsync, excluded directories are not traversed"""
        self.assertEqual(self.checkout(["+ /patches/", "+ /patches/a/***", "- *"]),
                         {"patches", "patches/a", "patches/a/0001.patch",
                          "patches/a/0001.patch.orig"})
        self.assertEqual(self.checkout(["+ /patches/", "+ /patches/a/", "+ *.patch", "- *"]),
                         {"patches", "patches/a", "patches/a/0001.patch"})

    def testDirOnly(self):
        """A trailing slash matches only directories"""
        self.assertEqual(self.checkout(["- link/", "- empty/"]), self.ALL - {"empty"})

    def testWildcards(self):
        """Test '*', '**', '?' and character sets"""
        self.assertEqual(self.checkout(["- /*/a/"]),
                         self.ALL - {"patches/a", "patches/a/0001.patch",
                                     "patches/a/0001.patch.orig"})
        self.assertEqual(self.checkout(["- patches/**"]),
                         {"a_defconfig", "b_defconfig", "readme.txt", "link",
                          "patches", "x", "x/a_defconfig", "empty"})
        self.assertEqual(self.checkout(["- **/a_defconfig"]),
                         self.ALL - {"a_defconfig", "x/a_defconfig"})
        self.assertEqual(self.checkout(["- ?_defconfig"]),
                         self.ALL - {"a_defconfig", "b_defconfig", "x/a_defconfig"})
        self.assertEqual(self.checkout(["- [!a]_defconfig"]),
                         self.ALL - {"b_defconfig"})

    def testIncludePattern(self):
        """Common rsync idiom to include files by pattern"""
        patches = {"patches/a/0001.patch", "patches/b/0001.patch"}
        self.assertEqual(self.checkout(["+ */", "+ *.patch", "- *"]),
                         patches | {"patches", "patches/a", "patches/b", "x", "empty"})
        self.assertEqual(self.checkout(["+ */", "+ *.patch", "- *"], True),
                         patches | {"patches", "patches/a", "patches/b"})

    def testPruneEmptyDirs(self):
        """Empty directories are pruned on request, even without filter"""
        self.assertEqual(self.checkout([], True), self.ALL - {"empty"})

    def testNothingImported(self):
        """Filter rules that exclude everything are an error"""
        s = self.createImportScm({"filter" : ["+ /c_defconfig", "- *"]})
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(InvocationError):
                self.invokeScm(workspace, s)
        with self.assertRaises(BuildError):
            s.getProperties(True)
        self.assertIsNone(runInEventLoop(s.predictLiveBuildId(DummyStep())))

    def testInvalidRules(self):
        """Malformed rules are rejected"""
        for r in ["+foo", "+ ", "x foo", "includefoo", "- [z-a]", "+ a\n", "+ a\n- b"]:
            with self.assertRaises((schema.SchemaError, ParseError)):
                self.createImportScm(ImportScm.SCHEMA.validate(
                    {"scm" : "import", "url" : self.url, "filter" : [r]}))

    def testFilterKeepsWorkspaceFiles(self):
        """Filter does not touch files that are already in the workspace"""
        s = self.createImportScm({"filter" : ["+ /a_defconfig", "- *"]})
        with tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, "canary"), "w") as f:
                pass
            self.invokeScm(workspace, s)
            self.assertEqual(self.listTree(workspace), {"a_defconfig", "canary"})

    def testDigest(self):
        """Filters are part of the digest but only if used"""
        def digest(spec):
            return self.createImportScm(spec, fixDigestBug=True).asDigestScript()

        plain = digest({})
        self.assertEqual(plain, self.url + " .")
        self.assertEqual(plain, digest({"filter" : [], "pruneEmptyDirs" : False}))

        # The format must stay stable. Otherwise variant-ids would change!
        rules = digest({"filter" : ["+ a", "- *"]})
        self.assertEqual(rules, self.url + " .\nfilter + a\nfilter - *")
        self.assertEqual(digest({"filter" : ["+ a"], "pruneEmptyDirs" : True}),
                         self.url + " .\npruneEmptyDirs\nfilter + a")
        self.assertEqual(self.createImportScm({"filter" : ["+ a"]}).asDigestScript(),
                         self.url + "\nfilter + a")
        self.assertNotEqual(rules, digest({"filter" : ["- *", "+ a"]}))
        self.assertNotEqual(rules, digest({"filter" : ["+ a", "- *"], "pruneEmptyDirs" : True}))
        self.assertNotEqual(plain, digest({"pruneEmptyDirs" : True}))

    def testProperties(self):
        """Options are in properties but hidden from pretty print if unset"""
        p = self.createImportScm().getProperties(False, True)
        self.assertNotIn("filter", p)
        self.assertNotIn("pruneEmptyDirs", p)

        s = self.createImportScm({"filter" : ["+ a", "- *"], "pruneEmptyDirs" : True})
        p = s.getProperties(False, True)
        self.assertEqual(p["filter"], ["+ a", "- *"])
        self.assertEqual(p["pruneEmptyDirs"], True)

    def testAudit(self):
        """Filters are recorded in the audit trail"""
        s = self.createImportScm({"filter" : ["+ /a_defconfig", "- *"]})
        with tempfile.TemporaryDirectory() as workspace:
            self.invokeScm(workspace, s)
            _, auditDir, extra = s.getAuditSpec()
            audit = runInEventLoop(ImportAudit.fromDir(workspace, auditDir, extra))
            d = audit.dump()
            self.assertEqual(d["digest"]["value"], asHexStr(hashDirectory(workspace)))
            self.assertEqual(d["filter"], ["+ /a_defconfig", "- *"])
            self.assertNotIn("pruneEmptyDirs", d)
            ImportAudit.SCHEMA.validate(d)
            self.assertEqual(d, ImportAudit.fromData(d).dump())
