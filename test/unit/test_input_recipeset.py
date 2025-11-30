# Bob build tool
# Copyright (C) 2016  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch
import os
import textwrap
import yaml

from bob import DEBUG
from bob.input import RecipeSet, YamlCache, LayersConfig
from bob.errors import ParseError, BobError
from bob.scm import ScmOverride
from bob.utils import runInEventLoop

from mocks.intermediate import MockIR, MockIRStep

DEBUG['ngd'] = True

def pruneBuiltin(env):
    return { k : v for k,v in env.items() if not k.startswith("BOB_") }

class RecipesTmp:
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmpdir = TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        os.mkdir("recipes")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmpdir.cleanup()

    def writeRecipe(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "recipes")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeClass(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "classes")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writeConfig(self, content, layer=[]):
        path = os.path.join("", *(os.path.join("layers", l) for l in layer))
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "config.yaml"), "w") as f:
            f.write(yaml.dump(content))

    def writeDefault(self, content, layer=[]):
        path = os.path.join("", *(os.path.join("layers", l) for l in layer))
        if path: os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "default.yaml"), "w") as f:
            f.write(yaml.dump(content))

    def writeAlias(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "aliases")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".yaml"), "w") as f:
            f.write(textwrap.dedent(content))

    def writePlugin(self, name, content, layer=[]):
        path = os.path.join("",
            *(os.path.join("layers", l) for l in layer),
            "plugins")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name+".py"), "w") as f:
            f.write(textwrap.dedent(content))

    def generate(self, sandboxEnabled=False, env={}):
        recipes = RecipeSet()
        recipes.parse(env)
        return recipes.generatePackages(lambda x,y: "unused", sandboxEnabled)


class TestUserConfig(RecipesTmp, TestCase):
    def testEmptyTree(self):
        """Test parsing an empty receipe tree"""
        recipeSet = RecipeSet()
        recipeSet.parse()

    def testDefaultEmpty(self):
        """Test parsing an empty default.yaml"""
        with open("default.yaml", "w") as f:
            f.write(" ")
        recipeSet = RecipeSet()
        recipeSet.parse()

    def testDefaultValidation(self):
        """Test that default.yaml is validated with a schema"""
        with open("default.yaml", "w") as f:
            f.write("wrongkey: foo\n")
        recipeSet = RecipeSet()
        self.assertRaises(ParseError, recipeSet.parse)

    def testDefaultInclude(self):
        """Test parsing default.yaml including another file"""
        with open("default.yaml", "w") as f:
            f.write("include:\n")
            f.write("    - user\n")
        with open("user.yaml", "w") as f:
            f.write("whitelist: [FOO]\n")
        recipeSet = RecipeSet()
        recipeSet.parse()

        self.assertIn("FOO", recipeSet.envWhiteList())

    def testDefaultIncludeMissing(self):
        """Test that default.yaml can include missing files"""
        with open("default.yaml", "w") as f:
            f.write("include:\n")
            f.write("    - user\n")
        recipeSet = RecipeSet()
        recipeSet.parse()

        self.assertEqual(pruneBuiltin(recipeSet.defaultEnv()), {})

    def testDefaultIncludeOverrides(self):
        """Test that included files override settings of default.yaml"""
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

        self.assertEqual(pruneBuiltin(recipeSet.defaultEnv()),
            { "FOO":"BAZ", "BAR":"BAZ" })

    def testUserConfigMissing(self):
        """Test that missing user config fails parsing"""
        recipeSet = RecipeSet()
        recipeSet.setConfigFiles(["user"])
        self.assertRaises(ParseError, recipeSet.parse)

    def testUserConfigOverrides(self):
        """Test that user configs override default.yaml w/ includes"""
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

        self.assertEqual(pruneBuiltin(recipeSet.defaultEnv()),
            { "FOO":"USER"})

    def testDefaultRequire(self):
        """Test parsing default.yaml requiring another file"""
        with open("default.yaml", "w") as f:
            f.write("require:\n")
            f.write("    - user\n")
        with open("user.yaml", "w") as f:
            f.write("whitelist: [FOO]\n")
        recipeSet = RecipeSet()
        recipeSet.parse()

        self.assertIn("FOO", recipeSet.envWhiteList())

    def testDefaultRequireMissing(self):
        """Test that default.yaml barfs on required missing files"""
        with open("default.yaml", "w") as f:
            f.write("require:\n")
            f.write("    - user\n")
        recipeSet = RecipeSet()
        self.assertRaises(ParseError, recipeSet.parse)

    def testDefaultRequireLowerPrecedence(self):
        """Test that 'require' has lower precedence than 'include'"""
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
        self.assertEqual(pruneBuiltin(recipeSet.defaultEnv()),
            {'FOO' : 'default', 'BAR' : 'lower', 'BAZ' : 'higher' })

    def testDefaultRelativeIncludes(self):
        """Test relative includes work"""
        os.makedirs("some/sub/dirs")
        os.makedirs("other/directories")
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
        self.assertEqual(pruneBuiltin(recipeSet.defaultEnv()),
            {'FOO' : 'default', 'BAR' : 'lower', 'BAZ' : 'higher' })

    def testWhitelistRemove(self):
        """Test whitelistRemove key"""
        self.writeDefault({
                "whitelist" : [ "FOO", "BAR" ],
                "whitelistRemove" : [ "BAR", "PATH" ],
            })
        recipeSet = RecipeSet()
        recipeSet.parse()
        self.assertIn("FOO", recipeSet.envWhiteList())
        self.assertNotIn("BAR", recipeSet.envWhiteList())
        self.assertNotIn("PATH", recipeSet.envWhiteList())

    def testArchiveAppendPrepend(self):
        """Test mirrorAppend/Prepend keywords"""
        self.writeDefault(
            {
                "archivePrepend" : [
                    {
                        "name" : "local",
                        "backend" : "file",
                        "path" : "/foo/bar",
                    },
                    {
                        "name" : "remote_prepend",
                        "backend" : "http",
                        "url" : "http://bob.test/prepend",
                    },
                ],
                "archive" : {
                    "name" : "remote",
                    "backend" : "http",
                    "url" : "http://bob.test/main",
                },
                "archiveAppend" : {
                    "name" : "remote_append",
                    "backend" : "http",
                    "url" : "http://bob.test/append",
                },
            })
        recipeSet = RecipeSet()
        recipeSet.parse()

        self.assertEqual(
            [
                {
                    "name" : "local",
                    "backend" : "file",
                    "path" : "/foo/bar",
                },
                {
                    "name" : "remote_prepend",
                    "backend" : "http",
                    "url" : "http://bob.test/prepend",
                },
                {
                    "name" : "remote",
                    "backend" : "http",
                    "url" : "http://bob.test/main",
                },
                {
                    "name" : "remote_append",
                    "backend" : "http",
                    "url" : "http://bob.test/append",
                },
            ], recipeSet.archiveSpec())

    def testArchiveEntries(self):
        # valid config
        self.writeDefault(
            {
                "archive": {
                    "name": "remote",
                    "backend": "http",
                    "url": "http://bob.test/main",
                    "retries": 1
                }
            })
        recipeSet = RecipeSet()
        recipeSet.parse()
        self.assertEqual(
            [{
                    "name": "remote",
                    "backend": "http",
                    "url": "http://bob.test/main",
                    "retries": 1
                }], recipeSet.archiveSpec())
        # string not allowed
        self.writeDefault(
            {
                "archive": {
                    "name": "remote",
                    "backend": "http",
                    "url": "http://bob.test/main",
                    "retries": "str"
                }
            })
        recipeSet = RecipeSet()
        with self.assertRaises(ParseError):
            recipeSet.parse()
        # negative integer not allowed
        self.writeDefault(
            {
                "archive": {
                    "name": "remote",
                    "backend": "http",
                    "url": "http://bob.test/main",
                    "retries": -1
                }
            })
        recipeSet = RecipeSet()
        with self.assertRaises(ParseError):
            recipeSet.parse()

    def testMirrorsAppendPrepend(self):
        """Test pre/fallbackMirrorAppend/Prepend keywords"""
        for prefix in ["pre", "fallback"]:
            self.writeDefault(
                {
                    prefix+"MirrorPrepend" : [
                        { "scm" : "url", "url" : "foo", "mirror" : "prepend-1" },
                        { "scm" : "url", "url" : "bar", "mirror" : "prepend-2" },
                    ],
                    prefix+"Mirror" : { "scm" : "url", "url" : "bar", "mirror" : "main" },
                    prefix+"MirrorAppend" : { "scm" : "url", "url" : "bar", "mirror" : "append" },
                })

            recipeSet = RecipeSet()
            recipeSet.parse()

            self.assertEqual(
                [
                    { "scm" : "url", "url" : "foo", "mirror" : "prepend-1" },
                    { "scm" : "url", "url" : "bar", "mirror" : "prepend-2" },
                    { "scm" : "url", "url" : "bar", "mirror" : "main" },
                    { "scm" : "url", "url" : "bar", "mirror" : "append" },
                ],
                recipeSet.getPreMirrors() if prefix == "pre" else recipeSet.getFallbackMirrors())


