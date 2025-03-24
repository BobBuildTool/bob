# Bob build tool
# Copyright (C) 2025  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import Mock, patch
from collections import namedtuple
import datetime

from bob.audit import Artifact

Uname = namedtuple('Uname', ['system', 'node', 'release', 'version', 'machine',
                             'processor'])

def fixedUname():
    return Uname('Linux', 'Bob', '0.8.15', 'stable', 'x86_64', '')

fixedTime = datetime.datetime(2025, 3, 21, 8, 0)

class TestArtifact(TestCase):

    @patch('platform.uname', fixedUname)
    @patch.object(Artifact, '_Artifact__getOsRelease', return_value="asdf")
    def testStableArtifactId(self, a):
        a = Artifact(fixedTime)
        self.assertEqual(a.getId(), b'W\x1fr\xc6\xc63\xca\x133e\xb9\xc5V\xc9\x81\xad\xb9\xb1\xc0\xa0')

        a = Artifact(fixedTime)
        a.addDefine("A", "B")
        a.addArg(b'\x11' * 20)
        self.assertEqual(a.getId(), b'\x03\x14\x19\x93%4-?\x9b\x10\xf9\xcco\x1b\xb3KP\xfa\x03\x11')

        a = Artifact(fixedTime)
        a.addTool("bob", b'\x12' * 20)
        self.assertEqual(a.getId(), b'F\n\xe9?\xf6HX\x01\xe1"\xdbM\xae\x8e\x81(\xe9:S\xba')

        a = Artifact(fixedTime)
        a.setSandbox(b'\x13' * 20)
        self.assertEqual(a.getId(), b'\xd5\xd1d\x1c\x1d\x81\x06\xa1\xcf\xf4\x10\x1c\xce\xec\xdae)Pr\x96')
