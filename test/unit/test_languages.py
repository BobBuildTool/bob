# Bob build tool
# Copyright (C) 2026  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import TemporaryDirectory
from unittest import TestCase
import hashlib
import os
import sys

from bob.errors import ParseError
from bob.languages import (ScriptLanguage, getLanguage, BashLanguage,
    PwshLanguage, PythonLanguage, BashResolver, PwshResolver, PythonResolver,
    BASH_FINGERPRINT_SNIPPETS)
from bob.utils import getBashPath, isWindows


class FakeSpec:
    def __init__(self, **kwargs):
        defaults = dict(
            env={},
            paths=[],
            libraryPaths=[],
            sandboxPaths=[],
            workspaceExecPath="/workspace",
            allPaths=[],
            depPaths=[],
            toolPaths=[],
            hasSandbox=False,
            fatSandbox=False,
            isJenkins=False,
            envFile=None,
            setupScript="",
            mainScript="",
            updateScript="",
            interpreterPath=None,
            args=[],
            scriptHint=None,
            fingerprintScript="",
        )
        defaults.update(kwargs)
        self.__dict__.update(defaults)


class ResolverTestBase:
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.baseDir = self.tmp.name
        with open(os.path.join(self.baseDir, "a.txt"), "wb") as f:
            f.write(b"hello world")
        with open(os.path.join(self.baseDir, "b.txt"), "wb") as f:
            f.write(b"second file")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def loader(path):
        with open(path, "rb") as f:
            return f.read()

    def makeResolver(self, origText="orig-text", sourceName="recipe:section",
                      varBase="myvar"):
        return self.RESOLVER(self.loader, self.baseDir, origText, sourceName,
                              varBase)

    def testIncludeFileReference(self):
        """'<' mode embeds a reference to the (single) matched file"""
        r = self.makeResolver()
        val = r["<a.txt"]
        self.assertEqual(val, self.fmtFile("myvar_0"))

    def testIncludeFilesReference(self):
        """'@' mode embeds references to all matched files, in sorted order"""
        r = self.makeResolver()
        val = r["@*.txt"]
        self.assertEqual(val, self.fmtFiles(["myvar_0", "myvar_1"]))

    def testIncludeLiteral(self):
        """"'" mode embeds the literal (escaped) content of the file"""
        r = self.makeResolver()
        val = r["'a.txt"]
        self.assertEqual(val, self.fmtLiteral("hello world"))

    def testNoMatchRaises(self):
        """Missing files throw a ParseError"""
        r = self.makeResolver()
        self.assertRaises(ParseError, lambda: r["<does-not-exist.txt"])

    def testLoaderErrorRaises(self):
        """Read errors are wrapped into a ParseError"""
        def brokenLoader(path):
            raise OSError("boom")
        r = self.RESOLVER(brokenLoader, self.baseDir, "orig", "src", "var")
        self.assertRaises(ParseError, lambda: r["<a.txt"])

    def testResolveDigestAndIncludedFiles(self):
        r = self.makeResolver(origText="orig-text")
        r["<a.txt"]
        content, digest, incFiles = r.resolve("RESULT")
        self.assertEqual(incFiles, {"myvar_0": b"hello world"})
        expected = "\n".join([
            hashlib.sha1(b"orig-text").digest().hex(),
            hashlib.sha1(b"hello world").digest().hex(),
        ])
        self.assertEqual(digest, expected)

    def testResolveAccumulatesMultipleIncludes(self):
        r = self.makeResolver()
        r["<a.txt"]
        r["<b.txt"]
        content, digest, incFiles = r.resolve("RESULT")
        self.assertEqual(incFiles, {
            "myvar_0": b"hello world",
            "myvar_1": b"second file",
        })


class TestBashResolver(ResolverTestBase, TestCase):
    RESOLVER = BashResolver

    @staticmethod
    def fmtFile(name):
        return "$_BOB_TMP_BASE/" + name

    @staticmethod
    def fmtFiles(names):
        return " ".join("$_BOB_TMP_BASE/" + n for n in names)

    @staticmethod
    def fmtLiteral(text):
        from shlex import quote
        return quote(text)