class TestProjectConfiguration(RecipesTmp, TestCase):
    def testInvalid(self):
        """Invalid data type of config.yaml is detected"""
        self.writeConfig([])
        with self.assertRaises(ParseError):
            self.generate()

    def testInvalidMinVerType(self):
        """Invalid type of bobMinimumVersion is detected"""
        self.writeConfig({ "bobMinimumVersion" : 1 })
        with self.assertRaises(ParseError):
            self.generate()

    def testInvalidMinVerStr(self):
        """Invalid bobMinimumVersion is detected"""
        self.writeConfig({ "bobMinimumVersion" : "a.b" })
        with self.assertRaises(ParseError):
            self.generate()


class TestDependencies(RecipesTmp, TestCase):
    def testVariableDeps(self):
        """Test resolve of dependecies by environment substitution"""
        self.writeRecipe("root", """\
            root: True
            depends: [a]
            environment:
                A : "b"
                D : "c"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("root2", """\
            root: True
            depends: [a, d]
            environment:
                A : "c"
            buildScript: "true"
            packageScript: "true"
            """)

        self.writeRecipe("a", """\
            depends: [ "$A-foo" ]
            buildScript: "true"
            packageScript: "true"
            provideDeps: [ "$A-f*" ]
            provideVars:
                D: "e"
            """)

        self.writeRecipe("b-foo", """\
            buildScript: "true"
            packageScript: "echo 'b'"
            """)
        self.writeRecipe("c-foo", """\
            buildScript: "true"
            packageScript: "echo 'c'"
            """)
        self.writeRecipe("d", """\
            depends:
             - name: a
               use: [environment, deps]
             - "$D"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("e", """\
            buildScript: "true"
            packageScript: "true"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")

        p = packages.walkPackagePath("root/a/b-foo")
        self.assertEqual(p.getName(), "b-foo")
        p = packages.walkPackagePath("root2/a/c-foo")
        self.assertEqual(p.getName(), "c-foo")
        p = packages.walkPackagePath("root2/d/e")
        self.assertEqual(p.getName(), "e")
        #access via providedDeps
        p = packages.walkPackagePath("root/b-foo")
        self.assertEqual(p.getName(), "b-foo")

    def testDepConditionCheckOrder(self):
        """Dependency conditions are tested before any other substitutions."""
        self.writeRecipe("root", """\
            root: True
            depends:
                - if: "false"
                  name: "$DOES_NOT_EXIST"
                  environment:
                    FOO: "$DOES_NOT_EXIST"
            buildScript: "true"
            packageScript: "true"
            """)
        packages = self.generate()
        packages.getRootPackage() # Must not fail due to substitution error

    def testGlobProvideDeps(self):
        """Test globbing pattern in provideDeps"""
        self.writeRecipe("root", """\
            root: True
            depends: [a]
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("a", """\
            depends: [b-dev, b-tgt]
            packageScript: "echo a"
            provideDeps: [ "*-dev" ]
            """)
        self.writeRecipe("b", """\
            multiPackage:
                dev:
                    packageScript: "echo b-dev"
                tgt:
                    packageScript: "echo b-tgt"
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")

        rootArgs = packages.walkPackagePath("root").getBuildStep().getArguments()
        self.assertEqual(len(rootArgs), 3)
        self.assertEqual(rootArgs[0].getPackage().getName(), "root")
        self.assertEqual(rootArgs[1].getPackage().getName(), "a")
        self.assertEqual(rootArgs[2].getPackage().getName(), "b-dev")

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

    def testProvideDisabled(self):
        """Providing disabled dependencies is not considered an error."""
        self.writeRecipe("root", """\
            root: True
            depends:
                - intermediate
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("intermediate", """\
            depends:
                - if: "false"
                  name: a
                - if: "true"
                  name: b
            buildScript: "true"
            packageScript: "true"
            provideDeps: ["a", "b"]
            """)
        self.writeRecipe("a", "packageScript: a")
        self.writeRecipe("b", "packageScript: b")
        packages = self.generate()
        #packages.walkPackagePath("root")
        rootArgs = packages.walkPackagePath("root").getBuildStep().getArguments()
        self.assertEqual(len(rootArgs), 3)
        self.assertEqual(rootArgs[0].getPackage().getName(), "root")
        self.assertEqual(rootArgs[1].getPackage().getName(), "intermediate")
        self.assertEqual(rootArgs[2].getPackage().getName(), "b")

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

    def testCheckoutDep(self):
        """Test that checkout dependencies are available in checkout step"""

        self.writeRecipe("root", """\
            root: True
            depends:
                - name: lib1
                  checkoutDep: True
                - name: lib2
                  checkoutDep: False
            checkoutScript: "true"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("lib1", "")
        self.writeRecipe("lib2", "")

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        p = packages.walkPackagePath("root")
        self.assertEqual(len(p.getCheckoutStep().getArguments()), 1)
        self.assertEqual(p.getCheckoutStep().getArguments()[0].getPackage().getName(),
                         "lib1")

        self.assertEqual(len(p.getBuildStep().getArguments()), 3)
        self.assertEqual(p.getCheckoutStep().getArguments()[0],
                         p.getBuildStep().getArguments()[1],
                         "lib1 is available at build step too")

    def testCheckoutDepVariants(self):
        """Checkout dependencies contribute to variant management of checkoutStep"""
        self.writeRecipe("root", """\
            root: True
            checkoutScript: "true"
            buildScript: "true"
            packageScript: "true"
            multiPackage:
                a:
                    depends:
                        - name: lib1
                          checkoutDep: True
                b:
                    depends:
                        - name: lib2
                          checkoutDep: True
            """)
        self.writeRecipe("lib1", "packageScript: foo")
        self.writeRecipe("lib2", "packageScript: bar")

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")

        pa = packages.walkPackagePath("root-a")
        self.assertEqual(len(pa.getCheckoutStep().getArguments()), 1)
        paVId = pa.getCheckoutStep().getVariantId()

        pb = packages.walkPackagePath("root-b")
        self.assertEqual(len(pb.getCheckoutStep().getArguments()), 1)
        pbVId = pb.getCheckoutStep().getVariantId()

        self.assertNotEqual(paVId, pbVId, "checkout steps are different")

    def testDepInherit(self):
        """ Test the `inherit` property for dependencies  """

        self.writeDefault(
            {"environment" : {"DEFAULT" : "42" }})

        self.writeClass("foo", """\
            root: True
            depends:
              - name: foo
                use: [tools]
                forward: True
            packageTools: [foo]
            """)

        self.writeRecipe("a", """\
            inherit: [foo]
            depends:
              - name: sandbox
                use: [sandbox]
                forward: True
              - name: b-env
                use: [environment]
                forward: true
              - name: b
                inherit: False
              - name: c
                inherit: False
                environment:
                    C: "1"
                tools:
                    c: foo
              - d
            packageScript: "true"
            """)

        self.writeRecipe("b-env", """\
            provideVars:
                B: "2"
            """)

        self.writeRecipe("b", """\
            inherit: [foo]
            packageVars: [DEFAULT, B]
            packageScript: "true"
            """)

        self.writeRecipe("c", """\
            packageVars: [C]
            packageTools: [c]
            packageScript: "true"
            """)

        self.writeRecipe("d", """\
            packageVars: [B]
            packageTools: [foo]
            packageScript: "true"
            """)

        self.writeRecipe("foo", """\
            packageTools: [foo]
            packageScript: "true"
            provideTools:
              foo: .
            """)

        self.writeRecipe("sandbox", """\
            provideSandbox:
               paths: ["."]
            """)

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda s,m: "unused", True)

        pa_b = packages.walkPackagePath("a/b")
        pb   = packages.walkPackagePath("b")
        pb_e = packages.walkPackagePath("a/b-env")
        pc   = packages.walkPackagePath("a/c")
        pd   = packages.walkPackagePath("a/d")
        self.assertEqual(pa_b.getPackageStep().getVariantId(),
                         pb.getPackageStep().getVariantId())
        self.assertEqual({"DEFAULT" : "42"}, pb.getPackageStep().getEnv())
        self.assertEqual({"C" : "1"}, pc.getPackageStep().getEnv())
        self.assertEqual(list(pc.getPackageStep().getTools()), ["c"])
        self.assertEqual(pc.getPackageStep().getSandbox(), None)
        self.assertNotEqual(pb_e.getPackageStep().getSandbox(), None)
        self.assertNotEqual(pd.getPackageStep().getSandbox(), None)

    def testPackageDepends(self):
        """Test that checkoutDepends enables dependencies in package step"""

        self.writeRecipe("root", """\
            root: True
            depends:
                - lib1
                - lib2
            packageDepends: True
            packageScript: "true"
            """)
        self.writeRecipe("lib1", "")
        self.writeRecipe("lib2", "")

        recipes = RecipeSet()
        recipes.parse()
        packages = recipes.generatePackages(lambda x,y: "unused")
        p = packages.walkPackagePath("root")
        self.assertEqual(len(p.getPackageStep().getArguments()), 3)
        self.assertEqual(p.getPackageStep().getArguments()[0].getPackage().getName(),
                         "root")
        self.assertEqual(p.getPackageStep().getArguments()[1].getPackage().getName(),
                         "lib1")
        self.assertEqual(p.getPackageStep().getArguments()[2].getPackage().getName(),
                         "lib2")

class TestDependencyEnv(RecipesTmp, TestCase):
    """Tests related to "environment" block in dependencies"""

    def testSetEnvironment(self):
        """Variables set in environment block are available in dependency"""
        self.writeRecipe("root", """\
            root: True
            environment:
                FOO: "default"
                ZZZ: "set"
            depends:
                - name: dep
                  environment:
                    FOO: overridden
                    BAR: added
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("dep", """\
            packageVars: [FOO, BAR, ZZZ]
            """)

        p = self.generate().walkPackagePath("root/dep")
        self.assertEqual(
            {"FOO": "overridden", "BAR": "added", "ZZZ": "set"},
            p.getPackageStep().getEnv())

    def testSubstitute(self):
        """Variables in environment block are subject to variable substitution"""
        self.writeRecipe("root", """\
            root: True
            environment:
                FOO: "default"
            depends:
                - name: dep
                  environment:
                    BAR: "aa $FOO cc"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("dep", """\
            packageVars: [FOO, BAR]
            """)

        p = self.generate().walkPackagePath("root/dep")
        self.assertEqual(
            {"FOO": "default", "BAR": "aa default cc"},
            p.getPackageStep().getEnv())

    def testSubstituteTakenFromPrivate(self):
        """Substitutions in environment block are done with package-private env"""

        # note the missing "forward: True"
        self.writeRecipe("root", """\
            root: True
            environment:
                FOO: "default"
            depends:
                - name: provider
                  use: [environment, result]
                - name: dep
                  environment:
                    BAR: "aa $FOO cc"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("provider", """\
            provideVars:
                FOO: provided-foo
                BAZ: provided-baz
            """)
        self.writeRecipe("dep", """\
            packageVars: [FOO, BAR, BAZ]
            """)

        p = self.generate().walkPackagePath("root/dep")
        self.assertEqual(
            {"FOO": "default", "BAR": "aa provided-foo cc"},
            p.getPackageStep().getEnv())


class TestNetAccess(RecipesTmp, TestCase):

    def testDefaultPolicy(self):
        """Test that network access is disabled by default"""
        self.writeConfig({
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.24",
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
            "bobMinimumVersion" : "0.16",
            "layers" : [ "l1_n1", "l1_n2" ],
        })
        self.assertRaises(ParseError, self.generate)

    def testManagedRejected(self):
        self.writeConfig({
            "bobMinimumVersion" : "0.25rc1",
            "layers" : [
                {
                    "name" : "l1_n1",
                    "scm" : "git",
                    "url" : "git@server.test:bob.git",
                }
            ]
        })
        with self.assertRaises(ParseError) as err:
            self.generate()
        self.assertEqual(err.exception.slogan,
                         "Managed layers aren't enabled! See the managedLayers policy for details.")

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
        recipes.parse(envOverrides={"USE_DEPS" : "0", "BAR" : "bar2"})
        ps = recipes.generatePackages(lambda x,y: "unused")
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-1")
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-2")

        recipes = RecipeSet()
        recipes.parse(envOverrides={"USE_DEPS" : "1"})
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("root/bar-1")
        self.assertRaises(BobError, ps.walkPackagePath, "root/bar-2")

        recipes = RecipeSet()
        recipes.parse(envOverrides={"USE_DEPS" : "1", "BAR" : "bar2"})
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("root/bar-1")
        ps.walkPackagePath("root/bar-2")

class TestRootProperty(RecipesTmp, TestCase):
    """Test root property evaluation"""

    def testStringType(self):
        """Test evaluation of string boolean type"""
        self.writeRecipe("r1", """\
            root: "True"
            """)
        self.writeRecipe("r2", """\
            root: "${FOO:-0}"
            """)

        recipes = RecipeSet()
        recipes.parse()
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("r1")
        self.assertRaises(BobError, ps.walkPackagePath, "r2")

        recipes = RecipeSet()
        recipes.parse({"FOO": "1"})
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("r1")
        ps.walkPackagePath("r2")

    def testStringType(self):
        """Test evaluation of IfExpression"""
        self.writeRecipe("r1", """\
            root: !expr |
                "True"
            """)
        self.writeRecipe("r2", """\
            root: !expr |
                "${FOO:-0}" == "bar"
            """)

        recipes = RecipeSet()
        recipes.parse()
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("r1")
        self.assertRaises(BobError, ps.walkPackagePath, "r2")

        recipes = RecipeSet()
        recipes.parse({"FOO": "bar"})
        ps = recipes.generatePackages(lambda x,y: "unused")
        ps.walkPackagePath("r1")
        ps.walkPackagePath("r2")

class TestNoUndefinedToolsPolicy(RecipesTmp, TestCase):
    """ Test behaviour of noUndefinedTools policy"""

    def setUp(self):
        super().setUp()
        self.writeRecipe("root", """\
            root: True
            packageTools: ["undefined"]
            packageScript: "true"
            """)

    def testOldBehaviour(self):
        """Test that undefined tools are permissable on old policy setting.

        The tool is silently ignored and dropped.
        """
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "noUndefinedTools" : False },
        })

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual(list(ps.getTools().keys()), [])

    def testNewBehaviour(self):
        """Test that undefined tools generate a parsing error on new policy setting"""
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "noUndefinedTools" : True },
        })
        with self.assertRaises(ParseError):
            packages = self.generate()
            packages.walkPackagePath("root").getPackageStep()

