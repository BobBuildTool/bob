# Bob build tool
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise overlay recovery through real checkout state and recipe caching."""

from unittest import TestCase
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile

import yaml


class TestPatchCheckout(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bobRoot = Path(__file__).resolve().parents[2]
        self.env = {**os.environ, 'PYTHONPATH': str(self.bobRoot / 'pym')}
        (self.root / 'recipes').mkdir()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        for name in ('message', 'second'):
            (self.repo / name).write_text('old\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.archive = self.root / 'source.tar'
        with tarfile.open(self.archive, 'w') as archive:
            for name in ('message', 'second'):
                archive.add(self.repo / name, arcname=name)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo,
                                       stderr=subprocess.PIPE, text=True)

    def patch(self, name, before, after, target='message'):
        (self.root / 'recipes' / name).write_text(
            '--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-{1}\n+{2}\n'.format(
                target, before, after))

    def recipe(self, kind, patches, script='true'):
        if kind == 'git':
            scm = {'scm': 'git', 'url': self.repo.as_uri(), 'commit': self.commit}
        else:
            scm = {'scm': 'url', 'url': self.archive.as_uri(),
                   'digestSHA256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                   'extract': 'tar'}
        scm.update(dir='src', patches=patches)
        (self.root / 'recipes' / 'root.yaml').write_text(yaml.safe_dump({
            'root': True, 'checkoutSCM': scm, 'checkoutScript': script,
            'checkoutDeterministic': True, 'buildScript': 'true', 'packageScript': 'true',
        }))

    def bob(self, success=True):
        result = subprocess.run([sys.executable, str(self.bobRoot / 'bob'),
                                 '--debug=ngd', 'dev', '--checkout-only', 'root'],
                                cwd=self.root, env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def workspace(self):
        return next((self.root / 'dev' / 'src' / 'root').glob('*/workspace'))

    def test_git_failed_stack_retries_corrected_patch_on_retained_base(self):
        self.check_failed_stack_retry('git')

    def test_url_failed_stack_retries_corrected_patch_on_retained_base(self):
        self.check_failed_stack_retry('url')

    def check_failed_stack_retry(self, kind):
        self.patch('first.patch', 'old', 'first')
        self.patch('second.patch', 'wrong', 'second', 'second')
        self.recipe(kind, [{'file': 'first.patch'}, {'file': 'second.patch'}])
        self.bob(False)
        workspace = self.workspace()
        self.assertEqual((workspace / 'src' / 'message').read_text(), 'old\n')
        self.patch('second.patch', 'old', 'second', 'second')
        self.bob()
        self.assertEqual((workspace / 'src' / 'message').read_text(), 'first\n')
        self.assertEqual((workspace / 'src' / 'second').read_text(), 'second\n')
        self.assertFalse((workspace.parent / 'attic').exists())
        self.bob()

    def test_unverified_failed_checkout_moves_to_attic(self):
        self.patch('fix.patch', 'wrong', 'patched')
        self.recipe('git', [{'file': 'fix.patch'}])
        self.bob(False)
        workspace = self.workspace()
        (workspace / 'src' / 'message').write_text('partial legacy overlay\n')
        relative = str(workspace.relative_to(self.root))
        subprocess.run([sys.executable, '-c',
                        'from bob.state import BobState, finalize; '
                        'import sys; s=BobState(); d=s.getDirectoryState(sys.argv[1], True); '
                        'd.pop(4, None); s.setDirectoryState(sys.argv[1], d); finalize()',
                        relative], cwd=self.root, env=self.env, check=True)
        self.patch('fix.patch', 'old', 'patched')
        self.bob()
        self.assertEqual((workspace / 'src' / 'message').read_text(), 'patched\n')
        self.assertTrue((workspace.parent / 'attic').exists())
        saved = next((workspace.parent / 'attic').glob('*/message'))
        self.assertEqual(saved.read_text(), 'partial legacy overlay\n')

    def test_series_reordering_invalidates_recipe_cache(self):
        self.patch('first.patch', 'old', 'first')
        self.patch('second.patch', 'first', 'second')
        series = self.root / 'recipes' / 'series'
        series.write_text('first.patch\nsecond.patch\n')
        self.recipe('git', [{'series': 'series'}])
        self.bob()
        series.write_text('second.patch\nfirst.patch\n')
        output = self.bob(False)
        self.assertIn('second.patch', output)

    def test_checkout_script_change_with_same_scm_reruns_checkout(self):
        self.recipe('git', [], 'echo first > stamp')
        self.bob()
        workspace = self.workspace()
        self.assertEqual((workspace / 'stamp').read_text(), 'first\n')
        self.recipe('git', [], 'echo second > stamp')
        self.bob()
        self.assertEqual((workspace / 'stamp').read_text(), 'second\n')
