# Bob Build Tool
# Copyright (C) 2025  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import MagicMock, patch
import os
import sys

from bob.share import LocalShare
from bob.utils import hashDirectory
from bob.errors import BuildError

class TestLocalShare(TestCase):

    def setUp(self):
        # create repo
        self.repo = TemporaryDirectory()
        self.share = LocalShare({ 'path' : self.repo.name })
        self.pkg_tmp = TemporaryDirectory()
        self.pkg = self.pkg_tmp.name
        with open(os.path.join(self.pkg, "audit.json.gz"), "wb") as f:
            pass
        self.workspace = os.path.join(self.pkg, "workspace")
        os.mkdir(self.workspace)

    def tearDown(self):
        self.pkg_tmp.cleanup()
        self.repo.cleanup()

    @skipIf(sys.platform.startswith("win"), "requires POSIX platform")
    def testInstallHardLinks(self):
        """Hard links are preserved when copying"""
        with open(os.path.join(self.workspace, "a"), "wb") as f:
            f.write(b'a')
        os.link(os.path.join(self.workspace, "a"), os.path.join(self.workspace, "b"))
        os.link(os.path.join(self.workspace, "a"), os.path.join(self.workspace, "c"))

        with patch('bob.share.warnEscapedHardLink') as warning:
            warning.show = MagicMock()
            bid = b'a'*20
            self.share.installSharedPackage(self.workspace, bid, hashDirectory(self.workspace), False)
            warning.show.assert_not_called()

        sharePath = self.share._LocalShare__buildPath(bid)
        s1 = os.stat(os.path.join(sharePath, "workspace", "a"))
        s2 = os.stat(os.path.join(sharePath, "workspace", "b"))
        s3 = os.stat(os.path.join(sharePath, "workspace", "c"))
        self.assertEqual(s1.st_nlink, 3)
        self.assertEqual(s2.st_nlink, 3)
        self.assertEqual(s3.st_nlink, 3)
        self.assertEqual(s1.st_dev, s2.st_dev)
        self.assertEqual(s1.st_ino, s2.st_ino)
        self.assertEqual(s2.st_dev, s3.st_dev)
        self.assertEqual(s2.st_ino, s3.st_ino)

    @skipIf(sys.platform.startswith("win"), "requires POSIX platform")
    def testInstallHardLinkOutsideWorkspace(self):
        """Hard links outside the workspace create a warning"""
        with open(os.path.join(self.pkg, "outside"), "wb") as f:
            f.write(b'a')
        os.link(os.path.join(self.pkg, "outside"), os.path.join(self.workspace, "file"))

        with patch('bob.share.warnEscapedHardLink') as warning:
            warning.show = MagicMock()
            bid = b'a'*20
            self.share.installSharedPackage(self.workspace, bid, hashDirectory(self.workspace), False)
            warning.show.assert_called()

    def testInstallCorrupted(self):
        """Changes to the hash sum at the destination are detected"""
        with self.assertRaises(BuildError):
            self.share.installSharedPackage(self.workspace, b'a'*20, b'0'*20, False)
        with self.assertRaises(BuildError):
            self.share.installSharedPackage(self.workspace, b'a'*20, b'0'*20, True)
