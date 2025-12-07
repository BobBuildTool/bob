from unittest import TestCase
from unittest.mock import MagicMock, patch

from bob.invoker import JobserverConfig

class TestJobserverConfig(TestCase):
    def expectEmpty(self, c):
        self.assertFalse(c)
        self.assertFalse(c.isPipe())
        self.assertFalse(c.isFifo())
        self.assertEqual(c.jobs(), 1)

    def testEmpty(self):
        c = JobserverConfig.fromMakeflags("")
        self.expectEmpty(c)

    def testParseInvalidMakeflags(self):
        """Test processing of various invalid MAKEFLAGS"""
        with patch('bob.invoker.invalidMakeflags') as warning:
            warning.show = MagicMock()
            c = JobserverConfig.fromMakeflags(" -jadsf")
            warning.show.assert_called()
            self.expectEmpty(c)

        with patch('bob.invoker.invalidMakeflags') as warning:
            warning.show = MagicMock()
            c = JobserverConfig.fromMakeflags(" -j2 --jobserver-auth=invalid")
            warning.show.assert_called()
            self.expectEmpty(c)

        with patch('bob.invoker.invalidMakeflags') as warning:
            warning.show = MagicMock()
            c = JobserverConfig.fromMakeflags(" -j2 --jobserver-auth=1,2,3")
            warning.show.assert_called()
            self.expectEmpty(c)

        with patch('bob.invoker.invalidMakeflags') as warning:
            warning.show = MagicMock()
            c = JobserverConfig.fromMakeflags(" -j2 --jobserver-auth=a,b")
            warning.show.assert_called()
            self.expectEmpty(c)

    def testLastWins(self):
        """Last --jobserver-auth= needs to win"""
        c = JobserverConfig.fromMakeflags("--jobserver-auth=1,2 -j8 --jobserver-auth=-2,-2")
        self.expectEmpty(c)

        c = JobserverConfig.fromMakeflags("--jobserver-auth=1,2 -j1 --jobserver-auth=3,4 -j2")
        self.assertTrue(c)
        self.assertTrue(c.isPipe())
        self.assertFalse(c.isFifo())
        self.assertEqual(c.jobs(), 2)
        self.assertEqual(c.pipeFds(), [3, 4])