class TestToolsWeak(RecipesTmp, TestCase):
    """Test behaviour or weak tools"""

    def setUp(self):
        super().setUp()
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "noUndefinedTools" : False },
        })
        self.writeRecipe("tool", """\
            multiPackage:
                "1":
                    provideTools:
                        tool: "."
                    packageScript: "foo"
                "2":
                    provideTools:
                        tool: "."
                    packageScript: "bat"
            """)

    def testWeak(self):
        """Weak tools have no impact on package build-id,

        The variant-id is still affected to honestly reflect the build
        structure of the recipes
        """
        self.writeRecipe("r1", """\
            root: True
            depends:
                - name: tool-1
                  use: [tools]
            packageToolsWeak: [tool]
            """)
        self.writeRecipe("r2", """\
            root: True
            depends:
                - name: tool-2
                  use: [tools]
            packageToolsWeak: [tool]
            """)
        packages = self.generate()
        r1 = packages.walkPackagePath("r1").getPackageStep()
        r2 = packages.walkPackagePath("r2").getPackageStep()
        async def buildId(steps):
            ret = []
            for step in steps:
                ret.append(await step.getDigestCoro(buildId, relaxTools=True))
            return ret
        self.assertEqual(runInEventLoop(buildId([MockIRStep.fromStep(r1, MockIR)])),
                         runInEventLoop(buildId([MockIRStep.fromStep(r2, MockIR)])),
                         "Weak tool does not influence build-id")
        self.assertNotEqual(r1.getVariantId(), r2.getVariantId())
        self.assertNotEqual(r1.getTools()["tool"].getStep().getVariantId(),
                            r2.getTools()["tool"].getStep().getVariantId())

    def testWeakMissing(self):
        """Weak tools that are missing still make a difference"""
        self.writeRecipe("r1", """\
            root: True
            depends:
                - name: tool-1
                  use: [tools]
            packageTools: [tool]
            """)
        self.writeRecipe("r2", """\
            root: True
            packageTools: [tool]
            """)
        packages = self.generate()
        r1 = packages.walkPackagePath("r1").getPackageStep()
        r2 = packages.walkPackagePath("r2").getPackageStep()
        self.assertNotEqual(r1.getVariantId(), r2.getVariantId())

    def testStrongOverride(self):
        """A weak and strong tool refence is treated as strong"""
        self.writeRecipe("r1", """\
            root: True
            depends:
                - name: tool-1
                  use: [tools]
            packageTools: [tool]
            packageToolsWeak: [tool]
            """)
        self.writeRecipe("r2", """\
            root: True
            depends:
                - name: tool-2
                  use: [tools]
            packageTools: [tool]
            packageToolsWeak: [tool]
            """)
        packages = self.generate()
        r1 = packages.walkPackagePath("r1").getPackageStep()
        r2 = packages.walkPackagePath("r2").getPackageStep()
        self.assertNotEqual(r1.getVariantId(), r2.getVariantId())

