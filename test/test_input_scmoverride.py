# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase
import yaml

from bob.errors import ParseError
from bob.input import ScmOverride
from bob.stringparser import Env

class TestScmOverride(TestCase):

    def setUp(self):
        self.scm = {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        }

    def testDel(self):
        """Test to delete a key"""
        o = ScmOverride({ 'del' : [ 'branch' ] })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
        })

        o = ScmOverride({ 'del' : [ "${DEL}" ] })
        e = Env({"DEL" : "branch"})
        match, scm = o.mangle(self.scm, e)
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
        })
    def testAdd(self):
        """Test to add a new key"""
        o = ScmOverride({ 'set' : { 'commit' : '1234' } })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop",
            'commit' : "1234"
        }
        )
        o = ScmOverride({ 'set' : { 'commit' : "${COMMIT}" } })
        e = Env({"COMMIT" : "4321"})
        match, scm = o.mangle(self.scm, e)
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop",
            'commit' : "4321"
        })
    def testOverwrite(self):
        """Test to overwrite existing key"""
        o = ScmOverride({ 'set' : { 'branch' : "master" } })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

        # test substitution
        o = ScmOverride({'set' : { 'branch' : "${BRANCH}" } })
        e = Env({"BRANCH" : "master"})
        match, scm = o.mangle(self.scm, e)
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

    def testReplace(self):
        """Test replacement via regex"""
        o = ScmOverride({
            'replace' : {
                'url' : {
                    'pattern'     : "@.*:",
                    'replacement' : "@acme.test:"
                }
            }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@acme.test:foo/bar.git",
            'branch' : "develop"
        })

    def testReplaceInvalid(self):
        """Test that invalid regexes are handled gracefully"""
        with self.assertRaises(ParseError):
            o = ScmOverride({
                'replace' : {
                    'url' : {
                        'pattern'     : "*",
                        'replacement' : "foo"
                    }
                }
            })
            o.mangle(self.scm, Env())

    def testMatch(self):
        """Test matching (multiple) keys"""

        # match single key
        o = ScmOverride({
            'match' : { 'branch' : "develop" },
            'set' : { 'branch' : "master" }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

        # mismatch single key
        o = ScmOverride({
            'match' : { 'branch' : "upstream" },
            'set' : { 'branch' : "master" }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        })

        # match multiple keys
        o = ScmOverride({
            'match' : {
                'branch' : "develop",
                'url' : "git@git.com:foo/bar.git",
            },
            'set' : { 'branch' : "master" }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })

        # mismatch one out of two keys
        o = ScmOverride({
            'match' : {
                'branch' : "develop",
                'url' : "asdfadgag",
            },
            'set' : { 'branch' : "master" }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "develop"
        })

        # test substitution
        o = ScmOverride({'match' : {
                'url' : "git@${SERVER}:foo/${NAME}.git",
            },
            'set' : { 'branch' : "master" }
        })
        e = Env({"SERVER" : "git.com", "NAME" : "bar"})
        match, scm = o.mangle(self.scm, e)
        self.assertEqual(match, True)
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "git@git.com:foo/bar.git",
            'branch' : "master"
        })


    def testMatchGlob(self):
        """Test that matching uses globbing"""
        o = ScmOverride({
            'match' : { 'url' : "*git.com*" },
            'set' : { 'url' : "mirror" }
        })
        match, scm = o.mangle(self.scm, Env())
        self.assertEqual(scm, {
            'scm' : "git",
            'url' : "mirror",
            'branch' : "develop"
        })

    def testDump(self):
        """Test that a scmOverride correctly converts back to yaml"""
        spec = {
            'match' : { 'url' : "*git.com*" },
            'set' : { 'url' : "mirror", "branch" : "feature" },
            'del' : [ 'tag', 'commit' ],
            'replace' : {
                "url" : {
                    "pattern" : "pattern",
                    "replacement" : "replacement",
                }
            }
        }

        o = ScmOverride(spec)
        self.assertEqual(spec, yaml.load(str(o), Loader=yaml.Loader))