class TestPwshResolver(ResolverTestBase, TestCase):
    RESOLVER = PwshResolver

    @staticmethod
    def fmtFile(name):
        from bob.utils import escapePwsh
        return '"$_BOB_TMP_BASE/' + escapePwsh(name) + '"'

    @staticmethod
    def fmtFiles(names):
        from bob.utils import escapePwsh
        return " ".join('"$_BOB_TMP_BASE/' + escapePwsh(n) + '"' for n in names)

    @staticmethod
    def fmtLiteral(text):
        from bob.utils import quotePwsh
        return quotePwsh(text)


class TestPythonResolver(ResolverTestBase, TestCase):
    RESOLVER = PythonResolver

    @staticmethod
    def fmtFile(name):
        return 'os.path.join(_BOB_TMP_BASE, ' + repr(name) + ')'

    @staticmethod
    def fmtFiles(names):
        return "[" + ", ".join(
            'os.path.join(_BOB_TMP_BASE, ' + repr(n) + ')' for n in names) + "]"

    @staticmethod
    def fmtLiteral(text):
        return repr(text)


class TestBashLanguage(TestCase):

    def testSetupCallWritesMainScript(self):
        spec = FakeSpec(mainScript="echo main", args=["/some-arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = BashLanguage.setupCall(spec, tmp, False, False)
            self.assertTrue(os.path.exists(realFile))
            with open(realFile) as f:
                content = f.read()
            self.assertIn("echo main", content)
            self.assertEqual(getBashPath(), args[0])
            # On Windows, paths are rewritten
            self.assertTrue(args[-1].endswith("some-arg"))

    def testSetupCallTraceAddsDashX(self):
        spec = FakeSpec(mainScript="true")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = BashLanguage.setupCall(spec, tmp, False, True)
            self.assertIn("-x", args)

    def testSetupCallCustomInterpreter(self):
        spec = FakeSpec(mainScript="true", interpreterPath="my/bash")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = BashLanguage.setupCall(spec, tmp, False, False)
            self.assertEqual(args[0], os.path.abspath("my/bash"))

    def testSetupUpdateWritesUpdateScript(self):
        spec = FakeSpec(mainScript="echo main", updateScript="echo update")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = BashLanguage.setupUpdate(spec, tmp, False, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("echo update", content)
            self.assertNotIn("echo main", content)

    def testSetupShellOmitsMainScript(self):
        """The interactive shell only gets prolog+setup, not the main script"""
        spec = FakeSpec(mainScript="echo main", setupScript="echo setup",
                         args=["/an-arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = BashLanguage.setupShell(spec, tmp, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("echo setup", content)
            self.assertNotIn("echo main", content)
            self.assertEqual(getBashPath(), args[0])
            # On Windows, paths are rewritten
            self.assertTrue(args[-1].endswith("an-arg"))

    def testSetupShellKeepEnvSourcesRcFiles(self):
        spec = FakeSpec()
        with TemporaryDirectory() as tmp:
            realFile, _, _ = BashLanguage.setupShell(spec, tmp, True)
            with open(realFile) as f:
                content = f.read()
            self.assertIn(".bashrc", content)

    def testSetupShellNoKeepEnvDoesNotSourceRcFiles(self):
        spec = FakeSpec()
        with TemporaryDirectory() as tmp:
            realFile, _, _ = BashLanguage.setupShell(spec, tmp, False)
            with open(realFile) as f:
                content = f.read()
            self.assertNotIn(".bashrc", content)

    def testFormatIncludesPathsAndEnv(self):
        spec = FakeSpec(env={"FOO": "bar"}, paths=["/opt/bin"],
                         libraryPaths=["/opt/lib"], mainScript="true",
                         allPaths=[("root", "/w/root")],
                         depPaths=[("lib", "/w/lib")],
                         toolPaths=[("host-tools", "/w/tools")])
        with TemporaryDirectory() as tmp:
            realFile, _, _ = BashLanguage.setupCall(spec, tmp, False, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("export FOO=bar", content)
            self.assertIn("/opt/bin", content)
            self.assertIn("/opt/lib", content)
            self.assertIn('[root]=', content)
            self.assertIn('[lib]=', content)
            self.assertIn('[host-tools]=', content)

    def testMangleFingerprintsEmptyScript(self):
        self.assertEqual(BashLanguage.mangleFingerprints([None, ""], {}), "")

    def testMangleFingerprintsBasic(self):
        result = BashLanguage.mangleFingerprints(["foo"], {"A": "1", "B": "2 b"}).splitlines()
        self.assertIn("foo", result)
        self.assertIn("export A=1", result)
        self.assertIn("export B='2 b'", result)

    def testMangleFingerprintsIncludesMatchingSnippet(self):
        snippetName, snippetText = BASH_FINGERPRINT_SNIPPETS[0]
        script = "call-to-" + snippetName + "-here"
        result = BashLanguage.mangleFingerprints([script], {})
        self.assertIn(snippetText, result)

    def testMangleFingerprintsOmitsUnmatchedSnippets(self):
        result = BashLanguage.mangleFingerprints(["nothing special"], {})
        for name, text in BASH_FINGERPRINT_SNIPPETS:
            self.assertNotIn(text, result)

    def testSetupFingerprintDefaultInterpreter(self):
        spec = FakeSpec(fingerprintScript="echo fp")
        env = {"BOB_CWD": "/some/path"}
        args = BashLanguage.setupFingerprint(spec, env, False)
        self.assertEqual(args, [getBashPath(), "-c", "echo fp"])
        self.assertEqual(env["BOB_CWD"], "/some/path")

    def testSetupFingerprintTrace(self):
        spec = FakeSpec(fingerprintScript="echo fp")
        args = BashLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, True)
        self.assertEqual(args, [getBashPath(), "-x", "-c", "echo fp"])

    def testSetupFingerprintCustomInterpreter(self):
        spec = FakeSpec(fingerprintScript="echo fp", interpreterPath="my/bash")
        args = BashLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        self.assertEqual(args[0], os.path.abspath("my/bash"))


class TestPwshLanguage(TestCase):

    def testSetupCallWritesMainScript(self):
        spec = FakeSpec(mainScript="Write-Output main", args=["/some-arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PwshLanguage.setupCall(spec, tmp, False, False)
            self.assertTrue(realFile.endswith(".ps1"))
            with open(realFile) as f:
                content = f.read()
            self.assertIn("Write-Output main", content)
            expectedInterpreter = "powershell" if isWindows() else "pwsh"
            self.assertEqual(args[0], expectedInterpreter)
            # On Windows, paths are rewritten
            self.assertTrue(args[-1].endswith("some-arg"))

    def testSetupCallCustomInterpreter(self):
        spec = FakeSpec(mainScript="true", interpreterPath="my/pwsh")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PwshLanguage.setupCall(spec, tmp, False, False)
            self.assertEqual(args[0], os.path.abspath("my/pwsh"))

    def testSetupUpdateWritesUpdateScript(self):
        spec = FakeSpec(mainScript="Write-Output main", updateScript="Write-Output update")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PwshLanguage.setupUpdate(spec, tmp, False, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("Write-Output update", content)
            self.assertNotIn("Write-Output main", content)

    def testSetupShellOmitsMainScript(self):
        """The interactive shell only gets prolog+setup, not the main script"""
        spec = FakeSpec(mainScript="Write-Output main", setupScript="Write-Output setup",
                         args=["/an/arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PwshLanguage.setupShell(spec, tmp, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("Write-Output setup", content)
            self.assertNotIn("Write-Output main", content)

    def testMangleFingerprintsEmptyScript(self):
        self.assertEqual(PwshLanguage.mangleFingerprints([None, ""], {}), "")

    def testMangleFingerprintsBasic(self):
        result = PwshLanguage.mangleFingerprints(["foo"], {"A": "1"}).splitlines()
        self.assertIn("foo", result)
        self.assertIn('$Env:A="1"', result)

    def testSetupFingerprint(self):
        spec = FakeSpec(fingerprintScript="Write-Output fp")
        args = PwshLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        expectedInterpreter = "powershell" if isWindows() else "pwsh"
        self.assertEqual(args, [expectedInterpreter, "-c", "Write-Output fp"])

    def testSetupFingerprintCustomInterpreter(self):
        spec = FakeSpec(fingerprintScript="Write-Output fp",
                         interpreterPath="my/pwsh")
        args = PwshLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        self.assertEqual(args[0], os.path.abspath("my/pwsh"))


class TestPythonLanguage(TestCase):

    def testSetupCallWritesMainScript(self):
        spec = FakeSpec(mainScript="print('main')", args=["/some-arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PythonLanguage.setupCall(spec, tmp, False, False)
            self.assertTrue(realFile.endswith(".py"))
            with open(realFile) as f:
                content = f.read()
            self.assertIn("print('main')", content)
            self.assertEqual(args[:2], [args[0], "-sS"])
            # On Windows, paths are rewritten
            self.assertTrue(args[-1].endswith("some-arg"))

    def testSetupCallCustomInterpreter(self):
        spec = FakeSpec(mainScript="pass", interpreterPath="my/python3")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PythonLanguage.setupCall(spec, tmp, False, False)
            self.assertEqual(args[0], os.path.abspath("my/python3"))

    def testSetupCallSandboxUsesPython3(self):
        spec = FakeSpec(mainScript="pass", hasSandbox=True)
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PythonLanguage.setupCall(spec, tmp, False, False)
            self.assertEqual(args[0], "python3")

    def testSetupUpdateWritesUpdateScript(self):
        spec = FakeSpec(mainScript="print('main')", updateScript="print('update')")
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PythonLanguage.setupUpdate(spec, tmp, False, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("print('update')", content)
            self.assertNotIn("print('main')", content)

    def testSetupShellOmitsMainScript(self):
        """The interactive shell only gets prolog+setup, not the main script"""
        spec = FakeSpec(mainScript="print('main')", setupScript="print('setup')",
                         args=["/an/arg"])
        with TemporaryDirectory() as tmp:
            realFile, execFile, args = PythonLanguage.setupShell(spec, tmp, False)
            with open(realFile) as f:
                content = f.read()
            self.assertIn("print('setup')", content)
            self.assertNotIn("print('main')", content)

    def testMangleFingerprintsEmptyScript(self):
        self.assertEqual(PythonLanguage.mangleFingerprints([None, ""], {}), "")

    def testMangleFingerprintsBasic(self):
        result = PythonLanguage.mangleFingerprints(["foo"], {"A": "1"}).splitlines()
        self.assertIn("foo", result)
        self.assertIn('os.environ["A"] = ' + repr("1"), result)

    def testSetupFingerprint(self):
        spec = FakeSpec(fingerprintScript="print('fp')")
        args = PythonLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        self.assertEqual(args, [sys.executable, "-sS", "-c", "print('fp')"])

    def testSetupFingerprintSandboxUsesPython3(self):
        spec = FakeSpec(fingerprintScript="print('fp')", hasSandbox=True)
        args = PythonLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        self.assertEqual(args[0], "python3")

    def testSetupFingerprintCustomInterpreter(self):
        spec = FakeSpec(fingerprintScript="print('fp')",
                         interpreterPath="my/python3")
        args = PythonLanguage.setupFingerprint(spec, {"BOB_CWD": "/x"}, False)
        self.assertEqual(args[0], os.path.abspath("my/python3"))
