
from unittest import TestCase
from bob.builder import InternalJobServer, JobServerSemaphore
from bob.utils import runInEventLoop
import asyncio

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
