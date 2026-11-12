# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Recipes and package instantiation."""

from .. import DEBUG
from ..errors import ParseError
from ..languages import getLanguage, ScriptLanguage, BashLanguage, \
    PwshLanguage, PythonLanguage
from ..stringparser import Env, IfExpression
from .common import fetchFingerprintScripts, fetchScripts, mergeScripts, uidGen
from .core import AbstractTool, CoreInterpreter, CoreRef, CoreSandbox, PathsConfig
from .package import CorePackage, corePackageInternal
from itertools import chain
from string import Template
import fnmatch
import os, os.path
import re
import schema


# FIXME: implement this on our own without the Template class. How to do proper
# escaping?
class IncludeHelper:

    def __init__(self, scriptLanguage, fileLoader, baseDir, varBase, sourceName):
        self.__pattern = re.compile(r"""
            \$<(?:
                (?P<escaped>\$)     |
                (?P<named>[<'@][^'>@]+)[@'>]>  |
                (?P<braced>[<'@][^'>@]+)[@'>]> |
                (?P<invalid>)
            )
            """, re.VERBOSE)
        self.__resolverClass = scriptLanguage.Resolver
        self.__baseDir = baseDir
        self.__varBase = re.sub(r'[^a-zA-Z0-9_]', '_', varBase, flags=re.DOTALL)
        self.__fileLoader = fileLoader
        self.__sourceName = sourceName

    def resolve(self, text, section):
        if isinstance(text, str):
            resolver = self.__resolverClass(self.__fileLoader, self.__baseDir,
                text, self.__sourceName, self.__varBase)
            t = Template(text)
            t.delimiter = '$<'
            t.pattern = self.__pattern
            try:
                ret = t.substitute(resolver)
            except ValueError as e:
                raise ParseError("Bad substiturion in {}: {}".format(section, str(e)))
            return resolver.resolve(ret)
        else:
            return (None, None, {})

def substituteAuditFiles(key, auditFiles, env):
    try:
        return { key : val.substitute(env)
                 for key, val in auditFiles.items()
                 if env.evaluate(val.condition, key) }
    except ParseError as e:
        raise ParseError(f"{key}: {e.slogan}") from e

RECIPE_NAME_SCHEMA = schema.Regex(r'^[0-9A-Za-z_.+-]+$')

MULTIPACKAGE_NAME_SCHEMA = schema.Regex(r'^[0-9A-Za-z_.+-]*$')

ALIAS_NAME_RE = re.compile(r"[0-9A-Za-z_.+-]+(::[0-9A-Za-z_.+-]+)*")

class UniquePackageList:
    def __init__(self, stack, errorHandler):
        self.stack = stack
        self.errorHandler = errorHandler
        self.ret = []
        self.cache = {}

    def append(self, ref):
        step = ref.refGetDestination()
        name = step.corePackage.name
        ref2 = self.cache.get(name)
        if ref2 is None:
            self.cache[name] = ref
            self.ret.append(ref)
        elif ref2.refGetDestination().variantId != step.variantId:
            self.errorHandler(name, self.stack, ref, ref2)

    def extend(self, gen):
        for i in gen: self.append(i)

    def result(self):
        return self.ret

class DepTracker:

    __slots__ = ('item', 'isNew', 'usedResult', 'depEntry')

    def __init__(self, item, depEntry):
        self.item = item
        self.depEntry = depEntry
        self.isNew = True
        self.usedResult = False

    def prime(self):
        if self.isNew:
            self.isNew = False
            return True
        else:
            return False

    def useResultOnce(self):
        if self.usedResult:
            return False
        else:
            self.usedResult = True
            return True

class PackageStack:
    __slots__ = ('__recipeSet', '__nameStack')

    def __init__(self):
        self.__recipeSet = set()
        self.__nameStack = []

    def __contains__(self, recipe):
        return recipe in self.__recipeSet

    def intersects(self, packages):
        return not self.__recipeSet.isdisjoint(packages)

    def push(self, recipe, name):
        ret = object.__new__(PackageStack)
        ret.__recipeSet = self.__recipeSet | set([recipe])
        ret.__nameStack = self.__nameStack + [name]
        return ret

    def getNameStack(self):
        return self.__nameStack

class VerbatimProvideDepsResolver:
    def __init__(self, pattern):
        self.pattern = pattern

    def resolve(self, env, resolvedDeps):
        pattern = self.pattern
        return set(d for d in resolvedDeps if d == pattern)

class GlobProvideDepsResolver:
    def __init__(self, pattern):
        self.pattern = pattern

    def resolve(self, env, resolvedDeps):
        pattern = self.pattern
        return set(d for d in resolvedDeps if fnmatch.fnmatchcase(d, pattern))

class SubstituteProvideDepsResolver:
    def __init__(self, pattern):
        self.pattern = pattern

    def resolve(self, env, resolvedDeps):
        pattern = self.pattern
        pattern = env.substitute(pattern, "providedDeps::"+pattern)
        return set(d for d in resolvedDeps if fnmatch.fnmatchcase(d, pattern))

def getProvideDepsResolver(pattern):
    if any((c in pattern) for c in '\\\"\'$'):
        return SubstituteProvideDepsResolver(pattern)
    elif any((c in pattern) for c in '*?['):
        return GlobProvideDepsResolver(pattern)
    else:
        return VerbatimProvideDepsResolver(pattern)

def addTransitiveTools(depSetStrong, depSetWeak, toolsView):
    todo = set(depSetStrong | depSetWeak)
    while todo:
        tool = toolsView.get(todo.pop())
        if tool is None: continue

        dependTools = tool.dependTools
        dependToolsWeak = tool.dependToolsWeak

        # All only previously unseen tools to the todo list
        todo |= (dependTools | dependToolsWeak) - depSetStrong - depSetWeak

        depSetStrong |= dependTools
        depSetWeak |= dependToolsWeak

