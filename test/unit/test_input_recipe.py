
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

    def parseAndPrepare(self, recipe, classes={}, name="foo", env={}):

        cwd = os.getcwd()
        recipeSet = MagicMock()
        recipeSet.loadBinary = MagicMock()
        recipeSet.scriptLanguage = self.SCRIPT_LANGUAGE
        recipeSet.getPolicy = lambda x: None

        cc = { n : Recipe(recipeSet, self.applyRecipeDefaults(r), [], n+".yaml",
                          cwd, n, n, {}, False)
            for n, r in classes.items() }
        recipeSet.getClass = lambda x, cc=cc: cc[x]

        env = Env(env)
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

    def testToolsRelocatable(self):
        """Recipes providing tools are relocatable by default"""

        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            }
        }
        p = self.parseAndPrepare(recipe)
        self.assertTrue(p.isRelocatable())

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

    def testToolNonRelocatable(self):
        """Test that tool can be marked as non-relocable"""

        recipe = {
            "packageScript" : "asdf",
            "provideTools" : {
                "foo" : "bar"
            },
            "relocatable" : False
        }
        p = self.parseAndPrepare(recipe)
        self.assertFalse(p.isRelocatable())

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


class TestSCMs(RecipeCommon, TestCase):

    def testAbsPath(self):
        """Absolute SCM paths are rejected"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "/absolute",
                },
            ],
            "buildScript" : "true",
        }
        with self.assertRaises(ParseError):
            self.parseAndPrepare(recipe)

    def testDifferentPath(self):
        """Multple SCMs in different directories are fine"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo",
                },
                {
                    "scm" : "svn",
                    "url" : "http://bob.test/test.svn",
                    "dir" : "bar",
                },
            ],
            "buildScript" : "true",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        l = c.getScmList()
        self.assertEqual(len(l), 2)
        self.assertEqual(l[0].getProperties(False)["scm"], "git")
        self.assertEqual(l[0].getDirectory(), "foo")
        self.assertEqual(l[1].getProperties(False)["scm"], "svn")
        self.assertEqual(l[1].getDirectory(), "bar")

    def testSamePath(self):
        """Multiple SCMs on same directory are rejected"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "same",
                },
                {
                    "scm" : "svn",
                    "url" : "http://bob.test/test.svn",
                    "dir" : "same",
                },
            ],
            "buildScript" : "true",
        }
        with self.assertRaises(ParseError):
            self.parseAndPrepare(recipe)

    def testNested(self):
        """Nested SCMs in upper-to-lower order are accepted"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo",
                },
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo/bar",
                },
            ],
            "buildScript" : "true",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        l = c.getScmList()
        self.assertEqual(len(l), 2)
        self.assertEqual(l[0].getDirectory(), "foo")
        self.assertEqual(l[1].getDirectory(), "foo/bar")

    def testNestedObstructs(self):
        """Nested SCMs that obstruct deeper SCMs are rejected"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo/bar",
                },
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo",
                },
            ],
            "buildScript" : "true",
        }
        with self.assertRaises(ParseError):
            self.parseAndPrepare(recipe)

    def testNestedJenkinsMixedPluginOk(self):
        """Nested non-plugin SCMs inside plugin SCMs are ok"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo",
                },
                {
                    "scm" : "cvs",
                    "url" : "http://bob.test/test.cvs",
                    "cvsroot" : "cvsroot",
                    "module" : "module",
                    "dir" : "foo/bar",
                },
            ],
            "buildScript" : "true",
        }
        c = self.parseAndPrepare(recipe).getCheckoutStep()
        l = c.getScmList()
        self.assertEqual(len(l), 2)
        self.assertEqual(l[0].getProperties(True)["scm"], "git")
        self.assertEqual(l[0].getDirectory(), "foo")
        self.assertTrue(l[0].hasJenkinsPlugin())
        self.assertEqual(l[1].getProperties(True)["scm"], "cvs")
        self.assertEqual(l[1].getDirectory(), "foo/bar")
        self.assertFalse(l[1].hasJenkinsPlugin())

    def testNestedJenkinsMixedPluginBad(self):
        """Nested plugin SCMs inside non-plugin SCMs are rejected"""
        recipe = {
            "checkoutSCM" : [
                {
                    "scm" : "cvs",
                    "url" : "http://bob.test/test.cvs",
                    "cvsroot" : "cvsroot",
                    "module" : "module",
                    "dir" : "foo",
                },
                {
                    "scm" : "git",
                    "url" : "http://bob.test/test.git",
                    "dir" : "foo/bar",
                },
            ],
            "buildScript" : "true",
        }
        with self.assertRaises(ParseError):
            self.parseAndPrepare(recipe)


class TestEnvironment(RecipeCommon, TestCase):

    def testMergeEnvironment(self):
        """The 'environment' and 'privateEnvironment' keys are merged during inheritence"""

        for key in ("environment", "privateEnvironment"):
            with self.subTest(key=key):
                recipe = {
                    "inherit" : ["a", "b"],
                    key : {
                        "A" : "<lib>${A:-}",
                        "B" : "<lib>${B:-}",
                        "C" : "<lib>${C:-}",
                    },
                    "packageVars" : ["A", "B", "C"]
                }
                classes = {
                    "a" : {
                        key : {
                            "A" : "${A:-}<a>",
                            "B" : "<a>",
                        },
                    },
                    "b" : {
                        key : {
                            "B" : "${B:-}<b>",
                            "C" : "<b>",
                        },
                    },
                }
                env = {
                    "A" : "a",
                    "B" : "b",
                }
                p = self.parseAndPrepare(recipe, classes, env=env).getPackageStep()
                self.assertEqual(p.getEnv(), {
                    "A" : "<lib>a<a>",
                    "B" : "<lib><a><b>",
                    "C" : "<lib><b>",
                })

    def testMetaEnvrionmentNoSubstitution(self):
        """metaEnvironment values are not substituted but merged on a key-by-key basis"""
        recipe = {
            "inherit" : ["a", "b"],
            "metaEnvironment" : {
                "A" : "<lib>${A:-}",
                "B" : "<lib>${B:-}",
            },
            "packageVars" : ["A", "B", "C"]
        }
        classes = {
            "a" : {
                "metaEnvironment" : {
                    "A" : "${A:-}<a>",
                    "B" : "<a>",
                },
            },
            "b" : {
                "metaEnvironment" : {
                    "B" : "${B:-}<b>",
                    "C" : "<b>",
                },
            },
        }
        env = {
            "A" : "a",
            "B" : "b",
        }
        p = self.parseAndPrepare(recipe, classes, env=env).getPackageStep()
        self.assertEqual(p.getEnv(), {
            "A" : "<lib>${A:-}",
            "B" : "<lib>${B:-}",
            "C" : "<b>",
        })
