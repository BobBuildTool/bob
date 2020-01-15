# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
import os
import textwrap
import yaml

from bob import DEBUG
from bob.input import RecipeSet
from bob.errors import ParseError, BobError

DEBUG['ngd'] = True

class RecipesTmp:
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmpdir = TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        os.mkdir("recipes")
        os.mkdir("classes")

    def tearDown(self):
        self.tmpdir.cleanup()
        os.chdir(self.cwd)

    def writeRecipe(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "recipes")
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeClass(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "classes")
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeConfig(self, content, layer=[]):
        path = os.path.join("", *(os.path.join("layers", l) for l in layer))
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "config.yaml"), "w") as f:
            f.write(yaml.dump(content))

    def generate(self, sandboxEnabled=False):
        recipes = RecipeSet()
        recipes.parse()
        return recipes.generatePackages(lambda x,y: "unused",
            sandboxEnabled=sandboxEnabled)


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


class TestDependencies(RecipesTmp, TestCase):
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


class TestNetAccess(RecipesTmp, TestCase):

    def testOldPolicy(self):
        """Test that network access is enbled by default for old projects"""
        self.writeRecipe("root", """\
            root: True
            """)
        p = self.generate().walkPackagePath("root")
        self.assertTrue(p.getBuildStep().hasNetAccess())
        self.assertTrue(p.getPackageStep().hasNetAccess())

    def testNewPolicy(self):
        """Test that network access is disabled by default"""
        self.writeConfig({
            "bobMinimumVersion" : "0.15",
        })
        self.writeRecipe("root", """\
            root: True
            """)
        p = self.generate().walkPackagePath("root")
        self.assertFalse(p.getBuildStep().hasNetAccess())
        self.assertFalse(p.getPackageStep().hasNetAccess())

    def testBuildNetAccess(self):
        """Test that a recipe can request network access for build step"""
        self.writeConfig({
            "bobMinimumVersion" : "0.15",
        })
        self.writeRecipe("root1", """\
            root: True
            buildNetAccess: True
            buildScript: "true"
            """)
        self.writeRecipe("root2", """\
            root: True
            packageNetAccess: True
            """)
        packages = self.generate()
        root1 = packages.walkPackagePath("root1")
        self.assertTrue(root1.getBuildStep().hasNetAccess())
        self.assertFalse(root1.getPackageStep().hasNetAccess())
        root2 = packages.walkPackagePath("root2")
        self.assertFalse(root2.getBuildStep().hasNetAccess())
        self.assertTrue(root2.getPackageStep().hasNetAccess())

    def testToolAccessBuild(self):
        """Test that a tool can force network access for build step."""

        self.writeConfig({
            "bobMinimumVersion" : "0.15",
        })
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool
                  use: [tools]
            buildTools: [compiler]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("tool", """\
            provideTools:
                compiler:
                    path: "."
                    netAccess: True
            """)

        p = self.generate().walkPackagePath("root")
        self.assertTrue(p.getBuildStep().hasNetAccess())
        self.assertTrue(p.getPackageStep().hasNetAccess())

    def testToolAccessPackage(self):
        """Test that a tool can force network access for package step."""

        self.writeConfig({
            "bobMinimumVersion" : "0.15",
        })
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool
                  use: [tools]
            buildScript: "true"
            packageTools: [compiler]
            packageScript: "true"
            """)
        self.writeRecipe("tool", """\
            provideTools:
                compiler:
                    path: "."
                    netAccess: True
            """)

        p = self.generate().walkPackagePath("root")
        self.assertFalse(p.getBuildStep().hasNetAccess())
        self.assertTrue(p.getPackageStep().hasNetAccess())


class TestToolEnvironment(RecipesTmp, TestCase):

    def testEnvDefine(self):
        """Test that a tool can set environment."""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool
                  use: [tools]
            environment:
                FOO: unset
                BAR: unset
            packageTools: [compiler]
            packageVars: [FOO, BAR]
            packageScript: "true"
            """)
        self.writeRecipe("tool", """\
            environment:
                LOCAL: "foo"
            provideTools:
                compiler:
                    path: "."
                    environment:
                        FOO: "${LOCAL}"
                        BAR: "bar"
            """)

        p = self.generate().walkPackagePath("root")
        self.assertEqual(p.getPackageStep().getEnv(),
            {"FOO":"foo", "BAR":"bar"})

    def testEnvCollides(self):
        """Test that colliding tool environment definitions are detected."""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool
                  use: [tools]
            packageTools: [t1, t2]
            packageScript: "true"
            """)
        self.writeRecipe("tool", """\
            provideTools:
                t1:
                    path: "."
                    environment:
                        FOO: "foo"
                        BAR: "bar"
                t2:
                    path: "."
                    environment:
                        BAR: "bar"
                        BAZ: "baz"
            """)

        packages = self.generate()
        self.assertRaises(ParseError, packages.getRootPackage)

class TestFingerprints(RecipesTmp, TestCase):
    """Test fingerprint impact.

    Everything is done with sandbox. Without sandbox the handling moves to the
    build-id that is implemented in the build backend. This should be covered
    by the 'fingerprints' black box test.
    """

    def setUp(self):
        super().setUp()
        self.writeRecipe("sandbox", """\
            provideSandbox:
                paths: ["/"]
            """)

    def testCheckoutNotFingerprinted(self):
        """Checkout steps are independent of fingerprints"""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: sandbox
                  use: [sandbox]
            checkoutScript: "true"
            buildScript: "true"
            packageScript: "true"

            multiPackage:
                "1": { }
                "2":
                    fingerprintScript: "echo bob"
                    fingerprintIf: True
            """)
        packages = self.generate(True)
        r1 = packages.walkPackagePath("root-1")
        r2 = packages.walkPackagePath("root-2")

        self.assertEqual(r1.getCheckoutStep().getVariantId(),
            r2.getCheckoutStep().getVariantId())
        self.assertNotEqual(r1.getBuildStep().getVariantId(),
            r2.getBuildStep().getVariantId())
        self.assertNotEqual(r1.getPackageStep().getVariantId(),
            r2.getPackageStep().getVariantId())

    def testCheckoutToolFingerpintIndependent(self):
        """Checkout steps are not influenced by tool fingerprint scripts.

        But the build and package steps must be still affetcted, though.
        """

        common = textwrap.dedent("""\
            root: True
            depends:
                - name: sandbox
                  use: [sandbox]
                  forward: True
                - name: tool
                  use: [tools]
            checkoutScript: "true"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("root1", common + "checkoutTools: [plainTool]\n")
        self.writeRecipe("root2", common + "checkoutTools: [fingerprintedTool]\n")
        self.writeRecipe("tool", """\
            provideTools:
                plainTool:
                    path: "."
                fingerprintedTool:
                    path: "."
                    fingerprintScript: "echo bob"
                    fingerprintIf: True
            """)
        packages = self.generate(True)
        r1 = packages.walkPackagePath("root1")
        r2 = packages.walkPackagePath("root2")

        self.assertEqual(r1.getCheckoutStep().getVariantId(),
            r2.getCheckoutStep().getVariantId())
        self.assertNotEqual(r1.getBuildStep().getVariantId(),
            r2.getBuildStep().getVariantId())
        self.assertNotEqual(r1.getPackageStep().getVariantId(),
            r2.getPackageStep().getVariantId())

    def testResultTransitive(self):
        """Fingerprint is transitive when using a tainted result"""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: sandbox
                  use: [sandbox]
                  forward: True
            buildScript: "true"
            multiPackage:
                clean:
                    depends:
                        - dep-clean
                tainted:
                    depends:
                        - dep-tainted
            """)
        self.writeRecipe("dep", """\
            packageScript: "true"
            multiPackage:
                clean: { }
                tainted:
                    fingerprintScript: "echo bob"
                    fingerprintIf: True
            """)
        packages = self.generate(True)
        r1 = packages.walkPackagePath("root-clean")
        r2 = packages.walkPackagePath("root-tainted")

        self.assertNotEqual(r1.getPackageStep().getVariantId(),
            r2.getPackageStep().getVariantId())

    def testToolNotTransitive(self):
        """Using a fingerprinted tool does not influence digest"""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: sandbox
                  use: [sandbox]
                  forward: True
            buildTools: [ tool ]
            buildScript: "true"
            multiPackage:
                clean:
                    depends:
                        - name: tools-clean
                          use: [tools]
                tainted:
                    depends:
                        - name: tools-tainted
                          use: [tools]
            """)
        self.writeRecipe("tools", """\
            packageScript: "true"
            provideTools:
                tool: "."
            multiPackage:
                clean: { }
                tainted:
                    fingerprintScript: "echo bob"
                    fingerprintIf: True
            """)
        packages = self.generate(True)

        r1 = packages.walkPackagePath("root-clean")
        r2 = packages.walkPackagePath("root-tainted")
        self.assertEqual(r1.getPackageStep().getVariantId(),
            r2.getPackageStep().getVariantId())

        self.assertFalse(packages.walkPackagePath("root-clean/tools-clean")
            .getPackageStep()._isFingerprinted())
        self.assertTrue(packages.walkPackagePath("root-tainted/tools-tainted")
            .getPackageStep()._isFingerprinted())

    def testSandboxNotTransitive(self):
        """Using a fingerprinted sandbox does not influence digest"""

        self.writeRecipe("root", """\
            root: True
            multiPackage:
                clean:
                    depends:
                        - name: sandbox-clean
                          use: [tools]
                tainted:
                    depends:
                        - name: sandbox-tainted
                          use: [tools]
            """)
        self.writeRecipe("sandbox", """\
            packageScript: "true"
            provideSandbox:
                paths: ["/"]
            multiPackage:
                clean: { }
                tainted:
                    fingerprintScript: "echo bob"
                    fingerprintIf: True
            """)
        packages = self.generate(True)

        r1 = packages.walkPackagePath("root-clean")
        r2 = packages.walkPackagePath("root-tainted")
        self.assertEqual(r1.getPackageStep().getVariantId(),
            r2.getPackageStep().getVariantId())

        self.assertFalse(packages.walkPackagePath("root-clean/sandbox-clean")
            .getPackageStep()._isFingerprinted())
        self.assertTrue(packages.walkPackagePath("root-tainted/sandbox-tainted")
            .getPackageStep()._isFingerprinted())

    def testByDefaultIncluded(self):
        """If no 'fingerprintIf' is given the 'fingerprintScript' must be evaluated.

        Parsed without sandbox to make sure fingerprint scripts are considered.
        """

        self.writeRecipe("root", """\
            root: True
            fingerprintScript: |
                must-be-included
            multiPackage:
                clean: { }
                tainted:
                    fingerprintScript: |
                        taint-script
                    fingerprintIf: True
            """)

        packages = self.generate()

        ps = packages.walkPackagePath("root-clean").getPackageStep()
        self.assertFalse(ps._isFingerprinted())
        self.assertFalse("must-be-included" in ps._getFingerprintScript())
        ps = packages.walkPackagePath("root-tainted").getPackageStep()
        self.assertTrue(ps._isFingerprinted())
        self.assertTrue("must-be-included" in ps._getFingerprintScript())
        self.assertTrue("taint-script" in ps._getFingerprintScript())

    def testToolCanEnable(self):
        """Tools must be able to amend and enable fingerprinting."""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tools
                  use: [tools]
            fingerprintIf: False
            fingerprintScript: |
                must-not-be-included
            packageTools: [tool]
            """)
        self.writeRecipe("tools", """\
            packageScript: "true"
            provideTools:
                tool:
                    path: "."
                    fingerprintScript: "tool-script"
                    fingerprintIf: True
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertTrue(ps._isFingerprinted())
        self.assertFalse("must-not-be-included" in ps._getFingerprintScript())
        self.assertTrue("tool-script" in ps._getFingerprintScript())

    def testDisabledNotIncluded(self):
        """The 'fingerprintScript' must not be included if 'fingerprintIf' is False."""

        self.writeClass("unspecified", """\
            fingerprintScript: |
                unspecified
            """)
        self.writeClass("static-disabled", """\
            fingerprintIf: False
            fingerprintScript: |
                static-disabled
            """)
        self.writeClass("static-enabled", """\
            fingerprintIf: True
            fingerprintScript: |
                static-enabled
            """)
        self.writeClass("dynamic", """\
            fingerprintIf: "${ENABLE_FINGERPRINTING}"
            fingerprintScript: |
                dynamic
            """)
        self.writeRecipe("root", """\
            root: True
            inherit:
                - unspecified
                - static-disabled
                - static-enabled
                - dynamic
            multiPackage:
                dyn-enabled:
                    environment:
                        ENABLE_FINGERPRINTING: "true"
                dyn-disabled:
                    environment:
                        ENABLE_FINGERPRINTING: "false"
            """)

        packages = self.generate()

        ps = packages.walkPackagePath("root-dyn-enabled").getPackageStep()
        self.assertTrue(ps._isFingerprinted())
        self.assertTrue("unspecified" in ps._getFingerprintScript())
        self.assertFalse("static-disabled" in ps._getFingerprintScript())
        self.assertTrue("static-enabled" in ps._getFingerprintScript())
        self.assertTrue("dynamic" in ps._getFingerprintScript())

        ps = packages.walkPackagePath("root-dyn-disabled").getPackageStep()
        self.assertTrue(ps._isFingerprinted())
        self.assertTrue("unspecified" in ps._getFingerprintScript())
        self.assertFalse("static-disabled" in ps._getFingerprintScript())
        self.assertTrue("static-enabled" in ps._getFingerprintScript())
        self.assertFalse("dynamic" in ps._getFingerprintScript())


class TestLayers(RecipesTmp, TestCase):
    """Test layer support.

    Test the various properties of layers and their error handling.
    """

    def setUp(self):
        super().setUp()
        self.writeConfig({
            "bobMinimumVersion" : "0.15",
            "layers" : [ "l1_n1", "l1_n2" ],
        })
        self.writeRecipe("root", """\
            root: True
            depends:
                - foo
                - bar
            buildScript: "true"
            packageScript: "true"
            """)

        self.writeConfig({
            "bobMinimumVersion" : "0.15",
            "layers" : [ "l2" ],
        }, layer=["l1_n1"])
        self.writeRecipe("foo", """\
            depends:
                - baz
            buildScript: "true"
            packageScript: "true"
            """,
            layer=["l1_n1"])

        self.writeRecipe("baz", """\
            buildScript: "true"
            packageScript: "true"
            """,
            layer=["l1_n1", "l2"])

        self.writeRecipe("bar", """\
            buildScript: "true"
            packageScript: "true"
            """,
            layer=["l1_n2"])

    def testRegular(self):
        """Test that layers can be parsed"""
        self.generate()

    def testRecipeObstruction(self):
        """Test that layers must not provide identical recipes"""
        self.writeRecipe("foo", """\
            depends:
                - baz
            buildScript: "true"
            packageScript: "true"
            """,
            layer=["l1_n2"])
        self.assertRaises(ParseError, self.generate)

    def testClassObstruction(self):
        """Test that layers must not provide identical classes"""
        self.writeClass("c", "", layer=["l1_n1", "l2"])
        self.writeClass("c", "", layer=["l1_n2"])
        self.assertRaises(ParseError, self.generate)

    def testMinimumVersion(self):
        """Test that (sub-)layers cannot request a higher minimum version"""
        self.writeConfig({
            "bobMinimumVersion" : "0.14",
            "layers" : [ "l1_n1", "l1_n2" ],
        })
        self.assertRaises(ParseError, self.generate)

class TestIfExpression(RecipesTmp, TestCase):
    """ Test if expressions """
    def setUp(self):
        super().setUp()
        self.writeRecipe("root", """\
            root: True
            depends:
                - if: !expr |
                    "${USE_DEPS}" == "1"
                  depends:
                    - bar-1
                    - name: bar-2
                      if: !expr |
                        "${BAR}" == "bar2"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("bar", """\
            multiPackage:
                "1":
                    buildScript: "true"
                "2":
                    buildScript: "true"
            packageScript: "true"
            """)

    def testRegular(self):
        """Test that if expressions can be parsed"""
        self.generate()

    def testNested(self):
        """Test that nested if expressions are working"""
        recipes = RecipeSet()
        recipes.parse()

        ps = recipes.generatePackages(lambda x,y: "unused",
                envOverrides={"USE_DEPS" : "0", "BAR" : "bar2"})
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-1")
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-2")

        ps = recipes.generatePackages(lambda x,y: "unused",
                envOverrides={"USE_DEPS" : "1"})
        ps.walkPackagePath("root/bar-1")
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-2")

        ps = recipes.generatePackages(lambda x,y: "unused",
                envOverrides={"USE_DEPS" : "1", "BAR" : "bar2"})
        ps.walkPackagePath("root/bar-1")
        ps.walkPackagePath("root/bar-2")