class Recipe(object):
    """Representation of a single recipe

    Multiple instaces of this class will be created if the recipe used the
    ``multiPackage`` keyword.  In this case the getName() method will return
    the name of the original recipe but the getPackageName() method will return
    it with some addition suffix. Without a ``multiPackage`` keyword there will
    only be one Recipe instance.
    """

    class Dependency(object):
        __slots__ = ('recipe', 'envOverride', 'provideGlobal', 'inherit',
                     'use', 'useEnv', 'useTools', 'useBuildResult', 'useDeps',
                     'useSandbox', 'useInterpreters', 'condition', 'toolOverride', 'checkoutDep',
                     'alias', 'origin')

        def __init__(self, origin, recipe, env, fwd, use, cond, tools, checkoutDep, inherit, alias):
            self.origin = origin
            self.recipe = recipe
            self.envOverride = env
            self.provideGlobal = fwd
            self.inherit = inherit
            self.use = use
            self.useEnv = "environment" in self.use
            self.useTools = "tools" in self.use
            self.useBuildResult = "result" in self.use
            self.useDeps = "deps" in self.use
            self.useSandbox = "sandbox" in self.use
            self.useInterpreters = "interpreters" in self.use
            self.condition = cond
            self.toolOverride = tools
            self.checkoutDep = checkoutDep
            self.alias = alias

        @staticmethod
        def __parseEntry(origin, dep, env, fwd, use, cond, tools, checkoutDep, inherit):
            if isinstance(dep, str):
                return [ Recipe.Dependency(origin, dep, env, fwd, use, cond, tools, checkoutDep,
                                           inherit, None) ]
            else:
                envOverride = dep.get("environment")
                if envOverride:
                    env = env.copy()
                    env.update(envOverride)
                toolOverride = dep.get("tools")
                if toolOverride:
                    tools = tools.copy()
                    tools.update(toolOverride)
                fwd = dep.get("forward", fwd)
                use = dep.get("use", use)
                inherit = dep.get("inherit", inherit)
                newCond = dep.get("if")
                if newCond is not None:
                    cond = cond + [newCond] if cond is not None else [ newCond ]
                checkoutDep = dep.get("checkoutDep", checkoutDep)
                name = dep.get("name")
                if name:
                    if "depends" in dep:
                        raise ParseError("A dependency must not use 'name' and 'depends' at the same time!",
                                         help=f"The offending entries 'name' attribute is '{name}'")
                    return [ Recipe.Dependency(origin, name, env, fwd, use, cond, tools,
                                               checkoutDep, inherit, dep.get("alias")) ]
                dependencies = dep.get("depends")
                if dependencies is None:
                    raise ParseError("Either 'name' or 'depends' required for dependencies!")
                return Recipe.Dependency.parseEntries(origin, dependencies, env, fwd,
                                                      use, cond, tools,
                                                      checkoutDep, inherit)

        @staticmethod
        def parseEntries(origin, deps, env={}, fwd=False, use=["result", "deps"],
                         cond=None, tools={}, checkoutDep=False, inherit=True):
            """Returns an iterator yielding all dependencies as flat list"""
            # return flattened list of dependencies
            return chain.from_iterable(
                Recipe.Dependency.__parseEntry(origin, dep, env, fwd, use, cond, tools,
                                               checkoutDep, inherit)
                for dep in deps )

    @staticmethod
    def loadFromFile(recipeSet, layer, rootDir, fileName, properties, fileSchema,
                     isRecipe, scriptLanguage=None):
        # MultiPackages are handled as separate recipes with an anonymous base
        # class. Directories are treated as categories separated by '::'.
        baseName = os.path.splitext( fileName )[0].split( os.sep )
        fileName = os.path.join(rootDir, fileName)
        try:
            for n in baseName: RECIPE_NAME_SCHEMA.validate(n)
        except schema.SchemaError as e:
            raise ParseError("Invalid recipe name: '{}'".format(fileName))
        baseName = "::".join( baseName )
        baseDir = os.path.dirname(fileName)

        nameMap = {}
        def anonNameCalculator(suffix):
            num = nameMap.setdefault(suffix, 0) + 1
            nameMap[suffix] = num
            return baseName + suffix + "#" + str(num)

        def collect(recipe, suffix, anonBaseClass):
            if "multiPackage" in recipe:
                anonBaseClass = Recipe(recipeSet, recipe, layer, fileName, baseDir,
                    anonNameCalculator(suffix), baseName, properties, isRecipe,
                    anonBaseClass)
                return chain.from_iterable(
                    collect(subSpec, suffix + ("-"+subName if subName else ""),
                            anonBaseClass)
                    for (subName, subSpec) in recipe["multiPackage"].items() )
            else:
                packageName = baseName + suffix
                return [ Recipe(recipeSet, recipe, layer, fileName, baseDir, packageName,
                                baseName, properties, isRecipe, anonBaseClass, scriptLanguage) ]

        return list(collect(recipeSet.loadYaml(fileName, fileSchema), "", None))

    @staticmethod
    def createVirtualRoot(recipeSet, roots, properties):
        recipe = {
            "depends" : [
                { "name" : name, "use" : ["result"] } for name in roots
            ],
            "checkoutUpdateIf" : False,
            "buildScript" : "true",
            "packageScript" : "true"
        }
        ret = Recipe(recipeSet, recipe, "", "", ".", "", "", properties)
        ret.resolveClasses(Env())
        return ret

    def __init__(self, recipeSet, recipe, layer, sourceFile, baseDir, packageName, baseName,
                 properties, isRecipe=True, anonBaseClass=None, scriptLanguage=ScriptLanguage.BASH):
        self.__recipeSet = recipeSet
        self.__primarySource = sourceFile
        self.__sources = [ sourceFile ] if anonBaseClass is None else []
        self.__classesResolved = False
        self.__inherit = recipe.get("inherit", [])
        self.__anonBaseClass = anonBaseClass
        self.__defaultScriptLanguage = scriptLanguage
        self.__deps = list(Recipe.Dependency.parseEntries(self, recipe.get("depends", [])))
        self.__packageName = packageName
        self.__baseName = baseName
        self.__root = recipe.get("root")
        self.__provideTools = { name : AbstractTool(spec)
            for (name, spec) in recipe.get("provideTools", {}).items() }
        self.__provideVars = recipe.get("provideVars", {})
        self.__provideDeps = set(recipe.get("provideDeps", []))
        self.__provideSandbox = recipe.get("provideSandbox")
        self.__provideInterpreters = recipe.get("provideInterpreters", {})
        self.__varSelf = recipe.get("environment", {})
        self.__varLocal = recipe.get("localEnvironment", {})
        self.__varPrivate = recipe.get("privateEnvironment", {})
        self.__metaEnv = recipe.get("metaEnvironment", {})
        self.__checkoutAuditFiles = recipe.get("checkoutAuditFiles", {})
        self.__checkoutDeterministic = recipe.get("checkoutDeterministic")
        self.__checkoutVars = set(recipe.get("checkoutVars", []))
        self.__checkoutVarsWeak = set(recipe.get("checkoutVarsWeak", []))
        self.__buildAuditFiles = recipe.get("buildAuditFiles", {})
        self.__buildVars = set(recipe.get("buildVars", []))
        self.__buildVars |= self.__checkoutVars
        self.__buildVarsWeak = set(recipe.get("buildVarsWeak", []))
        self.__buildVarsWeak |= self.__checkoutVarsWeak
        self.__packageDepends = recipe.get("packageDepends")
        self.__packageAuditFiles = recipe.get("packageAuditFiles", {})
        self.__packageVars = set(recipe.get("packageVars", []))
        self.__packageVars |= self.__buildVars
        self.__packageVarsWeak = set(recipe.get("packageVarsWeak", []))
        self.__packageVarsWeak |= self.__buildVarsWeak
        self.__toolDepCheckout = recipe.get("checkoutTools", [])
        self.__toolDepCheckoutWeak = recipe.get("checkoutToolsWeak", [])
        self.__toolDepBuild = recipe.get("buildTools", [])
        self.__toolDepBuildWeak = recipe.get("buildToolsWeak", [])
        self.__toolDepPackage = recipe.get("packageTools", [])
        self.__toolDepPackageWeak = recipe.get("packageToolsWeak", [])
        self.__shared = recipe.get("shared")
        self.__relocatable = recipe.get("relocatable")
        self.__jobServer = recipe.get("jobServer")
        self.__properties = {
            n : p(n in recipe, recipe.get(n))
            for (n, p) in properties.items()
        }
        self.__corePackagesByMatch = []
        self.__corePackagesById = {}
        self.__layer = layer

        sourceName = ("Recipe " if isRecipe else "Class  ") + packageName + (
            ", layer "+ layer if layer else "")
        incHelperBash = IncludeHelper(BashLanguage, recipeSet.loadBinary,
                                      baseDir, packageName, sourceName).resolve
        incHelperPwsh = IncludeHelper(PwshLanguage, recipeSet.loadBinary,
                                      baseDir, packageName, sourceName).resolve
        incHelperPython = IncludeHelper(PythonLanguage, recipeSet.loadBinary,
                                        baseDir, packageName, sourceName).resolve

        self.__scriptLanguage = recipe.get("scriptLanguage")
        self.__checkout = fetchScripts(recipe, "checkout", incHelperBash, incHelperPwsh, incHelperPython)
        self.__checkoutSCMs = recipe.get("checkoutSCM", [])
        for scm in self.__checkoutSCMs:
            scm["__source"] = sourceName
            scm["recipe"] = sourceFile
        self.__checkoutAsserts = recipe.get("checkoutAssert", [])
        i = 0
        for a in self.__checkoutAsserts:
            a["__source"] = sourceName + ", checkoutAssert #{}".format(i)
            i += 1
        self.__checkoutUpdateIf = recipe["checkoutUpdateIf"]
        self.__build = fetchScripts(recipe, "build", incHelperBash, incHelperPwsh, incHelperPython)
        self.__package = fetchScripts(recipe, "package", incHelperBash, incHelperPwsh, incHelperPython)
        self.__fingerprintScriptList = fetchFingerprintScripts(recipe)
        self.__fingerprintIf = recipe.get("fingerprintIf")
        self.__fingerprintVarsList = set(recipe.get("fingerprintVars", []))

        self.__buildNetAccess = recipe.get("buildNetAccess")
        self.__packageNetAccess = recipe.get("packageNetAccess")

    def __resolveClassesOrder(self, cls, stack, visited, isRecipe=False):
        # prevent cycles
        clsName = "<recipe>" if isRecipe else cls.__packageName
        if clsName in stack:
            raise ParseError("Cyclic class inheritence: " + " -> ".join(stack + [clsName]))

        # depth first
        subInherit = []
        if isRecipe:
            subInherit.extend([ self.__recipeSet.getClass(c)
                                for c in self.__recipeSet.getInheritPrepend() ])
        subInherit.extend([ self.__recipeSet.getClass(c) for c in cls.__inherit ])
        if cls.__anonBaseClass: subInherit.insert(0, cls.__anonBaseClass)
        if isRecipe:
            subInherit.extend([ self.__recipeSet.getClass(c)
                                for c in self.__recipeSet.getInheritAppend() ])

        ret = []
        for c in subInherit:
            ret.extend(self.__resolveClassesOrder(c, stack + [clsName], visited))

        # classes are inherited only once
        if (clsName not in visited) and not isRecipe:
            ret.append(cls)
            visited.add(clsName)

        return ret

    def getLayer(self):
        """Get layer to which this recipe belongs.

        Returns a list of the layer hierarchy. The root layer is represented
        by an empty list. If the recipe belongs to a nested layer the layers
        are named from top to bottom. Example:
        ``layers/foo/layers/bar/recipes/baz.yaml`` -> ``['foo', 'bar']``.

        If the managedLayers policy is set to the new behaviour, nested layers
        are flattened. This means that layers are always returnd as single-item
        lists.

        :rtype: List[str]
        """
        if self.__layer:
            return self.__layer.split("/")
        else:
            return []

    def resolveClasses(self, rootEnv):
        # must be done only once
        if self.__classesResolved: return
        self.__classesResolved = True

        # calculate order of classes (depth first) but ignore ourself
        inherit = self.__resolveClassesOrder(self, [], set(), True)
        inheritAll = inherit + [self]

        # prepare environment merge list
        self.__varSelf = [ self.__varSelf ] if self.__varSelf else []
        self.__varLocal = [ self.__varLocal ] if self.__varLocal else []
        self.__varPrivate = [ self.__varPrivate ] if self.__varPrivate else []

        # first pass: calculate used scripting language
        scriptLanguage = None
        for cls in reversed(inheritAll):
            if scriptLanguage is not None: break
            scriptLanguage = cls.__scriptLanguage
        if scriptLanguage is None:
            self.__scriptLanguage = self.__defaultScriptLanguage
        else:
            self.__scriptLanguage = scriptLanguage
        glue = getLanguage(self.__scriptLanguage).glue

        # Consider checkout deterministic by default if neither checkoutScript
        # nor checkoutFinalize is involved. A potential checkoutSetup is
        # ignored.
        def coDet(r):
            ret = r.__checkoutDeterministic
            if ret is not None:
                return ret
            return all(s[0] is None for s in r.__checkout[self.__scriptLanguage][1:])

        checkoutDeterministic = [ coDet(i) for i in inheritAll ]
        self.__checkoutDeterministic = all(checkoutDeterministic)

        # merge scripts and other lists
        selLang = lambda x: x[self.__scriptLanguage]

        # Join all scripts. The result is a tuple with (setupScript, mainScript, digestScript)
        checkoutScripts = [ selLang(i.__checkout) for i in inheritAll ]
        self.__checkout = mergeScripts(checkoutScripts, glue)
        self.__checkoutSCMs = list(chain.from_iterable(i.__checkoutSCMs for i in inheritAll))
        self.__checkoutAsserts = list(chain.from_iterable(i.__checkoutAsserts for i in inheritAll))
        self.__checkoutUpdateIf = [
            (cond, fragments[1][0], deterministic)
                for cond, fragments, deterministic
                in zip((i.__checkoutUpdateIf for i in inheritAll), checkoutScripts,
                       checkoutDeterministic)
                if cond != False
        ]
        self.__build = mergeScripts([ selLang(i.__build) for i in inheritAll ], glue)
        self.__package = mergeScripts([ selLang(i.__package) for i in inheritAll ], glue)
        self.__fingerprintScriptList = [ i.__fingerprintScriptList for i in inheritAll ]
        self.__fingerprintVarsList = [ i.__fingerprintVarsList for i in inheritAll ]
        self.__fingerprintIf = [ i.__fingerprintIf for i in inheritAll ]

        # inherit classes
        for cls in reversed(inherit):
            self.__sources.extend(cls.__sources)
            self.__deps[0:0] = cls.__deps
            if self.__root is None: self.__root = cls.__root
            if self.__shared is None: self.__shared = cls.__shared
            if self.__relocatable is None: self.__relocatable = cls.__relocatable
            if self.__jobServer is None: self.__jobServer = cls.__jobServer
            if self.__packageDepends is None: self.__packageDepends = cls.__packageDepends
            tmp = cls.__provideTools.copy()
            tmp.update(self.__provideTools)
            self.__provideTools = tmp
            tmp = cls.__provideVars.copy()
            tmp.update(self.__provideVars)
            self.__provideVars = tmp
            self.__provideDeps |= cls.__provideDeps
            if self.__provideSandbox is None: self.__provideSandbox = cls.__provideSandbox
            tmp = cls.__provideInterpreters.copy()
            tmp.update(self.__provideInterpreters)
            self.__provideInterpreters = tmp
            if cls.__varSelf: self.__varSelf.insert(0, cls.__varSelf)
            if cls.__varLocal: self.__varLocal.insert(0, cls.__varLocal)
            if cls.__varPrivate: self.__varPrivate.insert(0, cls.__varPrivate)
            self.__checkoutVars |= cls.__checkoutVars
            tmp = cls.__metaEnv.copy()
            tmp.update(self.__metaEnv)
            self.__metaEnv = tmp
            self.__checkoutVarsWeak |= cls.__checkoutVarsWeak
            self.__buildVars |= cls.__buildVars
            self.__buildVarsWeak |= cls.__buildVarsWeak
            self.__packageVars |= cls.__packageVars
            self.__packageVarsWeak |= cls.__packageVarsWeak
            self.__toolDepCheckout.extend(cls.__toolDepCheckout)
            self.__toolDepCheckoutWeak.extend(cls.__toolDepCheckoutWeak)
            self.__toolDepBuild.extend(cls.__toolDepBuild)
            self.__toolDepBuildWeak.extend(cls.__toolDepBuildWeak)
            self.__toolDepPackage.extend(cls.__toolDepPackage)
            self.__toolDepPackageWeak.extend(cls.__toolDepPackageWeak)
            if self.__buildNetAccess is None: self.__buildNetAccess = cls.__buildNetAccess
            if self.__packageNetAccess is None: self.__packageNetAccess = cls.__packageNetAccess
            for (n, p) in self.__properties.items():
                p.inherit(cls.__properties[n])
            tmp = cls.__checkoutAuditFiles.copy()
            tmp.update(self.__checkoutAuditFiles)
            self.__checkoutAuditFiles = tmp
            tmp = cls.__buildAuditFiles.copy()
            tmp.update(self.__buildAuditFiles)
            self.__buildAuditFiles = tmp
            tmp = cls.__packageAuditFiles.copy()
            tmp.update(self.__packageAuditFiles)
            self.__packageAuditFiles = tmp

        # the package step must always be valid
        if self.__package[1] is None:
            self.__package = (None, "", 'da39a3ee5e6b4b0d3255bfef95601890afd80709', {})

        if self.__relocatable is None:
            self.__relocatable = True

        if self.__jobServer is None:
            self.__jobServer = False

        if self.__packageDepends is None:
            self.__packageDepends = False

        # Optimize provideDeps
        self.__provideDeps = [ getProvideDepsResolver(d) for d in self.__provideDeps ]

        # Evaluate root property
        if isinstance(self.__root, str) or isinstance(self.__root, IfExpression):
            self.__root = rootEnv.evaluate(self.__root, "root")

    def getRecipeSet(self):
        """Get the :class:`RecipeSet` to which the recipe belongs"""
        return self.__recipeSet

    def getPrimarySource(self):
        return self.__primarySource

    def getSources(self):
        return self.__sources

    def getPackageName(self):
        """Get the name of the package that is drived from this recipe.

        Usually the package name is the same as the recipe name. But in case of
        a ``multiPackage`` the package name has an additional suffix.
        """
        return self.__packageName

    def getName(self):
        """Get plain recipe name.

        In case of a ``multiPackage`` multiple packages may be derived from the
        same recipe. This method returns the plain recipe name.
        """
        return self.__baseName

    def isRoot(self):
        """Returns True if this is a root recipe."""
        return self.__root == True

    def isRelocatable(self):
        """Returns True if the packages of this recipe are relocatable.

        :meta private:
        """
        return self.__relocatable

    def jobServer(self):
        """Returns True if the jobserver should be used to schedule builds for
        this recipe.

        :meta private:
        """
        if self.__jobServer == True:
            return "pipe"
        else:
            return self.__jobServer

    def getPluginProperties(self):
        """Get all plugin defined properties of recipe.

        The values of all properties have their final value, i.e. after all
        classes have been resolved.

        :return: Plugin defined properties of recipe
        :rtype: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        return self.__properties

    def prepare(self, inputEnv, sandboxEnabled, inputStates, inputSandbox=None,
                inputTools=Env(), inputInterpreters={}, inputStack=PackageStack(), packageName=None,
                inputResolved=None):
        # Cycle detection is based on the recipe package name. Any alias names
        # are ignored.
        if self.__packageName in inputStack:
            raise ParseError("Recipes are cyclic (1st package in cylce)")
        stack = inputStack.push(self.__packageName,
                                self.__packageName if packageName is None else packageName)
        if inputResolved is None:
            inputResolved = ({}, None, {})
        # already calculated?
        for m in self.__corePackagesByMatch:
            if m.matches(inputEnv.detach(), inputTools.detach(), inputStates, inputSandbox, packageName,
                         inputInterpreters):
                if stack.intersects(m.subTreePackages):
                    raise ParseError("Recipes are cyclic")
                m.touch(inputEnv, inputTools)
                if DEBUG['pkgck']:
                    reusedCorePackage = m.corePackage
                    break
                return m.corePackage, m.subTreePackages
        else:
            reusedCorePackage = None

        # Track tool, sandbox and interpreter changes
        diffSandbox = ...
        diffTools = { }
        diffInterpreters = {}

        # make copies because we will modify them
        states = { n : s.copy() for (n,s) in inputStates.items() }
        sandbox = inputSandbox
        inputTools = inputTools.copy()
        inputTools.touchReset()
        tools = inputTools.derive()
        interpreters = inputInterpreters.copy()
        inputEnv = inputEnv.derive()
        inputEnv.touchReset()
        inputEnv.setFunArgs({ "recipe" : self, "recipeSet" : self.__recipeSet,
            "sandbox" : bool(sandbox) and sandboxEnabled, "__tools" : tools,
            "states" : states })
        env = inputEnv.derive()
        for i in self.__varSelf:
            env.update(env.substituteCondDict(i, "environment"))

        # update plugin states
        for s in states.values(): s.onEnter(env, self.__properties)

        # Dependencies inherit the environment up to here. The local
        # environment is only visible to the current recipe.
        depEnv = env.derive()
        for i in self.__varLocal:
            env.update(env.substituteCondDict(i, "localEnvironment"))

        # traverse dependencies
        subTreePackages = set()
        directPackages = []
        injectedPackages = []
        def raiseIncompatible(name, path, left, right):
            self.__raiseIncompatibleProvided(name, path, left, right, inputResolved)

        provideDeps = UniquePackageList(stack.getNameStack(), raiseIncompatible)
        maybeProvideDeps = []
        checkoutDeps = []
        results = []
        depTools = tools.derive()
        depSandbox = sandbox
        depInterpreters = interpreters.copy()
        depStates = { n : s.copy() for (n,s) in states.items() }
        depDiffSandbox = diffSandbox
        depDiffTools = diffTools.copy()
        depDiffInterpreters = {}
        thisDeps = {}
        resolvedDeps = []

        for dep in self.__deps:
            env.setFunArgs({ "recipe" : self, "recipeSet" : self.__recipeSet,
                "sandbox" : bool(sandbox) and sandboxEnabled, "__tools" : tools,
                "states" : states })

            skip = dep.condition and not all(env.evaluate(cond, "dependency "+dep.recipe)
                                                          for cond in dep.condition)
            # The dependency name is always substituted because we allow
            # provideDeps to name a disabled dependency. But in case the
            # substiturion fails, the error is silently ignored.
            try:
                recipeName = env.substitute(dep.recipe, "dependency::"+dep.recipe)
                if dep.alias is None:
                    aliasName = None
                    realName = recipeName
                else:
                    realName = aliasName = env.substitute(dep.alias, "alias::"+dep.alias)
                    if not ALIAS_NAME_RE.fullmatch(aliasName):
                        raise ParseError(f"Invalid dependency alias: {aliasName}")
                resolvedDeps.append(realName)
            except ParseError:
                if skip: continue
                raise
            if skip: continue

            thisDepEnv = depEnv
            thisDepTools = depTools
            thisDepDiffTools = depDiffTools
            thisDepSandbox = depSandbox
            thisDepDiffSandbox = depDiffSandbox
            thisDepInterpreters = depInterpreters
            thisDepDiffInterpreters = depDiffInterpreters
            if not dep.inherit:
                thisDepEnv = self.getRecipeSet().getRootEnv()
                thisDepTools = Env()
                # Compute the diff to remove all tools that were passed to the
                # package.
                thisDepDiffTools = { n : None for n in inputTools.inspect().keys() }
                thisDepSandbox = None
                # Clear sandbox, if any
                thisDepDiffSandbox = None
                thisDepInterpreters = {}
                # Remove all tools that were passed to package.
                thisDepDiffInterpreters = { n : None for n in inputInterpreters.keys() }

            if dep.toolOverride:
                try:
                    thisDepTools = thisDepTools.derive({
                        k : depTools[v] for k,v in dep.toolOverride.items() })
                except KeyError as e:
                    raise ParseError("Cannot remap unkown tool '{}' for dependency '{}'!"
                        .format(e.args[0], realName))
                thisDepDiffTools = thisDepDiffTools.copy()
                thisDepDiffTools.update({
                    k : depDiffTools.get(v, v)
                    for k, v in dep.toolOverride.items() })

            if dep.envOverride:
                thisDepEnv = thisDepEnv.derive(
                    env.substituteCondDict(dep.envOverride, "depends["+realName+"].environment"))

            r = self.__recipeSet.getRecipe(recipeName)
            try:
                resolved = CoreRef(corePackageInternal, [], thisDepDiffTools,
                    thisDepDiffSandbox, thisDepDiffInterpreters).refDeref(
                        stack.getNameStack(), *inputResolved, PathsConfig(None, False))
                p, s = r.prepare(thisDepEnv, sandboxEnabled, depStates,
                                 thisDepSandbox, thisDepTools, thisDepInterpreters, stack, aliasName,
                                 resolved)
                subTreePackages.add(recipeName)
                subTreePackages.update(s)
                depCoreStep = p.getCorePackageStep()
                # The stack is never modified. Share it between all references
                # to keep the number of objects low.
                depStackAdd = [p.name]
                depRef = CoreRef(depCoreStep, depStackAdd, thisDepDiffTools, thisDepDiffSandbox,
                                 thisDepDiffInterpreters)
            except ParseError as e:
                e.pushFrame(r.getPackageName())
                raise e

            # A dependency should be named only once. Hence we can
            # optimistically create the DepTracker object. If the dependency is
            # named more than one we make sure that it is the same variant.
            depTrack = thisDeps.setdefault(p.name, DepTracker(depRef, dep))
            if depTrack.prime():
                directPackages.append(depRef)
            elif depCoreStep.variantId != depTrack.item.refGetDestination().variantId:
                self.__raiseIncompatibleLocal(depCoreStep)
            else:
                sources = " and ".join(set([dep.origin.getPrimarySource(), depTrack.depEntry.origin.getPrimarySource()]))
                raise ParseError("Duplicate dependency '{}'. Each dependency must only be named once!"
                                    .format(p.name),
                                 help=f"The dependencies were declared in {sources}.")

            # Remember dependency diffs before changing them
            origDepDiffTools = thisDepDiffTools
            origDepDiffSandbox = thisDepDiffSandbox
            origDepDiffInterpreters = thisDepDiffInterpreters

            # pick up various results of package
            for (n, s) in states.items():
                if n in dep.use:
                    s.onUse(depCoreStep.corePackage.states[n])
                    if dep.provideGlobal: depStates[n].onUse(depCoreStep.corePackage.states[n])
                else:
                    s.onSkip(depCoreStep.corePackage.states[n])
                    if dep.provideGlobal: depStates[n].onSkip(depCoreStep.corePackage.states[n])
            if dep.useDeps:
                injectedPackages.extend(
                    CoreRef(d, depStackAdd, origDepDiffTools, origDepDiffSandbox, origDepDiffInterpreters)
                    for d in depCoreStep.providedDeps)
            if dep.useBuildResult and depTrack.useResultOnce():
                results.append(depRef)
                if dep.checkoutDep: checkoutDeps.append(depRef)
            if dep.useTools:
                tools.update(depCoreStep.providedTools)
                diffTools.update( (n, CoreRef(d, depStackAdd, origDepDiffTools, origDepDiffSandbox,
                                              origDepDiffInterpreters))
                    for n, d in depCoreStep.providedTools.items() )
                if dep.provideGlobal:
                    depTools.update(depCoreStep.providedTools)
                    depDiffTools = depDiffTools.copy()
                    depDiffTools.update( (n, CoreRef(d, depStackAdd, origDepDiffTools, origDepDiffSandbox,
                                                     origDepDiffInterpreters))
                        for n, d in depCoreStep.providedTools.items() )
            if dep.useEnv:
                env.update(depCoreStep.providedEnv)
                if dep.provideGlobal: depEnv.update(depCoreStep.providedEnv)
            if dep.useSandbox and (depCoreStep.providedSandbox is not None):
                sandbox = depCoreStep.providedSandbox
                diffSandbox = CoreRef(depCoreStep.providedSandbox, depStackAdd, origDepDiffTools,
                    origDepDiffSandbox, origDepDiffInterpreters)
                if dep.provideGlobal:
                    depSandbox = sandbox
                    depDiffSandbox = diffSandbox
                if sandboxEnabled:
                    env.update(sandbox.environment)
                    if dep.provideGlobal: depEnv.update(sandbox.environment)
            if dep.useInterpreters:
                interpreters.update(depCoreStep.providedInterpreters)
                diffInterpreters.update( (n, CoreRef(d, depStackAdd, origDepDiffTools, origDepDiffSandbox,
                                                     origDepDiffInterpreters))
                    for n, d in depCoreStep.providedInterpreters.items() )
                if dep.provideGlobal:
                    depInterpreters.update(depCoreStep.providedInterpreters)
                    depDiffInterpreters = depDiffInterpreters.copy()
                    depDiffInterpreters.update( (n, CoreRef(d, depStackAdd, origDepDiffTools,
                                                            origDepDiffSandbox, origDepDiffInterpreters))
                        for n, d in depCoreStep.providedInterpreters.items() )

            maybeProvideDeps.append((p.name, depStackAdd, depRef, origDepDiffTools, origDepDiffSandbox,
                                     origDepDiffInterpreters))

        # check provided dependencies
        providedDeps = set()
        for pattern in self.__provideDeps:
            l = pattern.resolve(env, resolvedDeps)
            if not l:
                raise ParseError("Unknown dependency '{}' in provideDeps".format(pattern.pattern))
            providedDeps |= l

        for (name, depStackAdd, depRef, origDepDiffTools, origDepDiffSandbox, origDepDiffInterpreters) \
                in maybeProvideDeps:
            if name in providedDeps:
                provideDeps.append(depRef)
                provideDeps.extend([CoreRef(d, depStackAdd, origDepDiffTools, origDepDiffSandbox, origDepDiffInterpreters)
                    for d in depRef.refGetDestination().providedDeps])

        # Filter injected packages and add to result list if necessary. Most
        # likely there are many duplicates that are dropped.
        tmp = injectedPackages
        injectedPackages = []
        for depRef in tmp:
            depCoreStep = depRef.refGetDestination()
            name = depCoreStep.corePackage.name
            depTrack = thisDeps.get(name)
            if depTrack is None:
                thisDeps[name] = depTrack = DepTracker(depRef, None)

            if depTrack.prime():
                injectedPackages.append(depRef)
            elif depCoreStep.variantId != depTrack.item.refGetDestination().variantId:
                self.__raiseIncompatibleProvided(name, stack.getNameStack(),
                    depRef, depTrack.item, inputResolved)

            if depTrack.useResultOnce():
                results.append(depRef)

        # Calculate used tools. They are conditional.
        toolsView = tools.inspect()
        env.setFunArgs({ "recipe" : self, "recipeSet" : self.__recipeSet,
            "sandbox" : bool(sandbox) and sandboxEnabled, "__tools" : tools,
            "states" : states })

        toolDepCheckout = set(name for (name, cond) in self.__toolDepCheckout
                              if env.evaluate(cond, "checkoutTools"))
        toolDepCheckoutWeak = set(name for (name, cond) in self.__toolDepCheckoutWeak
                                  if env.evaluate(cond, "checkoutToolsWeak"))
        addTransitiveTools(toolDepCheckout, toolDepCheckoutWeak, toolsView)

        toolDepBuild = set(name for (name, cond) in self.__toolDepBuild
                           if env.evaluate(cond, "buildTools")) | toolDepCheckout
        toolDepBuildWeak = set(name for (name, cond) in self.__toolDepBuildWeak
                               if env.evaluate(cond, "buildToolsWeak")) | toolDepCheckoutWeak
        addTransitiveTools(toolDepBuild, toolDepBuildWeak, toolsView)

        toolDepPackage = set(name for (name, cond) in self.__toolDepPackage
                           if env.evaluate(cond, "packageTools")) | toolDepBuild
        toolDepPackageWeak = set(name for (name, cond) in self.__toolDepPackageWeak
                               if env.evaluate(cond, "packageToolsWeak")) | toolDepBuildWeak
        addTransitiveTools(toolDepPackage, toolDepPackageWeak, toolsView)

        # Only keep weak tools that are not strong at the same time.
        toolDepCheckoutWeak -= toolDepCheckout
        toolDepCheckout |= toolDepCheckoutWeak
        toolDepBuildWeak -= toolDepBuild
        toolDepBuild |= toolDepBuildWeak
        toolDepPackageWeak -= toolDepPackage
        toolDepPackage |= toolDepPackageWeak

        # apply tool environments
        toolsEnv = set()
        for i in toolDepPackage:
            tool = toolsView.get(i)
            if tool is None: continue
            if not tool.environment: continue
            tmp = set(tool.environment.keys())
            if not tmp.isdisjoint(toolsEnv):
                self.__raiseIncompatibleTools(toolsView, toolDepPackage)
            toolsEnv.update(tmp)
            env.update(tool.environment)

        # apply private environment
        for i in self.__varPrivate:
            env.update(env.substituteCondDict(i, "privateEnvironment"))

        # meta variables override existing variables
        if self.__recipeSet.getPolicy('substituteMetaEnv'):
            metaEnv = env.substituteCondDict(self.__metaEnv, "metaEnvironment")
        else:
            metaEnv = { k : v[0] for k, v in self.__metaEnv.items() if env.evaluate(v[1], k) }
        env.update(metaEnv)

        # set fixed built-in variables
        env['BOB_RECIPE_NAME'] = self.__baseName
        env['BOB_PACKAGE_NAME'] = self.__packageName if packageName is None else packageName

        # update plugin states
        for s in states.values(): s.onFinish(env, self.__properties)

        # calculate shared property
        if isinstance(self.__shared, str) or isinstance(self.__shared, IfExpression):
            isShared = env.evaluate(self.__shared, "shared")
        else:
            isShared = self.__shared == True

        # record used environment and tools
        env.touch(self.__packageVars | self.__packageVarsWeak)
        tools.touch(toolDepPackage)

        # Check if fingerprinting has to be applied. At least one
        # 'fingerprintIf' must evaluate to 'True'. The mask of included
        # fingerprints is stored in the package instead of the final string to
        # save memory.
        doFingerprint = 0
        doFingerprintMaybe = 0
        mask = 1
        fingerprintConditions = chain(
            ((t.fingerprintIf if t is not None else False)
                for t in (toolsView.get(i) for i in sorted(toolDepPackage))),
            self.__fingerprintIf)
        for fingerprintIf in fingerprintConditions:
            if fingerprintIf is None:
                doFingerprintMaybe |= mask << 1
            elif fingerprintIf == True:
                doFingerprint |= mask
            elif (isinstance(fingerprintIf, str) or isinstance(fingerprintIf, IfExpression)) \
                 and env.evaluate(fingerprintIf, "fingerprintIf"):
                doFingerprint |= mask
            mask <<= 2
        if doFingerprint:
            doFingerprint |= doFingerprintMaybe

        # Remove bits of all tools that are not used in buildStep. In case
        # you're wondering: the checkout step is never fingerprinted because
        # the sources are hashed.
        doFingerprintBuild = doFingerprint
        mask = 3
        for t in sorted(toolDepPackage):
            if t not in toolDepBuild:
                doFingerprintBuild &= ~mask
            mask <<= 2

        # check that all tools that are required are available
        toolsDetached = tools.detach()
        if self.__recipeSet.getPolicy('noUndefinedTools') and \
           not set(toolsDetached.keys()).issuperset(toolDepPackage):
            raise ParseError("Missing tools: " + ", ".join(sorted(
                set(toolDepPackage) - set(toolsDetached.keys()))))

        # create package
        # touchedTools = tools.touchedKeys()
        # diffTools = { n : t for n,t in diffTools.items() if n in touchedTools }
        p = CorePackage(self, toolsDetached, diffTools, sandbox, diffSandbox,
                interpreters, diffInterpreters, directPackages, injectedPackages,
                states, uidGen(), metaEnv, packageName, isShared)

        # optional checkout step
        if self.__checkout != (None, None, None, {}) or self.__checkoutSCMs or self.__checkoutAsserts:
            checkoutDigestEnv = env.prune(self.__checkoutVars)
            checkoutEnv = ( env.prune(self.__checkoutVars | self.__checkoutVarsWeak)
                if self.__checkoutVarsWeak else checkoutDigestEnv )
            checkoutUpdateIf = [
                ( (env.evaluate(cond, "checkoutUpdateIf")
                    if (isinstance(cond, str) or isinstance(cond, IfExpression))
                    else cond),
                  script,
                  deterministic)
                for cond, script, deterministic in self.__checkoutUpdateIf ]
            if any(cond == True for cond, _, _ in checkoutUpdateIf):
                checkoutUpdateDeterministic = all(
                    deterministic for cond, _, deterministic in checkoutUpdateIf
                    if cond != False)
                checkoutUpdateIf = [ script for cond, script, _ in checkoutUpdateIf if cond != False ]
            else:
                checkoutUpdateDeterministic = True
                checkoutUpdateIf = []
            srcCoreStep = p.createCoreCheckoutStep(self.__checkout,
                self.__checkoutSCMs, env, checkoutDigestEnv, checkoutEnv,
                checkoutDeps, checkoutUpdateIf, checkoutUpdateDeterministic,
                toolDepCheckout, toolDepCheckoutWeak,
                substituteAuditFiles("checkoutAuditFiles", self.__checkoutAuditFiles, env))
        else:
            srcCoreStep = p.createInvalidCoreCheckoutStep()

        # optional build step
        if self.__build != (None, None, None, {}):
            buildDigestEnv = env.prune(self.__buildVars)
            buildEnv = ( env.prune(self.__buildVars | self.__buildVarsWeak)
                if self.__buildVarsWeak else buildDigestEnv )
            buildCoreStep = p.createCoreBuildStep(self.__build, buildDigestEnv, buildEnv,
                [CoreRef(srcCoreStep)] + results, doFingerprintBuild, toolDepBuild, toolDepBuildWeak,
                substituteAuditFiles("buildAuditFiles", self.__buildAuditFiles, env))
        else:
            buildCoreStep = p.createInvalidCoreBuildStep([CoreRef(srcCoreStep)] + results)

        # mandatory package step
        packageDigestEnv = env.prune(self.__packageVars)
        packageEnv = ( env.prune(self.__packageVars | self.__packageVarsWeak)
            if self.__packageVarsWeak else packageDigestEnv )
        packageDeps = [CoreRef(buildCoreStep)]
        if self.__packageDepends:
            packageDeps.extend(results)
        packageCoreStep = p.createCorePackageStep(self.__package, packageDigestEnv, packageEnv,
            packageDeps, doFingerprint, toolDepPackage, toolDepPackageWeak,
            substituteAuditFiles("packageAuditFiles", self.__packageAuditFiles, env))

        # provide environment
        packageCoreStep.providedEnv = env.substituteCondDict(self.__provideVars, "provideVars")

        # provide tools
        packageCoreStep.providedTools = { name : tool.prepare(packageCoreStep, env)
            for (name, tool) in self.__provideTools.items() }

        # provide deps (named in provideDeps and their provided deps)
        packageCoreStep.providedDeps = provideDeps.result()

        # provide Sandbox
        if self.__provideSandbox:
            packageCoreStep.providedSandbox = CoreSandbox(packageCoreStep,
                env, sandboxEnabled, self.__provideSandbox)

        # provide interpreters
        packageCoreStep.providedInterpreters = {
            lang: CoreInterpreter(packageCoreStep, path)
            for lang, path in self.__provideInterpreters.items() }

        if self.__shared:
            if not packageCoreStep.isDeterministic():
                raise ParseError("Shared packages must be deterministic!")

        # remember calculated package
        if reusedCorePackage is None:
            pid = packageCoreStep.getResultId()
            reusableCorePackage = self.__corePackagesById.setdefault(pid, p)
            if reusableCorePackage is not p:
                p = reusableCorePackage
            self.__corePackagesByMatch.insert(0, PackageMatcher(
                reusableCorePackage, inputEnv, inputTools, inputStates,
                inputSandbox, subTreePackages, packageName, inputInterpreters))
        elif packageCoreStep.getResultId() != reusedCorePackage.getCorePackageStep().getResultId():
            raise AssertionError("Wrong reusage for " + "/".join(stack.getNameStack()))
        else:
            # drop calculated package to keep memory consumption low
            p = reusedCorePackage

        return p, subTreePackages

    @property
    def buildNetAccess(self):
        if self.__buildNetAccess is None:
            return False
        else:
            return self.__buildNetAccess

    @property
    def packageNetAccess(self):
        if self.__packageNetAccess is None:
            return False
        else:
            return self.__packageNetAccess

    def __raiseIncompatibleProvided(self, name, stack, ref1, ref2, inputResolved):
        from ..variant import findVariantDifference, renderVariantDifferences
        pathsConfig = PathsConfig(None, False)
        left = ref1.refDeref(stack, *inputResolved, pathsConfig)
        right = ref2.refDeref(stack, *inputResolved, pathsConfig)
        pair = findVariantDifference(left, right)
        if pair:
            detail = "\nConcrete differences:\n" + renderVariantDifferences(pair)
        else:
            detail = "\nNo concrete package difference could be identified."
        raise ParseError("Incompatible variants of package: {} vs. {}"
            .format("/".join(stack + ref1.refGetStack()), "/".join(stack + ref2.refGetStack())),
            help=
"""This error is caused by '{PKG}' that is passed upwards via 'provideDeps' from multiple dependencies of '{CUR}'.
These dependencies constitute different variants of '{PKG}' and can therefore not be used in '{CUR}'."""
    .format(PKG=name, CUR=self.__packageName) + detail)

    def __raiseIncompatibleLocal(self, r):
        raise ParseError("Multiple incompatible dependencies to package: {}"
            .format(r.corePackage.name),
            help=
"""This error is caused by naming '{PKG}' multiple times in the recipe with incompatible variants.
Every dependency must only be given once."""
    .format(PKG=r.corePackage.name))

    def __raiseIncompatibleTools(self, tools, toolDepPackage):
        toolsVars = {}
        for i in toolDepPackage:
            tool = tools.get(i)
            if tool is None: continue
            for k in tool.environment.keys():
                toolsVars.setdefault(k, []).append(i)
        toolsVars = ", ".join(sorted(
            "'{}' defined by {}".format(k, " and ".join(sorted(v)))
            for k,v in toolsVars.items() if len(v) > 1))
        raise ParseError("Multiple tools defined the same environment variable(s): {}"
            .format(toolsVars),
            help="Each environment variable must be defined only by one used tool.")

    @property
    def checkoutSetupScript(self):
        return self.__checkout[0] or ""

    @property
    def checkoutMainScript(self):
        return self.__checkout[1] or ""

    @property
    def checkoutDigestScript(self):
        return self.__checkout[2] or ""

    @property
    def checkoutIncludedFiles(self):
        return self.__checkout[3]

    @property
    def checkoutDeterministic(self):
        return self.__checkoutDeterministic

    @property
    def checkoutAsserts(self):
        return self.__checkoutAsserts

    @property
    def checkoutVars(self):
        return self.__checkoutVars

    @property
    def checkoutVarsWeak(self):
        return self.__checkoutVarsWeak - self.__checkoutVars

    @property
    def buildSetupScript(self):
        return self.__build[0] or ""

    @property
    def buildMainScript(self):
        return self.__build[1] or ""

    @property
    def buildDigestScript(self):
        return self.__build[2]

    @property
    def buildIncludedFiles(self):
        return self.__build[3]

    @property
    def buildVars(self):
        return self.__buildVars

    @property
    def buildVarsWeak(self):
        return self.__buildVarsWeak - self.__buildVars

    @property
    def packageSetupScript(self):
        return self.__package[0] or ""

    @property
    def packageMainScript(self):
        return self.__package[1] or ""

    @property
    def packageDigestScript(self):
        return self.__package[2]

    @property
    def packageIncludedFiles(self):
        return self.__package[3]

    @property
    def packageVars(self):
        return self.__packageVars

    @property
    def packageVarsWeak(self):
        return self.__packageVarsWeak - self.__packageVars

    @property
    def fingerprintScriptList(self):
        return self.__fingerprintScriptList

    @property
    def fingerprintVarsList(self):
        return self.__fingerprintVarsList

    @property
    def scriptLanguage(self):
        return getLanguage(self.__scriptLanguage)

class AliasPackage:
    SCHEMA = schema.Or(
        str,
        {
            "multiPackage" : {
                MULTIPACKAGE_NAME_SCHEMA : str
            }
        })

    @staticmethod
    def loadFromFile(recipeSet, rootDir, fileName):
        baseName = os.path.splitext( fileName )[0].split( os.sep )
        fileName = os.path.join(rootDir, fileName)
        try:
            for n in baseName: RECIPE_NAME_SCHEMA.validate(n)
        except schema.SchemaError as e:
            raise ParseError("Invalid recipe name: '{}'".format(fileName))
        baseName = "::".join( baseName )

        alias = recipeSet.loadYaml(fileName, (AliasPackage.SCHEMA, b''))
        if isinstance(alias, str):
            return [ AliasPackage(recipeSet, alias, baseName, fileName) ]
        else:
            return [
                AliasPackage(recipeSet, subAlias, baseName + ("-"+subName if subName else ""), fileName)
                for (subName, subAlias) in alias["multiPackage"].items()
            ]

    def __init__(self, recipeSet, target, packageName, fileName):
        self.__recipeSet = recipeSet
        self.__target = target
        self.__packageName = packageName
        self.__source = fileName

    def prepare(self, env, sandboxEnabled, states, sandbox, tools, interpreters, stack, packageName=None,
                inputResolved=None):
        target = env.substitute(self.__target, "alias package")
        if packageName is None:
            packageName = self.__packageName
        return self.__recipeSet.getRecipe(target).prepare(env, sandboxEnabled, states, sandbox,
                                                          tools, interpreters, stack, packageName, inputResolved)

    def getPackageName(self):
        return self.__packageName

    def resolveClasses(self, env):
        pass

    def isRoot(self):
        return False

    def getPrimarySource(self):
        return self.__source

    def getSources(self):
        return [self.__source]

class PackageMatcher:
    __slots__ = ( 'corePackage', 'env', 'tools', 'states', 'sandbox',
                  'interpreters', 'subTreePackages', 'packageName')

    def __init__(self, corePackage, env, tools, states, sandbox, subTreePackages, packageName,
                 interpreters):
        self.corePackage = corePackage
        envData = env.inspect()
        self.env = { name : envData.get(name) for name in env.touchedKeys() }
        toolsData = tools.inspect()
        self.tools = { name : (tool.resultId if tool is not None else None)
            for (name, tool) in ( (n, toolsData.get(n)) for n in tools.touchedKeys() ) }
        self.states = { n : s.copy() for (n,s) in states.items() }
        self.sandbox = sandbox.resultId if sandbox is not None else None
        self.interpreters = { lang : (interp.resultId if interp is not None else None)
            for (lang, interp) in ( (l, interpreters.get(l)) for l in ScriptLanguage ) }
        self.subTreePackages = subTreePackages
        self.packageName = packageName

    def matches(self, inputEnv, inputTools, inputStates, inputSandbox, packageName,
                inputInterpreters):
        for (name, env) in self.env.items():
            if env != inputEnv.get(name): return False
        for (name, tool) in self.tools.items():
            match = inputTools.get(name)
            match = match.resultId if match is not None else None
            if tool != match: return False
        match = inputSandbox.resultId if inputSandbox is not None else None
        if self.sandbox != match: return False
        for (lang, interp) in self.interpreters.items():
            match = inputInterpreters.get(lang)
            match = match.resultId if match is not None else None
            if interp != match: return False
        if self.states != inputStates: return False
        if self.packageName != packageName: return False
        return True

    def touch(self, inputEnv, inputTools):
        inputEnv.touch(self.env.keys())
        inputTools.touch(self.tools.keys())
