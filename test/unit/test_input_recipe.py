
from unittest import TestCase
from unittest.mock import MagicMock
import os

from bob.errors import ParseError
from bob.input import Recipe
from bob.languages import ScriptLanguage
from bob.stringparser import Env, IfExpression, DEFAULT_STRING_FUNS

class TestDependencies(TestCase):

    def cmpEntry(self, entry, name, env={}, fwd=False, use=["result", "deps"],
                 cond=None, checkoutDep=False):
        self.assertEqual(entry.recipe, name)
        self.assertEqual(entry.envOverride, env)
        self.assertEqual(entry.provideGlobal, fwd)
        self.assertEqual(entry.useEnv, "environment" in use)
        self.assertEqual(entry.useTools, "tools" in use)
        self.assertEqual(entry.useBuildResult, "result" in use)
        self.assertEqual(entry.useDeps, "deps" in use)
        self.assertEqual(entry.useSandbox, "sandbox" in use)
        self.assertEqual(entry.condition, cond)
        self.assertEqual(entry.checkoutDep, checkoutDep)

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
        self.cmpEntry(res[1], "b", cond=["cond1"])
        self.cmpEntry(res[2], "c", cond=["cond1","cond2"])
        self.cmpEntry(res[3], "d", cond=["cond1"])
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

    def testNestedCheckoutDep(self):
        deps = [
            "a",
            {
                "checkoutDep" : True,
                "depends" : [
                    "b",
                    {
                        "checkoutDep" : False,
                        "depends" : [ "c" ]
                    },
                    "d"
                ]
            },
            "e",
            {
                "name" : "f",
                "checkoutDep" : True,
            }
        ]
        res = list(Recipe.Dependency.parseEntries(deps))

        self.assertEqual(len(res), 6)
        self.cmpEntry(res[0], "a")
        self.cmpEntry(res[1], "b", checkoutDep=True)
        self.cmpEntry(res[2], "c")
        self.cmpEntry(res[3], "d", checkoutDep=True)
        self.cmpEntry(res[4], "e")
        self.cmpEntry(res[5], "f", checkoutDep=True)


class RecipeCommon:

    SCRIPT_LANGUAGE = ScriptLanguage.BASH

    def applyRecipeDefaults(self, recipe):
        r = recipe.copy()
        r.setdefault("checkoutUpdateIf", False)
        return r

    def parseAndPrepare(self, recipe, classes={}, allRelocatable=None, name="foo"):

        cwd = os.getcwd()
        recipeSet = MagicMock()
        recipeSet.loadBinary = MagicMock()
        recipeSet.scriptLanguage = self.SCRIPT_LANGUAGE
        recipeSet.getPolicy = lambda x: allRelocatable if x == 'allRelocatable' else None

        cc = { n : Recipe(recipeSet, self.applyRecipeDefaults(r), [], n+".yaml",
                          cwd, n, n, {}, False)
            for n, r in classes.items() }
        recipeSet.getClass = lambda x, cc=cc: cc[x]

        env = Env()
        env.funs = DEFAULT_STRING_FUNS
        ret = Recipe(recipeSet, self.applyRecipeDefaults(recipe), [], name+".yaml",
                     cwd, name, name, {})
        ret.resolveClasses(env)
        return ret.prepare(env, False, {})[0].refDeref([], {}, None, None)


class TestRelocatable(RecipeCommon, TestCase):

    def testNormalRelocatable(self):
        """Normal recipes are relocatable by default"""

        recipe = {
            "packageScript" : "asdf"
        }
        p = self.parseAndPrepare(recipe)
        self.assertTrue(p.isRelocatable())

    def testToolsNonRelocatable(self):
        """Recipes providing tools are not relocatable by default"""

        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            }
        }
        p = self.parseAndPrepare(recipe)
        self.assertFalse(p.isRelocatable())

    def testCheckoutAndBuildStep(self):
        """Checkout and build steps are never relocatable"""

        recipe = {
            "checkoutScript" : "asdf",
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        p = self.parseAndPrepare(recipe)
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
        p = self.parseAndPrepare(recipe)
        self.assertTrue(p.isRelocatable())

    def testNotRelocatable(self):
        """Normal recipes can be marked as not relocatable"""

        recipe = {
            "packageScript" : "asdf",
            "relocatable" : False
        }
        p = self.parseAndPrepare(recipe)
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
        p = self.parseAndPrepare(recipe, classes)
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
        p = self.parseAndPrepare(recipe, classes)
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
        p = self.parseAndPrepare(recipe, classes)
        self.assertFalse(p.isRelocatable())

        # recipe overrides classes
        recipe = {
            "inherit" : [ "bar" ],
            "relocatable" : True,
        }
        p = self.parseAndPrepare(recipe, classes)
        self.assertTrue(p.isRelocatable())

    def testAllRelocatablePolicy(self):
        """Setting allRelocatable policy will make all packages relocatable"""

        # normal package
        recipe = {
            "packageScript" : "asdf"
        }
        p = self.parseAndPrepare(recipe, allRelocatable=True)
        self.assertTrue(p.isRelocatable())

        # tool package
        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            }
        }
        p = self.parseAndPrepare(recipe, allRelocatable=True)
        self.assertTrue(p.isRelocatable())


