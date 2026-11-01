# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import MagicMock, mock_open, patch
import binascii
import errno
import hashlib

import os
import sys
import time
from bob.utils import hashFile, binStat, DirHasher, canSymlink

try:
    from bob._dirhasher import hash_directory_with_size
    def rustHashDirectory(path, index=None, ignoreDirs=None):
        return hash_directory_with_size(path, index, ignoreDirs)[0]
except ImportError:
    rustHashDirectory = None

def pyHashDirectory(path, index=None, ignoreDirs=None):
    return DirHasher(index, ignoreDirs).hashDirectory(path)[0]


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

def waitClockTick(path):
    """Wait until the file system clock has advanced.

    Files that are not older than the file system time are "racily clean"
    and are not recorded in the index. Make sure all files are old enough
    so that the index content is deterministic.
    """
    def change_time():
        with NamedTemporaryFile(dir=path) as f:
            if sys.platform == "win32":
                return os.fstat(f.fileno()).st_mtime_ns
            else:
                return os.fstat(f.fileno()).st_ctime_ns
    start = change_time()
    while change_time() <= start:
        time.sleep(0.001)

class FailingFile:
    """File wrapper that fails all writes after the first one."""
    def __init__(self, f):
        self.f = f
        self.name = f.name
        self.writes = 0
    def write(self, data):
        self.writes += 1
        if self.writes > 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return self.f.write(data)
    def close(self):
        self.f.close()

