# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch
import binascii

import os
from bob.utils import hashFile, hashDirectory

class TestHashFile(TestCase):
    def testBigFile(self):
        with NamedTemporaryFile() as f:
            for i in range(1000):
                f.write(b'0123456789' * 1024)
            f.flush()

            hashFile(f.name) == binascii.unhexlify(
                "c94d8ee379dcbef70b3da8fb57df8020b76b0c70")

    def testMissingFile(self):
        """Missing files should be treated as empty"""
        assert hashFile("does-not-exist") == binascii.unhexlify(
            "da39a3ee5e6b4b0d3255bfef95601890afd80709")

class TestHashDir(TestCase):
    def testDirAndFile(self):
        """Test hashing a directory with one file.

        The hash sum should stay stable in the long run as this might be used
        for binary artifact matching in the future.
        """

        with TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "dir"))
            with open(os.path.join(tmp, "dir", "file"), 'wb') as f:
                f.write(b'abc')

            sum1 = hashDirectory(tmp)
            assert len(sum1) == 20
            assert sum1 == binascii.unhexlify(
                "640f516de78fba0b6d2ddde4451000f142d06b0d")
            sum2 = hashDirectory(tmp)
            assert sum1 == sum2

    def testRenameDirectory(self):
        """Test that renaming directories has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "dir"))
            with open(os.path.join(tmp, "dir", "file"), 'wb') as f:
                f.write(b'abc')

            sum1 = hashDirectory(tmp)
            os.rename(os.path.join(tmp, "dir"), os.path.join(tmp, "foo"))
            sum2 = hashDirectory(tmp)
            assert sum1 != sum2

    def testRenameFile(self):
        """Test that renaming files has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "foo"), 'wb') as f:
                f.write(b'abc')

            sum1 = hashDirectory(tmp)
            os.rename(os.path.join(tmp, "foo"), os.path.join(tmp, "bar"))
            sum2 = hashDirectory(tmp)
            assert sum1 != sum2

    def testRewriteFile(self):
        """Changing the file content should change the hash sum"""

        with NamedTemporaryFile() as index:
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'abc')
                sum1 = hashDirectory(tmp, index.name)

                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'qwer')
                sum2 = hashDirectory(tmp, index.name)

                assert sum1 != sum2

    def testBigIno(self):
        """Test that index handles big inode numbers as found on Windows"""

        s = MagicMock()
        s.st_mode=33188
        s.st_ino=-5345198597064824875
        s.st_dev=65027
        s.st_nlink=1
        s.st_uid=1000
        s.st_gid=1000
        s.st_size=3
        s.st_atime=1452798827
        s.st_mtime=1452798827
        s.st_ctime=1452798827
        mock_lstat = MagicMock()
        mock_lstat.return_value = s

        with NamedTemporaryFile() as index:
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "ghost"), 'wb') as f:
                    f.write(b'abc')

                with patch('os.lstat', mock_lstat):
                    hashDirectory(tmp, index.name)