class TestCheckoutUpdateIf(RecipeCommon, TestCase):

    def testDefault(self):
        """By default checkoutUpdateIf is 'False'"""

        recipe = {
            "checkoutScript" : "asdf",
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertFalse(c.getUpdateScript())
        self.assertTrue(c.isUpdateDeterministic())

    def testSimpleBool(self):
        """checkoutUpdateIf can be a boolean value"""
        recipe = {
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : True,
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertIn("asdf", c.getUpdateScript())

    def testSimpleIfString(self):
        """checkoutUpdateIf can be a boolean string value"""
        recipe = {
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : "$(eq,a,b)",
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertNotIn("asdf", c.getUpdateScript())

        recipe["checkoutUpdateIf"] = "$(eq,a,a)"
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertIn("asdf", c.getUpdateScript())

    def testSimpleIfExpr(self):
        """checkoutUpdateIf can be an IfExpression value"""
        recipe = {
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : IfExpression('"a" == "b"'),
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertNotIn("asdf", c.getUpdateScript())

        recipe["checkoutUpdateIf"] = IfExpression('"a" == "a"')
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertIn("asdf", c.getUpdateScript())

    def testDeterministicDefault(self):
        """checkoutDeterministic applies for updates too"""
        recipe = {
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : True,
            "buildScript" : "asdf",
            "packageScript" : "asdf",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertFalse(c.isUpdateDeterministic())

        recipe["checkoutDeterministic"] = True
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        self.assertTrue(c.isUpdateDeterministic())

    def testDeterministicInherit(self):
        """checkoutDeterministic applies only for selected update parts"""

        recipe = {
            "inherit" : [ "bar" ],
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : True,
            "checkoutDeterministic" : True,
            "buildScript" : "asdf",
        }
        classes = {
            "bar" : {
                "checkoutScript" : "qwer",
                "checkoutUpdateIf" : False,
                "checkoutDeterministic" : False,
                "buildScript" : "qwer",
            },
        }

        c = self.parseAndPrepare(recipe, classes).getCheckoutStep()
        self.assertTrue(c.isUpdateDeterministic())

    def testInherit(self):
        """Only enabled checkoutScript takes part in update"""

        recipe = {
            "inherit" : [ "bar" ],
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : True,
            "checkoutDeterministic" : True,
            "buildScript" : "asdf",
        }
        classes = {
            "bar" : {
                "inherit" : [ "baz" ],
                "checkoutScript" : "qwer",
                "checkoutUpdateIf" : False,
                "checkoutDeterministic" : False,
                "buildScript" : "qwer",
            },
            "baz" : {
                "relocatable" : True,
                "checkoutScript" : "yxcv",
                "checkoutUpdateIf" : True,
                "buildScript" : "qwer",
            },
        }

        c = self.parseAndPrepare(recipe, classes).getCheckoutStep()
        self.assertFalse(c.isUpdateDeterministic())
        self.assertIn("asdf", c.getUpdateScript())
        self.assertNotIn("qwer", c.getUpdateScript())
        self.assertIn("yxcv", c.getUpdateScript())

    def testInheritNullNotEnabled(self):
        """A null checkoutUpdateIf does not yet enable the update script"""
        recipe = {
            "inherit" : [ "bar" ],
            "checkoutScript" : "asdf",
            "buildScript" : "asdf",
        }
        classes = {
            "bar" : {
                "checkoutScript" : "qwer",
                "checkoutUpdateIf" : None,
                "buildScript" : "qwer",
            },
        }

        c = self.parseAndPrepare(recipe, classes).getCheckoutStep()
        self.assertNotIn("asdf", c.getUpdateScript())
        self.assertNotIn("qwer", c.getUpdateScript())

    def testInheritNullEnabled(self):
        """A null checkoutUpdateIf is included if enabled elsewhere"""
        recipe = {
            "inherit" : [ "bar" ],
            "checkoutScript" : "asdf",
            "checkoutUpdateIf" : True,
            "buildScript" : "asdf",
        }
        classes = {
            "bar" : {
                "checkoutScript" : "qwer",
                "checkoutUpdateIf" : None,
                "buildScript" : "qwer",
            },
        }

        c = self.parseAndPrepare(recipe, classes).getCheckoutStep()
        self.assertIn("asdf", c.getUpdateScript())
        self.assertIn("qwer", c.getUpdateScript())
