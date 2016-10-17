
from unittest import TestCase
import os

from bob.input import Recipe
from bob.errors import ParseError

class TestDependencies(TestCase):

    def cmpEntry(self, entry, name, env={}, fwd=False, use=["result", "deps"], cond=None):
        self.assertEqual(entry.recipe, name)
        self.assertEqual(entry.envOverride, env)
        self.assertEqual(entry.provideGlobal, fwd)
        self.assertEqual(entry.useEnv, "environment" in use)
        self.assertEqual(entry.useTools, "tools" in use)
        self.assertEqual(entry.useBuildResult, "result" in use)
        self.assertEqual(entry.useDeps, "deps" in use)
        self.assertEqual(entry.useSandbox, "sandbox" in use)
        self.assertEqual(entry.condition, cond)

    def testSimpleList(self):
        deps = [ "a", "b" ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 2)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b")

    def testMixedList(self):
        deps = [ "a", { "name" : "b", "environment" : { "foo" : "bar" }} ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 2)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", env={"foo" : "bar" })

    def testNestedList(self):
        deps = [
            "a",
            { "depends" : [
                "b",
                { "depends" : [ "c" ] }
            ]}
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 3)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b")
        self.cmpEntry(res[2], "c")

    def testNestedEnv(self):
        deps = [
            "a",
            {
                "environment" : { "foo" : "1", "bar" : "2" },
                "depends" : [
                    "b",
                    {
                        "environment" : { "bar" : "3", "baz" : "4" },
                        "depends" : [ "c" ]
                    },
                    "d"
                ]
            },
            "e"
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 5)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", env={"foo" : "1", "bar" : "2"})
        self.cmpEntry(res[2], "c", env={"foo" : "1", "bar" : "3", "baz" : "4"})
        self.cmpEntry(res[3], "d", env={"foo" : "1", "bar" : "2"})
        self.cmpEntry(res[4], "e")

    def testNestedIf(self):
        deps = [
            "a",
            {
                "if" : "cond1",
                "depends" : [
                    "b",
                    {
                        "if" : "cond2",
                        "depends" : [ "c" ]
                    },
                    "d"
                ]
            },
            "e"
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 5)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", cond="cond1")
        self.cmpEntry(res[2], "c", cond="$(and,cond1,cond2)")
        self.cmpEntry(res[3], "d", cond="cond1")
        self.cmpEntry(res[4], "e")

    def testNestedUse(self):
        deps = [
            "a",
            {
                "use" : [],
                "depends" : [
                    "b",
                    {
                        "use" : ["tools"],
                        "depends" : [ "c" ]
                    },
                    "d"
                ]
            },
            "e"
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 5)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", use=[])
        self.cmpEntry(res[2], "c", use=["tools"])
        self.cmpEntry(res[3], "d", use=[])
        self.cmpEntry(res[4], "e")

    def testNestedFwd(self):
        deps = [
            "a",
            {
                "forward" : True,
                "depends" : [
                    "b",
                    {
                        "forward" : False,
                        "depends" : [ "c" ]
                    },
                    "d"
                ]
            },
            "e"
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 5)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", fwd=True)
        self.cmpEntry(res[2], "c",)
        self.cmpEntry(res[3], "d", fwd=True)
        self.cmpEntry(res[4], "e")

