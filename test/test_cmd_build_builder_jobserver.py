
from unittest import TestCase
from bob.cmds.build.builder import InternalJobServer, JobServerSemaphore
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

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

class TestJobserverTest (TestCase):
    def setUp (self):
        self.__t = TestJobServer()

    def testAcquire(self):
        _run(self.__t.testAcquire())
        self.__t.release()
        _run(self.__t.testAcquire(2))
        self.__t.release()
        self.__t.release()

    def testReleaseToOften(self):
        with self.assertRaises(ValueError):
            self.__t.release()
