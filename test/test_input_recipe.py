
from unittest import TestCase
from unittest.mock import MagicMock
import os

from bob.errors import ParseError
from bob.input import Recipe
from bob.stringparser import Env

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


class TestRelocatable(TestCase):

    def parseAndPrepare(self, name, recipe, classes={}, allRelocatable=None):

        cwd = os.getcwd()
        recipeSet = MagicMock()
        recipeSet.loadBinary = MagicMock()
        recipeSet.getPolicy = lambda x: allRelocatable if x == 'allRelocatable' else None

        cc = { n : Recipe(recipeSet, r, n+".yaml", cwd, n, n, {}, False)
            for n, r in classes.items() }
        recipeSet.getClass = lambda x, cc=cc: cc[x]

        r = recipe.copy()
        r["root"] = True
        ret = Recipe(recipeSet, recipe, name+".yaml", cwd, name, name, {})
        ret.resolveClasses()
        return ret.prepare(Env(), False, {})[0].refDeref([], {}, None, None)

    def testNormalRelocatable(self):
        """Normal recipes are relocatable by default"""

        recipe = {
            "packageScript" : "asdf"
        }
        p = self.parseAndPrepare("foo", recipe)
        self.assertTrue(p.isRelocatable())

    def testToolsNonRelocatable(self):
        """Recipes providing tools are not relocatable by default"""

        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            }
        }
        p = self.parseAndPrepare("foo", recipe)
        self.assertFalse(p.isRelocatable())

    def testCheckoutAndBuildStep(self):
        """Checkout and build steps are never relocatable"""

        recipe = {
            "checkoutScript" : "asdf",
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        p = self.parseAndPrepare("foo", recipe)
        self.assertFalse(p.getCheckoutStep().isRelocatable())
        self.assertFalse(p.getBuildStep().isRelocatable())
        self.assertTrue(p.getPackageStep().isRelocatable())

    def testToolRelocatable(self):
        """Test that tool can be marked relocable"""

        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            },
            "relocatable" : True
        }
        p = self.parseAndPrepare("foo", recipe)
        self.assertTrue(p.isRelocatable())

    def testNotRelocatable(self):
        """Normal recipes can be marked as not relocatable"""

        recipe = {
            "packageScript" : "asdf",
            "relocatable" : False
        }
        p = self.parseAndPrepare("foo", recipe)
        self.assertFalse(p.isRelocatable())

    def testClassCanSetRelocatable(self):
        """Classes can set relocatable flag too"""

        # single inheritence
        recipe = {
            "inherit" : [ "bar" ]
        }
        classes = {
            "bar" : {
                "relocatable" : False
            }
        }
        p = self.parseAndPrepare("foo", recipe, classes)
        self.assertFalse(p.isRelocatable())

        # two-stage inheritence
        classes = {
            "bar" : {
                "inherit" : [ "baz" ],
            },
            "baz" : {
                "relocatable" : False,
            }
        }
        p = self.parseAndPrepare("foo", recipe, classes)
        self.assertFalse(p.isRelocatable())

    def testClassOverride(self):
        """Inheriting recipe/class overrides inherited relocatable property"""

        # two-stage inheritence
        recipe = {
            "inherit" : [ "bar" ],
        }
        classes = {
            "bar" : {
                "inherit" : [ "baz" ],
                "relocatable" : False,
            },
            "baz" : {
                "relocatable" : True,
            }
        }
        p = self.parseAndPrepare("foo", recipe, classes)
        self.assertFalse(p.isRelocatable())

        # recipe overrides classes
        recipe = {
            "inherit" : [ "bar" ],
            "relocatable" : True,
        }
        p = self.parseAndPrepare("foo", recipe, classes)
        self.assertTrue(p.isRelocatable())

    def testAllRelocatablePolicy(self):
        """Setting allRelocatable policy will make all packages relocatable"""

        # normal package
        recipe = {
            "packageScript" : "asdf"
        }
        p = self.parseAndPrepare("foo", recipe, allRelocatable=True)
        self.assertTrue(p.isRelocatable())

        # tool package
        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            }
        }
        p = self.parseAndPrepare("foo", recipe, allRelocatable=True)
        self.assertTrue(p.isRelocatable())
