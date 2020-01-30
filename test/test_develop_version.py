# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import patch
import subprocess

from bob.develop.version import getVersion


class TestGitVersion(TestCase):

    def testInvalid(self):
        with patch("subprocess.check_output") as gitMock:
            gitMock.side_effect = subprocess.CalledProcessError("git", "Error")
            self.assertNotEqual(getVersion(), "")

        with patch("subprocess.check_output") as gitMock:
            gitMock.return_value = "garbage"
            self.assertNotEqual(getVersion(), "")

    def testVersions(self):
        with patch("subprocess.check_output") as gitMock:
            gitMock.return_value = "v0.1.2"
            self.assertEqual(getVersion(), "0.1.2")
            gitMock.return_value = "v1.2.3-rc10"
            self.assertEqual(getVersion(), "1.2.3rc10")
            gitMock.return_value = "v1.0.4-14-g2414721"
            self.assertEqual(getVersion(), "1.0.5.dev14+g2414721")
            gitMock.return_value = "v1.0.4-rc42-14-g2414721"
            self.assertEqual(getVersion(), "1.0.4rc43.dev14+g2414721")

            gitMock.return_value = "v0.1.2-dirty"
            self.assertEqual(getVersion(), "0.1.2+dirty")
            gitMock.return_value = "v1.2.3-rc10-dirty"
            self.assertEqual(getVersion(), "1.2.3rc10+dirty")
            gitMock.return_value = "v1.0.4-14-g2414721-dirty"
            self.assertEqual(getVersion(), "1.0.5.dev14+g2414721.dirty")
            gitMock.return_value = "v1.0.4-rc42-14-g2414721-dirty"
            self.assertEqual(getVersion(), "1.0.4rc43.dev14+g2414721.dirty")
