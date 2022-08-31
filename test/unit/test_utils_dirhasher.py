# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch
import binascii

import os
import sys
from bob.utils import hashFile, hashDirectory, binStat

class TestHashFile(TestCase):
    def testBigFile(self):
        with TemporaryDirectory() as tmp:
            fn = os.path.join(tmp, "file")
            with open(fn, "wb") as f:
                for i in range(1000):
                    f.write(b'0123456789' * 1024)

            self.assertEqual(hashFile(fn), binascii.unhexlify(
                "c94d8ee379dcbef70b3da8fb57df8020b76b0c70"))

    def testMissingFile(self):
        """Missing files should be treated as empty"""
        with self.assertLogs(level='WARNING') as cm:
            self.assertEqual(hashFile("does-not-exist"), binascii.unhexlify(
                "da39a3ee5e6b4b0d3255bfef95601890afd80709"))
            self.assertEqual(cm.records[0].msg, "Cannot hash file: %s")

class OsScandirList(list):
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, traceback):
        pass

def makeOsScandir(entries):
    m = MagicMock()
    m.return_value = OsScandirList(entries)
    return m

class TestHashDir(TestCase):
    def setUp(self):
        self.umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self.umask)

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
            self.assertEqual(len(sum1), 20)
            # Result depends on path separator character ('/' vs. '\')
            if sys.platform == "win32":
                self.assertEqual(sum1, binascii.unhexlify(
                    "4248c7223c9516dc2f7bafdf48591918d4d99ac8"))
            else:
                self.assertEqual(sum1, binascii.unhexlify(
                    "640f516de78fba0b6d2ddde4451000f142d06b0d"))
            sum2 = hashDirectory(tmp)
            self.assertEqual(sum1, sum2)

    def testRenameDirectory(self):
        """Test that renaming directories has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "dir"))
            with open(os.path.join(tmp, "dir", "file"), 'wb') as f:
                f.write(b'abc')

            sum1 = hashDirectory(tmp)
            os.rename(os.path.join(tmp, "dir"), os.path.join(tmp, "foo"))
            sum2 = hashDirectory(tmp)
            self.assertNotEqual(sum1, sum2)

    def testRenameFile(self):
        """Test that renaming files has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "foo"), 'wb') as f:
                f.write(b'abc')

            sum1 = hashDirectory(tmp)
            os.rename(os.path.join(tmp, "foo"), os.path.join(tmp, "bar"))
            sum2 = hashDirectory(tmp)
            self.assertNotEqual(sum1, sum2)

    def testRewriteFile(self):
        """Changing the file content should change the hash sum"""

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'abc')
                sum1 = hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB1')

                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'qwer')
                sum2 = hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB1')

                self.assertNotEqual(sum1, sum2)

    def testBigIno(self):
        """Test that index handles big inode numbers as found on Windows"""

        s = MagicMock()
        s.st_mode=33188
        s.st_ino=15345198597064824875
        s.st_dev=65027
        s.st_nlink=1
        s.st_uid=1000
        s.st_gid=1000
        s.st_size=3
        s.st_atime_ns=1452798827
        s.st_mtime_ns=1452798827
        s.st_ctime_ns=1452798827
        mock_lstat = MagicMock()
        mock_lstat.return_value = s
        entry = MagicMock()
        entry.is_dir = MagicMock(return_value=False)
        entry.name = b'ghost'
        entry.stat = mock_lstat
        entries = makeOsScandir([ entry ])

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "ghost"), 'wb') as f:
                    f.write(b'abc')

                with patch('os.scandir', entries):
                    hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB1')

    def testOldFile(self):
        """Test negative time fields for files from the past"""

        s = MagicMock()
        s.st_mode=33188
        s.st_ino=270794
        s.st_dev=65026
        s.st_nlink=1
        s.st_uid=1000
        s.st_gid=1000
        s.st_size=4
        s.st_atime_ns=1601623698
        s.st_mtime_ns=-3600
        s.st_ctime_ns=1601623698
        mock_lstat = MagicMock()
        mock_lstat.return_value = s
        entry = MagicMock()
        entry.is_dir = MagicMock(return_value=False)
        entry.name = b'McFly'
        entry.stat = mock_lstat
        entries = makeOsScandir([ entry ])

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "McFly"), 'wb') as f:
                    pass

                with patch('os.scandir', entries):
                    h = hashDirectory(tmp, index)
                with patch('os.stat', mock_lstat):
                    b = binStat("whatever")

        self.assertEqual(h, b'\xdc\xe1\xf4\x02\x01\xc3\xa1\xf7j\xac\xbc\xbf=1ey\x11\x1a\xc8\xda')
        self.assertEqual(type(b), bytes)

    def testBlockDev(self):
        """Test that index handles block devices"""

        s = MagicMock()
        s.st_mode=25008
        s.st_ino=8325
        s.st_dev=6
        s.st_nlink=1
        s.st_uid=0
        s.st_gid=6
        s.st_rdev=2048
        s.st_size=0
        s.st_atime_ns=1453317243
        s.st_mtime_ns=1451854748
        s.st_ctime_ns=1451854748
        mock_lstat = MagicMock()
        mock_lstat.return_value = s
        entry = MagicMock()
        entry.is_dir = MagicMock(return_value=False)
        entry.name = b'sda'
        entry.stat = mock_lstat
        entries = makeOsScandir([ entry ])

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with patch('os.scandir', entries):
                h = hashDirectory("whatever", index)

        self.assertEqual(h, b'\xe8\x8e\xad\x9bv\xcbt\xc4\xcd\xa7x\xdb\xde\x96\xab@\x18\xb1\xdcX')

    def testChrDev(self):
        """Test that index handles character devices"""

        s = MagicMock()
        s.st_mode=8630
        s.st_ino=8325
        s.st_dev=6
        s.st_nlink=1
        s.st_uid=0
        s.st_gid=6
        s.st_rdev=1280
        s.st_size=0
        s.st_atime_ns=1453317243
        s.st_mtime_ns=1451854748
        s.st_ctime_ns=1451854748
        mock_lstat = MagicMock()
        mock_lstat.return_value = s
        entry = MagicMock()
        entry.is_dir = MagicMock(return_value=False)
        entry.name = b'tty'
        entry.stat = mock_lstat
        entries = makeOsScandir([ entry ])

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with patch('os.scandir', entries):
                h = hashDirectory("whatever", index)

        self.assertEqual(h, b"\x9b\x98~\xa5\xd5\xc4\x1e\xe29'\x8d\x1e\xe1\x12\xdd\xf4\xa51\xf5d")

