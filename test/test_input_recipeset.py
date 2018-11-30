# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import os
import textwrap

from bob.input import RecipeSet
from bob.errors import ParseError

class TestUserConfig(TestCase):
    def setUp(self):
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)

    def testEmptyTree(self):
        """Test parsing an empty receipe tree"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            recipeSet = RecipeSet()
            recipeSet.parse()

    def testDefaultEmpty(self):
        """Test parsing an empty default.yaml"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write(" ")
            recipeSet = RecipeSet()
            recipeSet.parse()

    def testDefaultValidation(self):
        """Test that default.yaml is validated with a schema"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("wrongkey: foo\n")
            recipeSet = RecipeSet()
            self.assertRaises(ParseError, recipeSet.parse)

    def testDefaultInclude(self):
        """Test parsing default.yaml including another file"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - user\n")
            with open("user.yaml", "w") as f:
                f.write("whitelist: [FOO]\n")
            recipeSet = RecipeSet()
            recipeSet.parse()

            assert "FOO" in recipeSet.envWhiteList()

    def testDefaultIncludeMissing(self):
        """Test that default.yaml can include missing files"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - user\n")
            recipeSet = RecipeSet()
            recipeSet.parse()

            assert recipeSet.defaultEnv() == {}

    def testDefaultIncludeOverrides(self):
        """Test that included files override settings of default.yaml"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - user\n")
                f.write("environment:\n")
                f.write("    FOO: BAR\n")
                f.write("    BAR: BAZ\n")
            with open("user.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    FOO: BAZ\n")
            recipeSet = RecipeSet()
            recipeSet.parse()

            assert recipeSet.defaultEnv() == { "FOO":"BAZ", "BAR":"BAZ" }

    def testUserConfigMissing(self):
        """Test that missing user config fails parsing"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            recipeSet = RecipeSet()
            recipeSet.setConfigFiles(["user"])
            self.assertRaises(ParseError, recipeSet.parse)

    def testUserConfigOverrides(self):
        """Test that user configs override default.yaml w/ includes"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - included\n")
                f.write("environment:\n")
                f.write("    FOO: BAR\n")
            with open("included.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    FOO: BAZ\n")
            with open("user.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    FOO: USER\n")

            recipeSet = RecipeSet()
            recipeSet.setConfigFiles(["user"])
            recipeSet.parse()

            assert recipeSet.defaultEnv() == { "FOO":"USER"}

    def testDefaultRequire(self):
        """Test parsing default.yaml requiring another file"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("require:\n")
                f.write("    - user\n")
            with open("user.yaml", "w") as f:
                f.write("whitelist: [FOO]\n")
            recipeSet = RecipeSet()
            recipeSet.parse()

            assert "FOO" in recipeSet.envWhiteList()

    def testDefaultRequireMissing(self):
        """Test that default.yaml barfs on required missing files"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("require:\n")
                f.write("    - user\n")
            recipeSet = RecipeSet()
            self.assertRaises(ParseError, recipeSet.parse)

    def testDefaultRequireLowerPrecedence(self):
        """Test that 'require' has lower precedence than 'include'"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - higher\n")
                f.write("require:\n")
                f.write("    - lower\n")
                f.write("environment:\n")
                f.write("    FOO: default\n")
                f.write("    BAR: default\n")
                f.write("    BAZ: default\n")
            with open("lower.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    BAR: lower\n")
                f.write("    BAZ: lower\n")
            with open("higher.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    BAZ: higher\n")
            recipeSet = RecipeSet()
            recipeSet.parse()
            self.assertEqual(recipeSet.defaultEnv(),
                {'FOO' : 'default', 'BAR' : 'lower', 'BAZ' : 'higher' })

    def testDefaultRelativeIncludes(self):
        """Test relative includes work"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            os.makedirs("some/sub/dirs")
            os.makedirs("other/directories")
            with open("config.yaml", "w") as f:
                f.write("policies:\n")
                f.write("  relativeIncludes: True\n")
            with open("default.yaml", "w") as f:
                f.write("include:\n")
                f.write("    - some/first\n")
                f.write("require:\n")
                f.write("    - other/second\n")
                f.write("environment:\n")
                f.write("    FOO: default\n")
                f.write("    BAR: default\n")
                f.write("    BAZ: default\n")
            with open("other/second.yaml", "w") as f:
                f.write('require: ["directories/lower"]')
            with open("other/directories/lower.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    BAR: lower\n")
                f.write("    BAZ: lower\n")
            with open("some/first.yaml", "w") as f:
                f.write('include: ["sub/dirs/higher"]')
            with open("some/sub/dirs/higher.yaml", "w") as f:
                f.write("environment:\n")
                f.write("    BAZ: higher\n")
            recipeSet = RecipeSet()
            recipeSet.parse()
            self.assertEqual(recipeSet.defaultEnv(),
                {'FOO' : 'default', 'BAR' : 'lower', 'BAZ' : 'higher' })


class TestDependencies(TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmpdir = TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        os.mkdir("recipes")

    def tearDown(self):
        self.tmpdir.cleanup()
        os.chdir(self.cwd)

    def writeRecipe(self, name, content):
        with open(os.path.join("recipes", name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def testDuplicateRemoval(self):
        """Test that provided dependencies do not replace real dependencies"""
        self.writeRecipe("root", """\
            root: True
            depends: [a, b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("a", """\
            depends: [b]
            provideDeps: [b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("b", """\
            buildScript: "true"
            packageScript: "true"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")

        # make sure "b" is addressable
        p = packages.walkPackagePath("root/b")
        self.assertEqual(p.getName(), "b")

    def testIncompatible(self):
        """Incompatible provided dependencies must raise an error"""

        self.writeRecipe("root", """\
            root: True
            depends: [a, b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("a", """\
            depends:
                -
                    name: c
                    environment: { FOO: A }
            provideDeps: [c]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("b", """\
            depends:
                -
                    name: c
                    environment: { FOO: B }
            provideDeps: [c]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("c", """\
            buildVars: [FOO]
            buildScript: "true"
            packageScript: "true"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        self.assertRaises(ParseError, packages.getRootPackage)

    def testCyclic(self):
        """Cyclic dependencies must be detected during parsing"""
        self.writeRecipe("a", """\
            root: True
            depends: [b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("b", """\
            depends: [c]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("c", """\
            depends: [a]
            buildScript: "true"
            packageScript: "true"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        self.assertRaises(ParseError, packages.getRootPackage)

    def testCyclicSpecial(self):
        """Make sure cycles are detected on common sub-trees too"""
        self.writeRecipe("root1", """\
            root: True
            depends: [b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("root2", """\
            root: True
            depends:
                -   name: b
                    if: "${TERMINATE:-1}"
            buildScript: "true"
            packageScript: "true"
            """)

        self.writeRecipe("b", """\
            environment:
                TERMINATE: "0"
            depends: [c]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("c", """\
            depends: [root2]
            buildScript: "true"
            packageScript: "true"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        self.assertRaises(ParseError, packages.getRootPackage)

    def testIncompatibleNamedTwice(self):
        """Test that it is impossible to name the same dependency twice with
           different variants."""

        self.writeRecipe("root", """\
            multiPackage:
                "":
                    root: True

                    depends:
                        - name: root-lib
                          environment:
                            FOO: bar
                        - name: root-lib
                          use: [tools]
                          environment:
                            FOO: baz

                    buildScript: "true"
                    packageScript: "true"

                lib:
                    packageVars: [FOO]
                    packageScript: "true"
                    provideTools:
                        t: "."
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        self.assertRaises(ParseError, packages.getRootPackage)

