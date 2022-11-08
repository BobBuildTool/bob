# Bob build tool
# Copyright (C) 2022  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from unittest import TestCase

from bob.input import RecipeSet
from bob.errors import BobError

class QueryMixin:
    MODE = "nullglob"

    @classmethod
    def setUpClass(cls):
        recipes = RecipeSet()
        recipes._queryMode = cls.MODE
        recipes.parse(recipesRoot="data/pathspec")
        cls.packages = recipes.generatePackages(lambda x,y: "unused")

    def q(self, path, exp):
        """Execute query and compare package set"""
        res = set(p.getName() for p in self.packages.queryPackagePath(path))
        self.assertEqual(exp, res)

    def f(self, path):
        """Execute query and expect it to fail"""
        with self.assertRaises(BobError):
            self.packages.queryPackagePath(path)

class TestPathSpec(QueryMixin, TestCase):

    def testAxis(self):
        """Test regular forward axis"""
        self.q("root", {"root"})
        self.q("root/descendant@*", {"level1a", "level1b", "level2"})
        self.q("root/descendant-or-self@*", {"root", "level1a", "level1b", "level2"})
        self.q("root/child@*", {"level1a", "level1b", "level2"})
        self.q("root/direct-child@*", {"level1a", "level1b"})
        self.q("root/direct-descendant@*", {"level1a", "level1b", "level2"})
        self.q("root/direct-descendant-or-self@*", {"root", "level1a", "level1b", "level2"})

    def testPredicateFunc(self):
        """Test predicates with function calls"""
        self.q('*[eq("foo", "foo")]', {"root"})
        self.q('*["$(eq,foo,foo)"]', {"root"})

        self.q('*[eq("foo", "bar")]', set())
        self.q('*["$(eq,foo,bar)"]', set())

    def testPredicateBool(self):
        """Test boolean expressions in predicates"""
        self.q('*["false"]', set())
        self.q('*[!"false"]', {"root"})

        self.q('*["false" || "false"]', set())
        self.q('*["true" || "false"]', {"root"})
        self.q('*["false" || "true"]', {"root"})
        self.q('*["true" || "true"]', {"root"})

        self.q('*["false" && "false"]', set())
        self.q('*["false" && "true"]', set())
        self.q('*["true" && "false"]', set())
        self.q('*["true" && "true"]', {"root"})

        self.q('*[!"false" || "false"]', {"root"})
        self.q('*[!("false" || "false")]', {"root"})
        self.q('*["false" && !"false"]', set())
        self.q('*[!"false" && !"false"]', {"root"})
        self.q('*[!("false" && !"false")]', {"root"})

    def testPredicateComparison(self):
        """Test comparison operators in predicate"""
        self.q('*["  foo " == "foo"]', set())
        self.q('*[strip("  foo ") == "foo"]', {"root"})

        self.q('*[strip("foo") != "foo"]', set())
        self.q('*["  foo " != "foo"]', {"root"})

        self.q('*["b" < "a"]', set())
        self.q('*["a" < "a"]', set())
        self.q('*["a" < "b"]', {"root"})

        self.q('*["b" <= "a"]', set())
        self.q('*["a" <= "a"]', {"root"})
        self.q('*["a" <= "b"]', {"root"})

        self.q('*["b" > "a"]', {"root"})
        self.q('*["a" > "a"]', set())
        self.q('*["a" > "b"]', set())

        self.q('*["b" >= "a"]', {"root"})
        self.q('*["a" >= "a"]', {"root"})
        self.q('*["a" >= "b"]', set())

    def testPredicatePath(self):
        """Test paths in predicates"""
        self.q('//*[./level2]', {"root", "level1a", "level1b"})
        self.q('//*[direct-child@level2]', {"level1a", "level1b"})
        self.q('//*[descendant@level*a]', {"root"})
        self.q('//*[direct-descendant@level*a]', {"root"})
        self.q('//*[descendant-or-self@level*a]', {"root", "level1a"})
        self.q('//*[direct-descendant-or-self@level*a]', {"root", "level1a"})
        self.q('/root[/level*]', set())
        self.q('/root[/root]', {"root"})
        self.q('/root[./*["$LEVEL" == "1a"]]', {"root"})

    def testComplex(self):
        self.q('descendant@level*["true"]/child@*["$LEVEL" == "2"]', {"level2"})

    def testBrokenSyntax(self):
        """Test unparsable paths"""
        self.f('asdf[qwer')
        self.f('root/unknown@*')
        self.f('root/unknown@*/aaaaaaaaaaaaaa/bbbbbbbbbbbb/ccccccccccccc/dddddddddddd/eeeeeeeeee')
        self.f('aaaaaa/bbbbbbb/cccccccccc/dddddddddd/eeeeeeeee/unknown@*/fffffffffff/bbbbbbbbbbbb')

    def testBrokenUsage(self):
        self.f('root[invalid-function()]')
        self.f('*[./foo == "bar"]')
        self.f('*[!"foo" == "bar"]')
        self.f('*[("foo" || "bar") == "baz"]')
        self.f('*[("foo" < "bar") == "baz"]')

class TestNullSet(QueryMixin, TestCase):
    MODE = "nullset"

    def testOk(self):
        self.q('/root', {"root"})
        self.q('/root/level*', {"level1a", "level1b", "level2"})
        self.q('/root/foo*', set())
        self.q('/*/foo', set())
        self.q('/root["true"]/non-existing', set())
        self.q('/root[./foo]', set())
        self.q('/non-existing', set())

class TestNullGlob(QueryMixin, TestCase):
    MODE = "nullglob"

    def testOk(self):
        self.q('/root', {"root"})
        self.q('/root/level*', {"level1a", "level1b", "level2"})
        self.q('/root/foo*', set())
        self.q('/*/foo', set())
        self.q('/root["true"]/non-existing', set())
        self.q('/root[./foo]', set())

    def testFail(self):
        self.f('/non-existing')

class TestNullFail(QueryMixin, TestCase):
    MODE = "nullfail"

    def testOk(self):
        self.q('/root', {"root"})
        self.q('/root/level*', {"level1a", "level1b", "level2"})

    def testFail(self):
        self.f('/root/foo*')
        self.f('/non-existing')
        self.f('/root["true"]/non-existing')
        self.f('/root["true"]/descendant@non-existing')
        self.f('/root[!"true"]')
        self.f('/root[!("false" || "true")]')
        self.f('/root[!strip("true")]')
        self.f('/root["a" > "b"]')
        self.f('/root[./foo]')