class CommonHashDirTests:
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

            sum1 = self.hashDirectory(tmp)
            self.assertEqual(len(sum1), 20)
            # Result depends on path separator character ('/' vs. '\')
            if sys.platform == "win32":
                self.assertEqual(sum1, binascii.unhexlify(
                    "4248c7223c9516dc2f7bafdf48591918d4d99ac8"))
            else:
                self.assertEqual(sum1, binascii.unhexlify(
                    "640f516de78fba0b6d2ddde4451000f142d06b0d"))
            sum2 = self.hashDirectory(tmp)
            self.assertEqual(sum1, sum2)

    def testRenameDirectory(self):
        """Test that renaming directories has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "dir"))
            with open(os.path.join(tmp, "dir", "file"), 'wb') as f:
                f.write(b'abc')

            sum1 = self.hashDirectory(tmp)
            os.rename(os.path.join(tmp, "dir"), os.path.join(tmp, "foo"))
            sum2 = self.hashDirectory(tmp)
            self.assertNotEqual(sum1, sum2)

    def testRenameFile(self):
        """Test that renaming files has an influence on the checksum"""

        with TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "foo"), 'wb') as f:
                f.write(b'abc')

            sum1 = self.hashDirectory(tmp)
            os.rename(os.path.join(tmp, "foo"), os.path.join(tmp, "bar"))
            sum2 = self.hashDirectory(tmp)
            self.assertNotEqual(sum1, sum2)

    def testRewriteFile(self):
        """Changing the file content should change the hash sum"""

        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index.bin")
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'abc')
                sum1 = self.hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB2')

                with open(os.path.join(tmp, "foo"), 'wb') as f:
                    f.write(b'qwer')
                sum2 = self.hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB2')

                self.assertNotEqual(sum1, sum2)

    def testMissingIndexDir(self):
        """Index in non-existing directory just issues a warning"""
        with TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "missing", "index.bin")
            with open(os.path.join(tmp, "file"), "wb") as f:
                f.write(b'abc')
            with self.assertLogs(level='WARNING') as cm:
                if sys.platform == "win32":
                    expect = b'\x88\xee\x84\x87Y\xb21\xaa\x84\x98\x05\xc9&\xcf#4\x05\xe2\xab\x15'
                else:
                    expect = b'\x039\xa9+\xebb\xaf\xbe\x11>@\xf7\xeeMl}\xb6\xd0\x9a\x90'
                self.assertEqual(self.hashDirectory(tmp, index), expect)
            self.assertTrue(cm.records[0].getMessage().startswith("Cannot write hash cache:"))

    def testIndexIsDirectory(self):
        """Unreadable and unwritable index just issues warnings"""
        with TemporaryDirectory() as indexDir:
            index = os.path.join(indexDir, "index")
            os.mkdir(index)
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "file"), "wb") as f:
                    f.write(b'abc')
                if sys.platform == "win32":
                    expect = b'\x88\xee\x84\x87Y\xb21\xaa\x84\x98\x05\xc9&\xcf#4\x05\xe2\xab\x15'
                else:
                    expect = b'\x039\xa9+\xebb\xaf\xbe\x11>@\xf7\xeeMl}\xb6\xd0\x9a\x90'
                with self.assertLogs(level='WARNING') as cm:
                    self.assertEqual(self.hashDirectory(tmp, index), expect)
            msgs = [ r.getMessage() for r in cm.records ]
            self.assertEqual(len(msgs), 2)
            self.assertTrue(msgs[0].startswith("Cannot open hash cache:"))
            self.assertTrue(msgs[1].startswith("Cannot write hash cache:"))
            # The temporary new index must have been removed
            self.assertEqual(os.listdir(indexDir), ["index"])

    def testBadIndex(self):
        """A corrupted index is ignored"""
        with TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "index.bin")
            with open(index, "wb") as f:
                f.write(b'garbage')

            ws = os.path.join(tmp, "dir")
            os.mkdir(ws)
            with open(os.path.join(ws, "file"), 'wb') as f:
                f.write(b'abc')

            if sys.platform == "win32":
                expect = b'\x88\xee\x84\x87Y\xb21\xaa\x84\x98\x05\xc9&\xcf#4\x05\xe2\xab\x15'
            else:
                expect = b'\x039\xa9+\xebb\xaf\xbe\x11>@\xf7\xeeMl}\xb6\xd0\x9a\x90'
            self.assertEqual(self.hashDirectory(ws, index), expect)

            with open(index, "rb") as f:
                self.assertEqual(f.read(4), b'BOB2')

    def testNoDirectory(self):
        """A file cannot be hashed and issues a warning"""
        with NamedTemporaryFile() as tmp:
            with self.assertLogs(level='WARNING') as cm:
                self.assertEqual(self.hashDirectory(tmp.name),
                                 b'\xda9\xa3\xee^kK\r2U\xbf\xef\x95`\x18\x90\xaf\xd8\x07\t')
            self.assertTrue(cm.records[0].getMessage().startswith("Cannot list directory:"))


class TestHashDirPython(CommonHashDirTests, TestCase):
    hashDirectory = staticmethod(pyHashDirectory)

    def prepareCacheWriteFailure(self, indexDir, ws):
        """Create a valid index and add a file that must be added to the index."""
        index = os.path.join(indexDir, "index.bin")
        with open(os.path.join(ws, "a"), "wb") as f:
            f.write(b'abc')
        waitClockTick(indexDir)
        self.hashDirectory(ws, index)
        with open(index, "rb") as f:
            oldIndex = f.read()
        with open(os.path.join(ws, "b"), "wb") as f:
            f.write(b'def')
        waitClockTick(indexDir)
        return index, oldIndex

    def testCacheWriteFails(self):
        """A failed index update keeps the old index"""
        with TemporaryDirectory() as indexDir, TemporaryDirectory() as ws:
            index, oldIndex = self.prepareCacheWriteFailure(indexDir, ws)
            with patch("bob.utils.NamedTemporaryFile",
                       side_effect=lambda *a, **kw: FailingFile(NamedTemporaryFile(*a, **kw))):
                with self.assertLogs(level='WARNING') as cm:
                    h = self.hashDirectory(ws, index)
            self.assertEqual(h, self.hashDirectory(ws))
            self.assertEqual(len(cm.records), 1)
            self.assertTrue(cm.records[0].getMessage().startswith("Cannot write hash cache:"))
            with open(index, "rb") as f:
                self.assertEqual(f.read(), oldIndex)
            self.assertEqual(os.listdir(indexDir), ["index.bin"])

    def testCacheCleanupFails(self):
        """Failing to remove the aborted index is ignored"""
        with TemporaryDirectory() as indexDir, TemporaryDirectory() as ws:
            index, oldIndex = self.prepareCacheWriteFailure(indexDir, ws)
            with patch("bob.utils.NamedTemporaryFile",
                       side_effect=lambda *a, **kw: FailingFile(NamedTemporaryFile(*a, **kw))), \
                 patch("os.unlink", side_effect=OSError(errno.EACCES, "Permission denied")):
                with self.assertLogs(level='WARNING') as cm:
                    h = self.hashDirectory(ws, index)
            self.assertEqual(h, self.hashDirectory(ws))
            self.assertEqual(len(cm.records), 1)
            with open(index, "rb") as f:
                self.assertEqual(f.read(), oldIndex)

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
                    self.hashDirectory(tmp, index)

                with open(index, "rb") as f:
                    self.assertEqual(f.read(4), b'BOB2')

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
                    h = self.hashDirectory(tmp, index)
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
                h = self.hashDirectory("whatever", index)

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
                h = self.hashDirectory("whatever", index)

        self.assertEqual(h, b"\x9b\x98~\xa5\xd5\xc4\x1e\xe29'\x8d\x1e\xe1\x12\xdd\xf4\xa51\xf5d")

@skipIf(rustHashDirectory is None, "rust DirHasher implementation not available")
class TestHashDirRust(CommonHashDirTests, TestCase):
    hashDirectory = staticmethod(rustHashDirectory)

@skipIf(rustHashDirectory is None, "rust DirHasher implementation not available")
class TestHashDirCompat(TestCase):
    """Compare rust implementation against Python implementation"""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = root = os.path.join(self.tmp.name, "root")
        def mk(path, content=b''):
            path = os.path.join(root, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)
        # Names that exercise the sort order of files vs. directories
        mk("a-b", b'1')
        mk("a/x", b'2')
        mk("a0", b'3')
        os.chmod(os.path.join(root, "a0"), 0o755)
        mk("a.c/y", b'4')
        mk("big", os.urandom(1024*1024+17))
        mk("sub/deep/er/file", b'deep')
        mk("sub/BaseDirList.txt", b'ignored')
        mk(".git/config", b'ignored')
        mk("ign/file", b'ignored by option')
        mk("sub/ign/file", b'ignored by option')
        if sys.platform != "win32":
            # On Windows, invalid UTF-8 sequences are not allowed
            mk(os.fsdecode(b"y\xe4\xf6\xfc-latin1"), b'5')
            mk("Umlaut-\u00e4\u00f6\u00fc", b'6')
            # Windows does not support FIFOs
            os.mkfifo(os.path.join(root, "fifo"))
        os.mkdir(os.path.join(root, "empty"))
        if canSymlink():
            os.symlink("a/x", os.path.join(root, "link"))
            os.symlink("dangling", os.path.join(root, "sub", "dangling"))
            os.symlink("sub", os.path.join(root, "dirlink"))

    def check(self, path, index=False, ignoreDirs=None):
        results = []
        impls = ["py", "rust"]
        funs = [pyHashDirectory, rustHashDirectory]
        for name, func in zip(impls, funs):
            indexPath = os.path.join(self.tmp.name, name + ".bin") if index else None
            results.append(func(path, indexPath, ignoreDirs))
            results.append(func(path, indexPath, ignoreDirs)) # run again with index cache
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(results[0], results[3])
        return results[0]

    def testHashDirectory(self):
        d1 = self.check(self.root)
        d2 = self.check(self.root, ignoreDirs=["ign"])
        self.assertNotEqual(d1, d2)
        self.check(os.path.join(self.root, "dirlink"))
        self.check(os.path.join(self.root, "sub"))

    def testManyFiles(self):
        """Enough files to keep all threads busy and to prefetch files"""
        for i in range(500):
            d = os.path.join(self.root, "many", str(i % 7))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, str(i)), "wb") as f:
                f.write(os.urandom(i * 97))
        self.check(self.root)
        self.check(self.root, True)

    def waitClockTick(self):
        waitClockTick(self.tmp.name)

    def testHashDirectoryIndex(self):
        """Both implementations create the same index"""
        self.waitClockTick()
        d1 = self.check(self.root, True)
        self.assertEqual(d1, self.check(self.root))
        with open(os.path.join(self.tmp.name, "py.bin"), "rb") as f:
            pyIndex = f.read()
        with open(os.path.join(self.tmp.name, "rust.bin"), "rb") as f:
            rustIndex = f.read()
        self.assertEqual(pyIndex, rustIndex)

    def checkIndexUpdate(self):
        """Hash once with each implementation and compare the updated indices.

        A second run would fix up a wrongly updated index. Hence each
        implementation is invoked just once.
        """
        pyIndex = os.path.join(self.tmp.name, "py.bin")
        rustIndex = os.path.join(self.tmp.name, "rust.bin")
        self.assertEqual(pyHashDirectory(self.root, pyIndex),
                         rustHashDirectory(self.root, rustIndex))
        with open(pyIndex, "rb") as f:
            pyIndex = f.read()
        with open(rustIndex, "rb") as f:
            rustIndex = f.read()
        self.assertEqual(pyIndex, rustIndex)
        return pyIndex

    def testHashDirectoryIndexUpdate(self):
        """Removed and added files update the index identically"""
        self.waitClockTick()
        i1 = self.checkIndexUpdate()

        # Entries of removed files are dropped
        os.unlink(os.path.join(self.root, "a-b"))
        i2 = self.checkIndexUpdate()
        self.assertNotIn(b"a-b", i2)

        # Appending after the last entry keeps all other entries
        with open(os.path.join(self.root, os.fsdecode(b"zzz-end")), "wb") as f:
            f.write(b'end')
        self.waitClockTick()
        i3 = self.checkIndexUpdate()
        self.assertTrue(i3.startswith(i2))
        self.assertIn(b"zzz-end", i3)

