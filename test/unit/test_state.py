# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch
import os
import pickle
import stat
import sys

from bob.state import _BobState as BobState
from bob.errors import BobError

class BobStateWrap:
    """Small wrapper around _BobState that makes sure finalize() is always
    called"""
    def __init__(self):
        self.state = None

    def __enter__(self):
        self.state = BobState()
        return self.state

    def __exit__(self, exc_type, exc_value, traceback):
        self.state.finalize()
        return False

def makeDeleteable(p):
    for entry in os.scandir(p):
        if entry.is_dir():
            os.chmod(entry.path, stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR)
            makeDeleteable(entry.path)

def partialDump(obj, f):
    b = pickle.dumps(obj)
    f.write(b[:len(b)//2]) # write only half
    raise OSError()

class EmptyDir:
    def setUp(self):
        self._oldCwd = os.getcwd()
        self._tmpDir = TemporaryDirectory()
        os.chdir(self._tmpDir.name)

    def tearDown(self):
        os.chdir(self._oldCwd)
        makeDeleteable(self._tmpDir.name)
        self._tmpDir.cleanup()

    def unlock(self):
        os.unlink(".bob-state.lock")

    def writeState(self, version=BobState.CUR_VERSION):
        state = {
            "version" : version,
            "byNameDirs" : {},
            "results" : {},
            "inputs" : {},
            "jenkins" : {},
            "dirStates" : {},
            "buildState" : {},
            "variantIds" : {},
            "atticDirs" : {},
            "createdWithVersion" : version,
        }
        with open(".bob-state.pickle", "wb") as f:
            pickle.dump(state, f)


class TestLock(EmptyDir, TestCase):
    """Verify locking of project while Bob is running"""

    def testLocking(self):
        """Make sure lock is deleted after cleanup"""
        with BobStateWrap():
            with self.assertRaises(BobError):
                with BobStateWrap():
                    pass
        with BobStateWrap():
            pass

    def testCannotLock(self):
        """It's ok to be not able to create a lock file"""
        os.mkdir("ro")
        os.chmod("ro", stat.S_IRUSR|stat.S_IXUSR)
        os.chdir("ro")

        with BobStateWrap():
            pass
        self.assertFalse(os.path.exists(".bob-state.lock"))

    def testVanishedLock(self):
        """Test that we do not crash if lock file vanished"""
        with BobStateWrap():
            self.unlock()

    def testStickyLock(self):
        """Test that we do not crash if lock file cannot be removed"""
        os.mkdir("ro")
        os.chdir("ro")

        with BobStateWrap():
            os.chmod(".", stat.S_IRUSR|stat.S_IXUSR)


class TestPersistence(EmptyDir, TestCase):
    """Verify persistence of state"""

    def testPersistence(self):
        """Smoke test that tings are persisted"""
        with BobStateWrap() as s1:
            s1.setInputHashes("path", b"hash")

        with BobStateWrap() as s2:
            self.assertEqual(b"hash", s2.getInputHashes("path"))

    def testUncommitted(self):
        """Uncommitted state must be picked up on next run"""
        s1 = BobState()
        s1.setInputHashes("path", b"hash")

        # simulate hard crash
        self.unlock()
        del s1

        with BobStateWrap() as s2:
            self.assertEqual(b"hash", s2.getInputHashes("path"))

    def testCorrupt(self):
        """A corrupted uncommitted state must be discarded"""
        with BobStateWrap() as s1:
            s1.setInputHashes("path", b"hash")

        s2 = BobState()
        s2.setInputHashes("path", b"must-be-discarded")
        # simulate hard crash and corrupt state
        self.unlock()
        del s2
        with open(".bob-state.pickle.new", "r+b") as f:
            f.write(b"garbabe")

        with BobStateWrap() as s3:
            self.assertEqual(b"hash", s3.getInputHashes("path"))


class TestErrors(EmptyDir, TestCase):
    """Trigger various abnormal conditions that are normally not experienced"""

    def testUnreadable(self):
        """Test that unreadable state is handled gracefully"""

        self.writeState()
        os.chmod(".bob-state.pickle", 0)

        with self.assertRaises(BobError):
            s1 = BobState()

    def testCorrupted(self):
        """Test that corrupted state is handled gracefully"""
        with open(".bob-state.pickle", "wb") as f:
            f.write(b"garbabe")
        with self.assertRaises(BobError):
            s1 = BobState()

    def testTooOld(self):
        """Too old states must be rejected gracefully"""
        self.writeState(1)
        with self.assertRaises(BobError):
            s1 = BobState()

    def testTooYoung(self):
        """Too new states must be rejected gracefully"""
        self.writeState(99)
        with self.assertRaises(BobError):
            s1 = BobState()

    def testCannotSave(self):
        """Failure to save state aborts"""
        os.mkdir("ro")
        os.chdir("ro")

        self.writeState()
        with self.assertRaises(BobError):
            s1 = BobState()
            os.chmod(".", stat.S_IRUSR|stat.S_IXUSR)
            s1.setInputHashes("path", b"hash")

    def testCannotCommit(self):
        """Unability to commit state is handled gracefully"""
        os.mkdir("ro")
        os.chdir("ro")

        with BobStateWrap() as s1:
            s1.setInputHashes("path", b"hash")
            os.chmod(".", stat.S_IRUSR|stat.S_IXUSR)

        os.chmod(".", stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR)
        self.unlock()
        os.unlink(".bob-state.pickle.new")

        with BobStateWrap() as s2:
            self.assertEqual(None, s2.getInputHashes("path"))

    def testLastStateRetained(self):
        """The last successfully saved state is retained even if update fails"""
        with BobStateWrap() as s1:
            s1.setInputHashes("path", b"hash")

        with self.assertRaises(BobError):
            with BobStateWrap() as s2:
                with patch('pickle.dump', partialDump):
                    s2.setInputHashes("path", b"lost")

        with BobStateWrap() as s3:
            self.assertEqual(b"hash", s3.getInputHashes("path"))

    def testAsyncStateUpdateFails(self):
        """Failures in asynchronous updates are handled gracefully"""
        with BobStateWrap() as s1:
            s1.setInputHashes("path", b"hash")

        with self.assertRaises(BobError):
            with BobStateWrap() as s2:
                with patch('pickle.dump', partialDump):
                    s2.setAsynchronous()
                    s2.setInputHashes("path", b"lost1")
                    s2.setInputHashes("path", b"lost2")
                    s2.setSynchronous()

        with BobStateWrap() as s3:
            self.assertEqual(b"hash", s3.getInputHashes("path"))
