from bob.builder import InternalJobServer, JobServerSemaphore, ExternalJobServer, InternalJobServer
from bob.errors import BuildError
from bob.invoker import JobserverConfig
from bob.utils import runInEventLoop
from unittest import TestCase, skipIf
from unittest.mock import patch
import asyncio
import os
import sys
import tempfile

class TestJobServer:
    def __init__(self):
        self.__jobServer = InternalJobServer (2)
        self.__jobServerSem = JobServerSemaphore(self.__jobServer.getMakeFd(), False)

    async def testAcquire (self, cnt = 1):
        while cnt != 0:
            await self.__jobServerSem.acquire()
            cnt -= 1

    def release(self):
        self.__jobServerSem.release()

# Migrate to unittest.IsolatedAsyncioTestCase starting with Python 3.8
@skipIf(sys.platform == "win32", "Requires POSIX platform")
class TestJobserverTest (TestCase):
    def testAcquire(self):
        runInEventLoop(self._testAcquire())

    async def _testAcquire(self):
        t = TestJobServer()

        await t.testAcquire()
        t.release()

        await t.testAcquire(2)
        t.release()
        t.release()

    def testReleaseToOften(self):
        runInEventLoop(self._testReleaseToOften())

    async def _testReleaseToOften(self):
        t = TestJobServer()
        with self.assertRaises(ValueError):
            t.release()


@skipIf(sys.platform == "win32", "Requires POSIX platform")
class TestExternalJobServer(TestCase):
    def testInvalidFifo(self):
        with self.assertRaises(BuildError):
            ExternalJobServer(JobserverConfig.fromFifo(3, "/does/not/exist"))

        with self.assertRaises(BuildError):
            with tempfile.NamedTemporaryFile() as tmp:
                ExternalJobServer(JobserverConfig.fromFifo(3, tmp.name))

    def testInvalidPipe(self):
        # both file descriptors
        with self.assertRaises(BuildError):
            ExternalJobServer(JobserverConfig.fromPipe(3, 456, 789))

        # just one invalid descriptor
        r, w = os.pipe()
        with self.assertRaises(BuildError):
            ExternalJobServer(JobserverConfig.fromPipe(3, 456, 789))
        with self.assertRaises(BuildError):
            ExternalJobServer(JobserverConfig.fromPipe(3, r, 789))
        with self.assertRaises(BuildError):
            ExternalJobServer(JobserverConfig.fromPipe(3, 456, w))

        os.close(r)
        os.close(w)

@skipIf(sys.platform == "win32", "Requires POSIX platform")
class TestInternalJobServer(TestCase):
    def testFifoCreateFailed(self):
        with patch('os.mkfifo') as f:
            f.side_effect = OSError("forbidden")
            with self.assertRaises(BuildError):
                InternalJobServer(3)
