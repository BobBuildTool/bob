# Bob build tool
# Copyright (C) 2026  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import redirect_stdout
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
import io
import os
import sys

from bob.cmds.help import showManPage
from bob.errors import BobError


class TestShowManPage(TestCase):

    @skipIf(sys.platform == "win32", "Bob uses man only on POSIX platforms")
    def testUseMan(self):
        """Use "man" if it is available"""
        with patch("bob.cmds.help.shutil.which", return_value="/usr/bin/man"), \
             patch("bob.develop.make.makeManpages", return_value="/man"), \
             patch("bob.cmds.help.subprocess.call", return_value=0) as call:
            self.assertEqual(showManPage("bob-build", "1"), 0)
        call.assert_called_once_with(["man", os.path.join("/man", "bob-build.1")])

    def testNoMan(self):
        """Fall back to plain text help if there is no "man" """
        with TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "bobpaths.txt"), "w", encoding="utf-8") as f:
                f.write("bobpaths - Specifying paths to Bob packages\n")
            out = io.StringIO()
            with patch("bob.cmds.help.shutil.which", return_value=None), \
                 patch("bob.develop.make.makeHelpText", return_value=tmp), \
                 redirect_stdout(out):
                self.assertEqual(showManPage("bobpaths", "7"), 0)
            self.assertIn("bobpaths - Specifying paths to Bob packages", out.getvalue())

    @skipIf(sys.platform == "win32", "Bob uses man only on POSIX platforms")
    def testUseManRelease(self):
        """Release builds use the installed manpages via "man SECTION PAGE" """
        with patch.dict(sys.modules, {"bob.develop": None}), \
             patch("bob.cmds.help.shutil.which", return_value="/usr/bin/man"), \
             patch("bob.cmds.help.subprocess.call", return_value=0) as call:
            self.assertEqual(showManPage("bob-build", "1"), 0)
        call.assert_called_once_with(["man", "1", "bob-build"])

    def testNoManRelease(self):
        """Release builds read the plain text help from the bob/_man package data"""
        with TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "_man"))
            with open(os.path.join(tmp, "_man", "bobpaths.txt"), "w", encoding="utf-8") as f:
                f.write("bobpaths - Specifying paths to Bob packages\n")
            out = io.StringIO()
            with patch.dict(sys.modules, {"bob.develop": None}), \
                 patch("bob.cmds.help.__file__", os.path.join(tmp, "cmds", "help.py")), \
                 patch("bob.cmds.help.shutil.which", return_value=None), \
                 redirect_stdout(out):
                self.assertEqual(showManPage("bobpaths", "7"), 0)
            self.assertIn("bobpaths - Specifying paths to Bob packages", out.getvalue())

    def testMissingPageRelease(self):
        """Unknown help pages raise a proper error in release builds too"""
        with TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, {"bob.develop": None}), \
                 patch("bob.cmds.help.__file__", os.path.join(tmp, "cmds", "help.py")), \
                 patch("bob.cmds.help.shutil.which", return_value=None):
                with self.assertRaises(BobError):
                    showManPage("bobfoo", "7")

    @skipIf(sys.platform == "win32", "Bob uses man only on POSIX platforms")
    def testBuildManpageFails(self):
        """Errors while building the manpages are reported"""
        with patch("bob.cmds.help.shutil.which", return_value="/usr/bin/man"), \
             patch("bob.develop.make.makeManpages", side_effect=OSError("boom")):
            with self.assertRaises(BobError):
                showManPage("bob-build", "1")

    @skipIf(sys.platform == "win32", "Bob uses man only on POSIX platforms")
    def testManFails(self):
        """Failing to execute "man" returns an error code"""
        with patch("bob.cmds.help.shutil.which", return_value="/usr/bin/man"), \
             patch("bob.develop.make.makeManpages", return_value="/man"), \
             patch("bob.cmds.help.subprocess.call", side_effect=OSError("boom")):
            self.assertEqual(showManPage("bob-build", "1"), 1)

    def testBuildHelpTextFails(self):
        """Errors while building the plain text help are reported"""
        with patch("bob.cmds.help.shutil.which", return_value=None), \
             patch("bob.develop.make.makeHelpText", side_effect=OSError("boom")):
            with self.assertRaises(BobError):
                showManPage("bobpaths", "7")

    def testReadHelpTextFails(self):
        """Errors other than a missing page while reading the help are reported"""
        with TemporaryDirectory() as tmp:
            # A directory cannot be opened as a file -> OSError, but not FileNotFoundError
            os.mkdir(os.path.join(tmp, "bobpaths.txt"))
            with patch("bob.cmds.help.shutil.which", return_value=None), \
                 patch("bob.develop.make.makeHelpText", return_value=tmp):
                with self.assertRaises(BobError):
                    showManPage("bobpaths", "7")

    def testMissingPage(self):
        """Unknown help pages raise a proper error"""
        with TemporaryDirectory() as tmp:
            with patch("bob.cmds.help.sys.platform", "win32"), \
                 patch("bob.develop.make.makeHelpText", return_value=tmp):
                with self.assertRaises(BobError):
                    showManPage("bobfoo", "7")