class TestConditionalTools(RecipesTmp, TestCase):
    """Test behaviour of conditional tools"""

    def setUp(self):
        super().setUp()
        self.writeConfig({
            "bobMinimumVersion" : "0.24",
        })
        self.writeRecipe("tool", """\
            multiPackage:
                "1":
                    provideTools:
                        tool-foo: "."
                    packageScript: "foo"
                "2":
                    provideTools:
                        tool-bar: "."
                    packageScript: "bar"
            """)

    def testConditional(self):
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool-1
                  use: [tools]
                - name: tool-2
                  use: [tools]
            packageTools:
                - tool-foo
                - name: tool-bar
                  if: "${BAR:-}"
            """)

        packages = self.generate()
        r = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual(set(r.getTools().keys()), { "tool-foo" },
                         "By default, 'tool-bar' is not used")

        packages = self.generate(env = { "BAR" : "1" })
        r = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual(set(r.getTools().keys()), { "tool-foo", "tool-bar" },
                         "If enabled, 'tool-bar' is used")

class TestScmIgnoreUserPolicy(RecipesTmp, TestCase):
    """ Test behaviour of scmIgnoreUser policy"""

    def setUp(self):
        super().setUp()
        self.writeRecipe("git", """\
            root: True
            buildScript: "true"
            packageScript: "true"
            multiPackage:
                a:
                    checkoutSCM:
                        scm: git
                        url: foo@host.xz:path/to/repo.git
                b:
                    checkoutSCM:
                        scm: git
                        url: bar@host.xz:path/to/repo.git
            """)
        self.writeRecipe("url", """\
            root: True
            buildScript: "true"
            packageScript: "true"
            multiPackage:
                a:
                    checkoutSCM:
                        scm: url
                        url: https://foo@host.test/file
                b:
                    checkoutSCM:
                        scm: url
                        url: https://bar@host.test/file
            """)

    def testOldBehaviour(self):
        """Test that user name of URL is part of the variantId"""
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "scmIgnoreUser" : False },
        })

        packages = self.generate()

        git_a = packages.walkPackagePath("git-a").getPackageStep()
        git_b = packages.walkPackagePath("git-b").getPackageStep()
        self.assertNotEqual(git_a.getVariantId(), git_b.getVariantId())

        url_a = packages.walkPackagePath("url-a").getPackageStep()
        url_b = packages.walkPackagePath("url-b").getPackageStep()
        self.assertNotEqual(url_a.getVariantId(), url_b.getVariantId())

    def testNewBehaviour(self):
        """Test that user name in URL is not part of variantId on new policy setting"""
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "scmIgnoreUser" : True },
        })

        packages = self.generate()

        git_a = packages.walkPackagePath("git-a").getPackageStep()
        git_b = packages.walkPackagePath("git-b").getPackageStep()
        self.assertEqual(git_a.getVariantId(), git_b.getVariantId())

        url_a = packages.walkPackagePath("url-a").getPackageStep()
        url_b = packages.walkPackagePath("url-b").getPackageStep()
        self.assertEqual(url_a.getVariantId(), url_b.getVariantId())

class TestPruneImportScmPolicy(RecipesTmp, TestCase):
    """ Test behaviour of pruneImportScm policy"""

    def setUp(self):
        super().setUp()
        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
                scm: import
                url: ./recipes
            buildScript: "true"
            packageScript: "true"
            """)

    def testOldBehaviour(self):
        """Test that prune was disabled in the past by default"""
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "pruneImportScm" : False },
        })

        pkg = self.generate().walkPackagePath("root")
        self.assertFalse(pkg.getCheckoutStep().getScmList()[0].getProperties(False)["prune"])

    def testNewBehaviour(self):
        """Test that prune is the new default"""
        self.writeConfig({
            "bobMinimumVersion" : "0.17",
            "policies" : { "pruneImportScm" : True },
        })

        pkg = self.generate().walkPackagePath("root")
        self.assertTrue(pkg.getCheckoutStep().getScmList()[0].getProperties(False)["prune"])


