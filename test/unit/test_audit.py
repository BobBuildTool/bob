# Bob build tool
# Copyright (C) 2025  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
from unittest.mock import Mock, patch
from collections import namedtuple
import asyncio
import datetime
import os
import tempfile
import subprocess

from bob.audit import Artifact
from bob.scm import auditFromData
from bob.utils import asHexStr, hashDirectory

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

    def testPatchAuditIncludesPatchedTreeFingerprint(self):
        with tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(workspace, 'archive'), 'w') as f:
                f.write('patched source')
            artifact = Artifact(fixedTime)
            asyncio.run(artifact.addScm('url', workspace, 'archive', {
                'url': 'file:///tmp/archive', 'patchDirectory': '.',
                'patches': [{'path': 'fix.patch', 'strip': 1,
                             'digestSHA256': '0' * 64}],
            }))
            scm = artifact.dump()['scms'][0]
            self.assertEqual(scm['patches'][0]['path'], 'fix.patch')
            self.assertEqual(scm['patchFingerprint'],
                             asHexStr(hashDirectory(workspace)))

    def testPatchedGitAuditRoundTrip(self):
        with tempfile.TemporaryDirectory() as workspace:
            def git(*args):
                subprocess.run(['git', *args], cwd=workspace, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git('init')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            with open(os.path.join(workspace, 'message'), 'w') as f:
                f.write('base\n')
            git('add', 'message')
            git('commit', '-m', 'base')
            with open(os.path.join(workspace, 'message'), 'w') as f:
                f.write('patched\n')
            artifact = Artifact(fixedTime)
            asyncio.run(artifact.addScm('git', workspace, '.', {
                'patchDirectory': '.', 'patches': [{
                    'path': 'fix.patch', 'strip': 1, 'digestSHA256': '0' * 64}],
            }))
            data = artifact.dump()
            self.assertEqual(Artifact.fromData(data).dump(), data)
            scm = data['scms'][0]
            self.assertEqual(scm['patches'][0]['path'], 'fix.patch')
            self.assertEqual(scm['patchFingerprint'],
                             asHexStr(hashDirectory(workspace, ignoreDirs=['.git', '.svn', 'CVS'])))
            legacy = {k: v for k, v in scm.items()
                      if k not in ('patches', 'patchFingerprint')}
            self.assertEqual(auditFromData(legacy).dump(), legacy)
