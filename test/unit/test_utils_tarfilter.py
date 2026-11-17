# Bob build tool
# Copyright (C) 2026  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
import io
import os
import stat
import sys
import tarfile

from bob.errors import BuildError
from bob.utils import tarfileOpen
from bob import utils
from bob.tty import WarnOnce

def SYM(name, target):
    return (name, tarfile.SYMTYPE, target)

def LNK(name, target):
    return (name, tarfile.LNKTYPE, target)

def DIR(name):
    return (name, tarfile.DIRTYPE, None)

def REG(name, data=b"PWNED\n"):
    return (name, tarfile.REGTYPE, data)

def FIFO(name):
    return (name, tarfile.FIFOTYPE, None)

def CHR(name):
    return (name, tarfile.CHRTYPE, None)

def BLK(name):
    return (name, tarfile.BLKTYPE, None)

class TestTarExtractFilter(TestCase):
    """Verify that crafted tar files cannot write outside of the destination.

    Layout of the temporary directory:

        victim/secret       -- must never be modified
        victim/sub/deep/    -- must stay empty
        ws/                 -- extraction destination

    The "@V" placeholder in link targets is replaced by the absolute path of
    the victim directory.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)
        self.victim = os.path.join(self.base, "victim")
        os.makedirs(os.path.join(self.victim, "sub", "deep"))
        self.secret = os.path.join(self.victim, "secret")
        with open(self.secret, "w") as f:
            f.write("ORIGINAL\n")
        self.ws = os.path.join(self.base, "ws")
        os.makedirs(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def makeTar(self, entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, typ, arg in entries:
                ti = tarfile.TarInfo(name)
                ti.type = typ
                if typ in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    ti.linkname = arg.replace("@V", self.victim)
                    tar.addfile(ti)
                elif typ == tarfile.REGTYPE:
                    ti.size = len(arg)
                    tar.addfile(ti, io.BytesIO(arg))
                else:
                    if typ == tarfile.DIRTYPE:
                        ti.mode = 0o755
                    tar.addfile(ti)
        buf.seek(0)
        return buf

    def extract(self, entries, dest=None):
        """Extract like bob.archive does (streaming, errorlevel=1)"""
        with tarfileOpen(None, "r|*", fileobj=self.makeTar(entries),
                         errorlevel=1) as tar:
            tar.extractall(dest or self.ws)

    def assertVictimUntouched(self):
        with open(self.secret) as f:
            self.assertEqual(f.read(), "ORIGINAL\n")
        self.assertEqual(sorted(os.listdir(self.victim)), ["secret", "sub"])
        self.assertEqual(os.listdir(os.path.join(self.victim, "sub", "deep")), [])

    def assertRefused(self, entries, dest=None):
        with self.assertRaises(BuildError):
            self.extract(entries, dest)
        self.assertVictimUntouched()

    # Regular names

    def testAbsoluteNameIsStripped(self):
        """Leading slashes are stripped like GNU tar does"""
        self.extract([REG("/abs/file", b"data")])
        with open(os.path.join(self.ws, "abs", "file"), "rb") as f:
            self.assertEqual(f.read(), b"data")
        self.assertVictimUntouched()

    def testDotDotName(self):
        self.assertRefused([REG("../victim/secret")])
        self.assertRefused([REG("a/../../victim/secret")])

    def testInternalDotDotIsNormalized(self):
        self.extract([REG("a/../b", b"data")])
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "b")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "a")))

    def testDotDotNoOutsideDirs(self):
        """Intermediate directories must not be created outside.

        tarfile creates missing parent directories with os.makedirs() on the
        raw name. Without normalization, components that are later left by
        '..' would be created even if they are outside of the destination.
        """
        self.extract([REG("../outside/../ws/f", b"data")])
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "f")))
        self.assertEqual(sorted(os.listdir(self.base)), ["victim", "ws"])
        self.assertVictimUntouched()

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testDotDotNoOutsideDirsThroughSymlink(self):
        """Same as above but through a symlink pointing outside."""
        self.extract([SYM("s", "@V"), REG("s/newdir/../../ws/f", b"data")])
        self.assertEqual(sorted(os.listdir(self.base)), ["victim", "ws"])
        self.assertVictimUntouched()

    # Symlinks

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testFileThroughSymlinkDir(self):
        self.assertRefused([SYM("s", "@V"), REG("s/secret")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testFileThroughRelativeSymlink(self):
        self.assertRefused([SYM("s", "../victim"), REG("s/secret")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testFileThroughSymlinkChain(self):
        self.assertRefused([SYM("a", "b"), SYM("b", "@V"), REG("a/secret")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testFileOverSymlink(self):
        """A regular file must not be written through an existing symlink"""
        self.assertRefused([SYM("s", "@V/secret"), REG("s")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testNewFileThroughSymlinkDir(self):
        self.assertRefused([SYM("d", "@V/sub/deep"), REG("d/new")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testReplaceDestination(self):
        """The destination itself must not be replaced by a symlink"""
        for name in ("", ".", "./", "a/..", "../ws"):
            with self.subTest(name=name):
                self.assertRefused([SYM(name, "@V"), REG("secret")])
                self.assertTrue(os.path.isdir(self.ws))
                self.assertFalse(os.path.islink(self.ws))

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testReplaceSymlinkDestination(self):
        """Replacing a symlinked destination must be refused too"""
        os.rmdir(self.ws)
        os.makedirs(os.path.join(self.base, "real"))
        os.symlink("real", self.ws)
        self.assertRefused([SYM("../ws", "@V"), REG("secret")])
        self.assertEqual(os.readlink(self.ws), "real")

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testReplaceOutsideSymlink(self):
        """A symlink outside of the destination that points into the
        destination must not be replaced."""
        alias = os.path.join(self.base, "alias")
        os.symlink("ws", alias)
        self.assertRefused([SYM("../alias", "@V")])
        self.assertEqual(os.readlink(alias), "ws")

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testSymlinkOutsideAllowed(self):
        """Symlinks may point outside and can be overwritten later"""
        self.extract([SYM("s", "@V"), SYM("s", "/nonexistent"),
                      SYM("r", "../victim/secret")])
        self.assertEqual(os.readlink(os.path.join(self.ws, "s")), "/nonexistent")
        self.assertEqual(os.readlink(os.path.join(self.ws, "r")), "../victim/secret")
        self.assertVictimUntouched()

    # Hard links

    def testHardLinkAbsolute(self):
        self.assertRefused([LNK("h", "@V/secret"), REG("h")])

    def testHardLinkDotDot(self):
        self.assertRefused([LNK("h", "../victim/secret"), REG("h")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testHardLinkToSymlink(self):
        self.assertRefused([SYM("s", "@V/secret"), LNK("h", "s"), REG("h")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testHardLinkThroughSymlink(self):
        self.assertRefused([SYM("s", "@V"), LNK("h", "s/secret"), REG("h")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testHardLinkSymlinkDotDot(self):
        """The link target must not be normalized lexically.

        "s/../secret" looks like "secret" but the kernel resolves it to
        "victim/sub/../secret".
        """
        self.assertRefused([SYM("s", "@V/sub"), LNK("h", "s/../secret"), REG("h")])
        self.assertRefused([SYM("s", "@V/sub/deep"), LNK("h", "s/../../secret"), REG("h")])
        self.assertRefused([DIR("d"), SYM("s", "@V/sub"),
                            LNK("d/h", "d/../s/../secret"), REG("d/h")])

    @skipIf(sys.platform == "win32", "requires symlinks")
    def testHardLinkOverOutsideSymlink(self):
        """The location of the hard link must be inside the destination"""
        alias = os.path.join(self.base, "alias")
        os.symlink("ws", alias)
        self.assertRefused([REG("f"), LNK("../alias", "f")])
        self.assertEqual(os.readlink(alias), "ws")

    def testHardLinkInside(self):
        self.extract([DIR("d"), REG("d/f", b"data"), LNK("d/h", "d/f"),
                      LNK("h2", "./d/../d/f")])
        f = os.stat(os.path.join(self.ws, "d", "f"))
        self.assertEqual(os.stat(os.path.join(self.ws, "d", "h")).st_ino, f.st_ino)
        self.assertEqual(os.stat(os.path.join(self.ws, "h2")).st_ino, f.st_ino)

    # Special files

    def testDeviceNodes(self):
        self.assertRefused([CHR("c")])
        self.assertRefused([BLK("b")])
        self.assertFalse(os.path.lexists(os.path.join(self.ws, "c")))
        self.assertFalse(os.path.lexists(os.path.join(self.ws, "b")))

    # Unsafe Python version

    def testMissingExtractionFilter(self):
        """Warn once and leave extraction unfiltered without PEP 706."""
        archive = self.makeTar([REG('f', b'data')])
        stderr = io.StringIO()
        with patch.object(utils, '_warnUnsafeTar', WarnOnce('unfiltered')), \
             patch('sys.stderr', stderr), patch.object(tarfile, 'data_filter'):
            del tarfile.data_filter
            with tarfileOpen(fileobj=archive) as tar:
                self.assertIsNone(tar.extraction_filter)
            with tarfileOpen(fileobj=self.makeTar([])):
                pass
        self.assertEqual(stderr.getvalue().count('unfiltered'), 1)

    def testWritingWithoutExtractionFilterDoesNotWarn(self):
        with patch.object(utils, '_warnUnsafeTar') as warning, \
             patch.object(tarfile, 'data_filter'):
            del tarfile.data_filter
            with tarfileOpen(fileobj=io.BytesIO(), mode='w'):
                pass
        warning.warn.assert_not_called()

    def testMissingAllowMissing(self):
        """Refuse extraction if os.path.ALLOW_MISSING is not available.

        realpath(strict=False) would silently ignore errors, which makes the
        filter unreliable (CVE-2025-4517).
        """
        allowMissing = os.path.ALLOW_MISSING
        del os.path.ALLOW_MISSING
        try:
            with self.assertRaises(BuildError) as cm:
                self.extract([REG("f", b"data")])
            self.assertIn("too old", cm.exception.slogan)
        finally:
            os.path.ALLOW_MISSING = allowMissing
        self.assertFalse(os.path.lexists(os.path.join(self.ws, "f")))

    # Regular archive

    def testRegularArchive(self):
        self.extract([DIR("./x/"), DIR("d"), REG("d/f", b"data"), LNK("d/h", "d/f")])
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "x")))
        with open(os.path.join(self.ws, "d", "h"), "rb") as f:
            self.assertEqual(f.read(), b"data")
        self.assertVictimUntouched()