class TestScmDefaults(RecipesTmp, TestCase):
    """ Test scmDefault Settings in default.yaml"""

    def testScmDefaultsGit(self):
        """Test scmDefaults for git scm"""

        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
               scm: git
               url: foo@bar
            buildScript: "true"
            packageScript: "true"
            """)
        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertEqual(props["branch"], "master")
        self.assertEqual(props["sslVerify"], True)
        self.assertEqual(props["shallow"], None)
        self.assertEqual(props["singleBranch"], None)
        self.assertFalse(props["submodules"])
        self.assertFalse(props["recurseSubmodules"])
        self.assertTrue(props["shallowSubmodules"])

        self.writeDefault(
            { "scmDefaults" : {
                "git" : {
                    "branch" : "main" ,
                    "sslVerify" : True,
                    "shallow" : 42,
                    "singleBranch" : True,
                    "submodules" : True,
                    "recurseSubmodules" : True,
                    "shallowSubmodules" : False,
                    "dir" : "git",
                }
            }})

        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertEqual(props["branch"], "main")
        self.assertTrue(props["sslVerify"])
        self.assertEqual(props["shallow"], 42)
        self.assertTrue(props["singleBranch"])
        self.assertTrue(props["submodules"])
        self.assertTrue(props["recurseSubmodules"])
        self.assertFalse(props["shallowSubmodules"])
        self.assertEqual(props["dir"], "git")

        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
               scm: git
               url: foo@bar
               branch: "foobar"
               shallow: 7
               singleBranch: false
               submodules: false
            buildScript: "true"
            packageScript: "true"
            """)
        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertEqual(props["branch"], "foobar")
        self.assertTrue(props["sslVerify"])
        self.assertEqual(props["shallow"], 7)
        self.assertFalse(props["singleBranch"])
        self.assertFalse(props["submodules"])
        self.assertTrue(props["recurseSubmodules"])
        self.assertFalse(props["shallowSubmodules"])

    def testScmDefaultsImport(self):
        """Test default settings for import scm"""

        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
               scm: import
               url: foo@bar
            buildScript: "true"
            packageScript: "true"
            """)

        pkg = self.generate().walkPackagePath("root")
        self.assertFalse(pkg.getCheckoutStep().getScmList()[0].getProperties(False)["prune"])
        self.writeDefault({"scmDefaults" :
            { "import" : { "prune" : True}}})

        pkg = self.generate().walkPackagePath("root")
        self.assertTrue(pkg.getCheckoutStep().getScmList()[0].getProperties(False)["prune"])

    def testScmDefaultsSvn(self):
        """Test default settings for svn-scm"""

        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
               scm: svn
               url: foo@bar
            buildScript: "true"
            packageScript: "true"
            """)

        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertTrue(props["sslVerify"])
        self.writeDefault({"scmDefaults" :
            { "svn" : { "sslVerify" : False}}})

        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertFalse(props["sslVerify"])

    def testScmDefaultsUrl(self):
        """Test default settings for url scm"""

        self.writeRecipe("root", """\
            root: True
            checkoutSCM:
               scm: url
               url: foo@bar/foobar.tgz
            buildScript: "true"
            packageScript: "true"
            """)

        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertTrue(props["sslVerify"])
        self.assertTrue(props["extract"])
        self.assertEqual(props["stripComponents"], 0)
        self.assertTrue( props["fileName"])

        self.writeDefault({"scmDefaults" :
            { "url" : {
                "sslVerify" : False,
                "extract" : False,
                "fileName" : "downloaded_file",
                "stripComponents" : 1,
            }}})

        pkg = self.generate().walkPackagePath("root")
        props = pkg.getCheckoutStep().getScmList()[0].getProperties(False)
        self.assertFalse(props["sslVerify"])
        self.assertFalse(props["extract"])
        self.assertEqual(props["stripComponents"], 1)
        self.assertEqual(props["fileName"], "downloaded_file")


