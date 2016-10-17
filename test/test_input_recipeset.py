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
from unittest.mock import Mock
import os

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

    def testUserConfigAlias(self):
        """Test Package Alias Names"""
        with TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.mkdir("recipes")
            with open(os.path.join("recipes","bar.yaml"), "w") as f:
                f.write("root: true")
            with open("default.yaml", "w") as f:
                f.write("alias:\n")
                f.write("    FOO: bar\n")
            recipeSet = RecipeSet()
            recipeSet.parse()
            roots = recipeSet.generatePackages(lambda s,m: "unused").values()
            names = [ "bar", "FOO" ]
            i = 0
            for r in roots:
                assert r.getName() == names[i]
                i += 1

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

