# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import os
import textwrap

from bob.input import RecipeSet, walkPackagePath
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
        roots = recipes.generatePackages(lambda x,y: "unused")

        # make sure "b" is addressable
        p = walkPackagePath(roots, "root/b")
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
        self.assertRaises(ParseError, recipes.generatePackages,
            lambda x,y: "unused")

class TestMultiPackage(TestCase):
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

    def testEmptyMultiPackageName(self):
        self.writeRecipe("root", """\
            root: True
            depends: [a, a-b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("a", """\
            buildScript: "true"
            multiPackage:
                "":
                    packageScript: "true"
                b:
                    packageScript: "true"
            """)
        recipes = RecipeSet()
        recipes.parse()
        roots = recipes.generatePackages(lambda x,y: "unused")

        package = walkPackagePath(roots, "root")
        needed = ['a', 'a-b' ]
        for p in package.getDirectDepSteps():
            if p.getPackage().getName() in needed:
                needed.remove(p.getPackage().getName())
        assert(len(needed) == 0)

    def myJoinScripts(scripts, glue=""):
        scripts = [ s for s in scripts if ((s is not None) and (s != "")) ]
        if scripts != []:
            return "".join(scripts)
        else:
            return None

    @patch('bob.input.joinScripts', side_effect=myJoinScripts)
    @patch('bob.input.PackagePickler.dump')
    def testNestedMultiPackage(self, join, pickler):
        pickler.return_value = 0
        self.writeRecipe("root", """\
            root: True
            depends: [a-a, a-b, a-b-a, a-b-b, a-b-b-a, a-b-b-b]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("a", """\
            buildScript: 'a'
            packageScript: "true"
            multiPackage:
                a:
                    buildScript: -a
                b:
                    buildScript: -b
                    multiPackage:
                        a:
                            buildScript: -a
                        b:
                            buildScript: -b
                            multiPackage:
                                a:
                                    buildScript: -a
                                b:
                                    buildScript: -b
            """)

        pickler = patch('bob.input.PackagePickler.dump')

        recipes = RecipeSet()
        recipes.parse()
        roots = recipes.generatePackages(lambda x,y: "unused")

        package = walkPackagePath(roots, "root")
        needed = ['a-a', 'a-b', 'a-b-a', 'a-b-b', 'a-b-b-a', 'a-b-b-b' ]
        for p in package.getDirectDepSteps():
            assert( p.getPackage().getBuildStep().getScript() == p.getPackage().getName() )
            if p.getPackage().getName() in needed:
                needed.remove(p.getPackage().getName())
        assert(len(needed) == 0)