class TestAliases(RecipesTmp, TestCase):
    """Test recipe aliasing"""

    def testGlobalAlias(self):
        """Test global alias"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - virtual
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("real", """\
            packageScript: "real"
            """)
        self.writeAlias("virtual", "real")
        pkg = self.generate().walkPackagePath("root/virtual")
        self.assertEqual(pkg.getName(), "virtual")
        self.assertEqual(pkg.getRecipe().getName(), "real")

    def testGlobalAliasRecursive(self):
        """Test global alias of an alias"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - level1
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("real", """\
            packageScript: "real"
            """)
        self.writeAlias("level1", "level2")
        self.writeAlias("level2", "real")
        pkg = self.generate().walkPackagePath("root/level1")
        self.assertEqual(pkg.getName(), "level1")
        self.assertEqual(pkg.getRecipe().getName(), "real")

    def testLocalAlias(self):
        """Test that the same dependency can be named with different aliases"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: real
                  alias: virtual1
                - name: real
                  alias: virtual2
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("real", """\
            packageScript: "real"
            """)
        packages = self.generate()
        p1 = self.generate().walkPackagePath("root/virtual1")
        p2 = self.generate().walkPackagePath("root/virtual2")
        self.assertEqual(p1.getName(), "virtual1")
        self.assertEqual(p1.getRecipe().getName(), "real")
        self.assertEqual(p2.getName(), "virtual2")
        self.assertEqual(p2.getRecipe().getName(), "real")
        self.assertEqual(p1.getPackageStep().getVariantId(),
                         p2.getPackageStep().getVariantId())

    def testLocalAliasWins(self):
        """A local alias works with global aliases"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: virtual
                  alias: foo
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("real", """\
            packageScript: "real"
            """)
        self.writeAlias("virtual", "real")
        pkg = self.generate().walkPackagePath("root/foo")
        self.assertEqual(pkg.getName(), "foo")
        self.assertEqual(pkg.getRecipe().getName(), "real")

    def testGlobalAliasDoesntHideCycle(self):
        """Global aliases cannot hide the fact that recipes are cyclic"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - level1
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("level1", """\
            depends:
                - level2
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("level2", """\
            depends:
                - level3
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeAlias("level3", "level1")
        with self.assertRaises(ParseError):
            packages = self.generate()
            packages.walkPackagePath("root").getPackageStep()

    def testLocalAliasDoesntHideCycle(self):
        """Per-dependency aliases cannot hide the fact that recipes are cyclic"""
        self.writeRecipe("root", """\
            root: True
            environment:
                NAME: "x"
            depends:
                - name: level1
                  alias: "${NAME}x"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("level1", """\
            depends:
                - name: level2
                  alias: "${NAME}x"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("level2", """\
            depends:
                - name: level1
                  alias: "${NAME}x"
            buildScript: "true"
            packageScript: "true"
            """)
        with self.assertRaises(ParseError):
            packages = self.generate()
            packages.walkPackagePath("root").getPackageStep()


class TestConditionalEnv(RecipesTmp, TestCase):
    """Test conditional environment blocks"""

    def testDependendsEnv(self):
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: level1
                  environment:
                      V1:
                          value: "x"
                          if: "${ENABLE_V1:-0}"
            buildScript: "true"
            packageScript: "true"
            """)
        self.writeRecipe("level1", """\
            packageVars: [V1]
            packageScript: "true"
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root/level1").getPackageStep()
        self.assertEqual({}, ps.getEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root/level1").getPackageStep()
        self.assertEqual({"V1" : "x"}, ps.getEnv())

    def testEnvironment(self):
        self.writeRecipe("root", """\
            root: True
            environment:
                V1:
                    value: "x"
                    if: "${ENABLE_V1:-0}"
            buildVars: [V1]
            buildScript: "true"
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({}, ps.getEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V1" : "x"}, ps.getEnv())

    def testMetaEnvironment(self):
        self.writeRecipe("root", """\
            root: True
            metaEnvironment:
                V1:
                    value: "x"
                    if: "${ENABLE_V1:-0}"
            """)

        # Old substituteMetaEnv policy
        packages = self.generate()
        pkg = packages.walkPackagePath("root")
        self.assertEqual({}, pkg.getMetaEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        pkg = packages.walkPackagePath("root")
        self.assertEqual({"V1" : "x"}, pkg.getMetaEnv())

        # New substituteMetaEnv policy
        self.writeConfig({
            "policies" : { "substituteMetaEnv" : True },
        })

        packages = self.generate()
        pkg = packages.walkPackagePath("root")
        self.assertEqual({}, pkg.getMetaEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        pkg = packages.walkPackagePath("root")
        self.assertEqual({"V1" : "x"}, pkg.getMetaEnv())

    def testPrivateEnvironment(self):
        self.writeRecipe("root", """\
            root: True
            privateEnvironment:
                V1:
                    value: "x"
                    if: "${ENABLE_V1:-0}"
                V2: y
            buildVars: [V1, V2]
            buildScript: "true"
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V2" : "y"}, ps.getEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V1" : "x", "V2" : "y"}, ps.getEnv())

    def testProvideToolsEnv(self):
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tool
                  use: [tools]
            packageTools: [t1]
            packageVars: [V1]
            """)
        self.writeRecipe("tool", """\
            provideTools:
                t1:
                    path: "."
                    environment:
                        V1:
                            value: "x"
                            if: "${ENABLE_V1:-0}"
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({}, ps.getEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V1" : "x"}, ps.getEnv())

    def testProvideSandboxEnv(self):
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: sandbox
                  use: [sandbox]
            packageVars: [V1]
            """)
        self.writeRecipe("sandbox", """\
            provideSandbox:
                paths: [bin]
                environment:
                    V1:
                        value: "x"
                        if: "${ENABLE_V1:-0}"
            """)

        packages = self.generate(sandboxEnabled=True)
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({}, ps.getEnv())

        packages = self.generate(sandboxEnabled=True, env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V1" : "x"}, ps.getEnv())

    def testProvideVars(self):
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: lib
                  use: [environment]
            packageVars: [V1]
            """)
        self.writeRecipe("lib", """\
            provideVars:
                V1:
                    value: "x"
                    if: "${ENABLE_V1:-0}"
            """)

        packages = self.generate()
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({}, ps.getEnv())

        packages = self.generate(env={"ENABLE_V1" : "1"})
        ps = packages.walkPackagePath("root").getPackageStep()
        self.assertEqual({"V1" : "x"}, ps.getEnv())


class TestToolDependencies(RecipesTmp, TestCase):
    """Test dependTools and dependToolsWeak"""

    def testSimpleDeps(self):
        """dependTools(Weak) are added to using step"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tools
                  use: [tools]
            packageTools: [tool-a]
            """)
        self.writeRecipe("tools", """\
            provideTools:
                tool-a:
                    path: a
                    dependTools: [tool-b]
                    dependToolsWeak: [tool-c]
                tool-b: b
                tool-c: c
            """)

        p = self.generate().walkPackagePath("root")
        self.assertEqual(p.getPackageStep().toolDep, {"tool-a", "tool-b", "tool-c"})
        self.assertEqual(p.getPackageStep().toolDepWeak, {"tool-c"})

        tools = p.getPackageStep().getTools()
        self.assertEqual(tools["tool-a"].getDependTools(), {"tool-b"})
        self.assertEqual(tools["tool-a"].getDependToolsWeak(), {"tool-c"})
        self.assertEqual(tools["tool-b"].getDependTools(), set())
        self.assertEqual(tools["tool-b"].getDependToolsWeak(), set())

    def testTransitiveDeps(self):
        """dependTools(Weak) is resolved recursively"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tools
                  use: [tools]
            packageTools: [tool-a]
            """)
        self.writeRecipe("tools", """\
            provideTools:
                tool-a:
                    path: a
                    dependTools: [tool-b]
                tool-b:
                    path: b
                    dependTools: [tool-c]
                tool-c: c
            """)

        p = self.generate().walkPackagePath("root")
        self.assertEqual(p.getPackageStep().toolDep, {"tool-a", "tool-b", "tool-c"})
        self.assertEqual(p.getPackageStep().toolDepWeak, set())

    def testTransitiveMixedDeps(self):
        """dependTools(Weak) is resolved recursively even when mixing weak and strong"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tools
                  use: [tools]
            packageTools: [tool-a]
            """)
        self.writeRecipe("tools", """\
            provideTools:
                tool-a:
                    path: a
                    dependToolsWeak: [tool-b]
                tool-b:
                    path: b
                    dependTools: [tool-c]
                tool-c: c
            """)

        p = self.generate().walkPackagePath("root")
        self.assertEqual(p.getPackageStep().toolDep, {"tool-a", "tool-b", "tool-c"})
        self.assertEqual(p.getPackageStep().toolDepWeak, {"tool-b"})

    def testSimpleDepsUpgrade(self):
        """dependTools(Weak) weak and strong can be overridden by recipe"""
        self.writeRecipe("root", """\
            root: True
            depends:
                - name: tools
                  use: [tools]
            buildTools: [tool-a]
            buildScript: "true"
            packageToolsWeak: [tool-b]
            packageTools: [tool-c]
            """)
        self.writeRecipe("tools", """\
            provideTools:
                tool-a:
                    path: a
                    dependTools: [tool-b]
                    dependToolsWeak: [tool-c]
                tool-b: b
                tool-c: c
            """)

        p = self.generate().walkPackagePath("root")
        self.assertEqual(p.getBuildStep().toolDep, {"tool-a", "tool-b", "tool-c"})
        self.assertEqual(p.getBuildStep().toolDepWeak, {"tool-c"})
        self.assertEqual(p.getPackageStep().toolDep, {"tool-a", "tool-b", "tool-c"})
        self.assertEqual(p.getPackageStep().toolDepWeak, set())

class TestPlugins(RecipesTmp, TestCase):

    def testWarnStringFunCollision(self):
        """A warning is issued if a plugin defines post-1.0 string function"""
        self.writeRecipe("root", """\
            root: True
            privateEnvironment:
                VAR1: "$(resubst,X,Y,AXBX)"
                VAR2: "$(custom)"
            packageVars: [VAR1, VAR2]
            """)
        self.writeConfig({
            "plugins" : [ "funs" ]
        })
        self.writePlugin("funs", """\
            def resubst(args, **options):
                return "plugin called"
            def custom(args, **options):
                return "custom called"

            manifest = {
                'apiVersion' : "1.0",
                'stringFunctions' : {
                    "resubst" : resubst,
                    "custom" : custom,
                }
            }
            """)

        warnObjMock = MagicMock()
        warnClassMock = MagicMock(return_value=warnObjMock)

        # Patch "Warn" class import at bob.input module
        with patch('bob.input.Warn', warnClassMock):
            p = self.generate().walkPackagePath("root")

        # The parser should warn about the "resubst" collision
        warnObjMock.warn.assert_called()
        self.assertIn("resubst", warnClassMock.call_args.args[0])

        # Make sure the plugin is called instead of the internal implementation.
        self.assertEqual(p.getPackageStep().getEnv(),
                         {"VAR1" : "plugin called", "VAR2" : "custom called"})

class TestLayersConfig(RecipesTmp, TestCase):
    """Test parsing of layer configuration files"""

    def testInclude(self):
        """Test parsing config.yaml including a layer configuration file"""
        with open("config.yaml", "w") as f:
            f.write("layersInclude:\n")
            f.write("    - user\n")
        with open("user.yaml", "w") as f:
            f.write("layersWhitelist: [FOO]\n")

        with YamlCache() as yamlCache:
            _, cfg = RecipeSet.loadConfigYaml(yamlCache.loadYaml, ".")

        self.assertIn("FOO", cfg.envWhiteList())

    def testIncludeMissing(self):
        """A missing layer configuration file is ignored"""
        with open("config.yaml", "w") as f:
            f.write("layersInclude:\n")
            f.write("    - user\n")

        with YamlCache() as yamlCache:
            _, cfg = RecipeSet.loadConfigYaml(yamlCache.loadYaml, ".")

    def testRequire(self):
        """Test parsing config.yaml requiring another file"""
        with open("config.yaml", "w") as f:
            f.write("layersRequire:\n")
            f.write("    - user\n")
        with open("user.yaml", "w") as f:
            f.write("layersWhitelist: [FOO]\n")

        with YamlCache() as yamlCache:
            _, cfg = RecipeSet.loadConfigYaml(yamlCache.loadYaml, ".")

        self.assertIn("FOO", cfg.envWhiteList())

    def testRequireMissing(self):
        """Missing required includes throw an error"""
        with open("config.yaml", "w") as f:
            f.write("layersRequire:\n")
            f.write("    - user\n")

        with self.assertRaises(ParseError):
            with YamlCache() as yamlCache:
                RecipeSet.loadConfigYaml(yamlCache.loadYaml, ".")

    def testInvalidKeys(self):
        """Layer configuration files only support a subset of keys"""
        with open("layerconfig.yaml", "w") as f:
            f.write('bobMinimumVersion: "1.0"\n')

        with self.assertRaises(ParseError):
            with YamlCache() as yamlCache:
                RecipeSet.loadLayersConfigYaml(yamlCache.loadYaml, "layerconfig.yaml", LayersConfig())

    def testPrecedence(self):
        """Included files have a higher precedence than required files"""

        with open("config.yaml", "w") as f:
            f.write(textwrap.dedent("""\
                layersRequire:
                    - require_l1
                layersInclude:
                    - include_l1
                layersScmOverrides:
                    - set:
                        url: root
                layersWhitelist: [ROOT]
                """))
        with open("require_l1.yaml", "w") as f:
            f.write(textwrap.dedent("""\
                layersInclude:
                    - require_l2
                layersScmOverrides:
                    - set:
                        url: require_l1
                layersWhitelist: [REQUIRE_L1]
                """))

        with open("require_l2.yaml", "w") as f:
            f.write(textwrap.dedent("""\
                layersScmOverrides:
                    - set:
                        url: require_l2
                layersWhitelist: [REQUIRE_L2]
                """))
        with open("include_l1.yaml", "w") as f:
            f.write(textwrap.dedent("""\
                layersRequire:
                    - include_l2
                layersScmOverrides:
                    - set:
                        url: include_l1
                layersWhitelist: [INCLUDE_L1]
                """))

        with open("include_l2.yaml", "w") as f:
            f.write(textwrap.dedent("""\
                layersScmOverrides:
                    - set:
                        url: include_l2
                layersWhitelist: [INCLUDE_L2]
                """))

        with YamlCache() as yamlCache:
            _, cfg = RecipeSet.loadConfigYaml(yamlCache.loadYaml, ".")

        self.assertIn("ROOT", cfg.envWhiteList())
        self.assertIn("REQUIRE_L1", cfg.envWhiteList())
        self.assertIn("REQUIRE_L2", cfg.envWhiteList())
        self.assertIn("INCLUDE_L1", cfg.envWhiteList())
        self.assertIn("INCLUDE_L2", cfg.envWhiteList())

        self.assertEqual([ ScmOverride({ "set" : { "url" : "root" }}),
                           ScmOverride({ "set" : { "url" : "require_l1" }}),
                           ScmOverride({ "set" : { "url" : "require_l2" }}),
                           ScmOverride({ "set" : { "url" : "include_l1" }}),
                           ScmOverride({ "set" : { "url" : "include_l2" }}) ],
                         cfg.scmOverrides())
