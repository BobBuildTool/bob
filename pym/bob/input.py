# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_VERSION, BOB_INPUT_HASH, DEBUG
from .errors import ParseError, BobError
from .languages import getLanguage, ScriptLanguage, BashLanguage, PwshLanguage
from .pathspec import PackageSet
from .scm import CvsScm, GitScm, ImportScm, SvnScm, UrlScm, ScmOverride, \
    auditFromDir, auditFromProperties, getScm, SYNTHETIC_SCM_PROPS
from .state import BobState
from .stringparser import checkGlobList, Env, DEFAULT_STRING_FUNS, IfExpression
from .tty import InfoOnce, Warn, WarnOnce, setColorMode, setParallelTUIThreshold
from .utils import asHexStr, joinScripts, compareVersion, binStat, \
    updateDicRecursive, hashString, getPlatformTag, getPlatformString, \
    replacePath, getPlatformEnvWhiteList, isAbsPath
from itertools import chain
from os.path import expanduser
from string import Template
from textwrap import dedent
import copy
import hashlib
import fnmatch
import os, os.path
import pickle
import re
import schema
import sqlite3
import struct
import sys
try:
    from yaml import load as yamlLoad, CSafeLoader as YamlSafeLoader
except ImportError:
    from yaml import load as yamlLoad, SafeLoader as YamlSafeLoader


def isPrefixPath(p1, p2):
    """Check if the initial elements of ``p2`` equal ``p1``.

    :return: True if ``p2`` is a subdirectory or file inside ``p1``, otherwise
             False.
    """
    p1 = os.path.normcase(os.path.normpath(p1)).split(os.sep)
    if p1 == ["."]: p1 = []
    p2 = os.path.normcase(os.path.normpath(p2)).split(os.sep)
    if p2 == ["."]: p2 = []

    if len(p1) > len(p2):
        return False

    return p2[:len(p1)] == p1

def __maybeGlob(pred):
    if pred.startswith("!"):
        pred = pred[1:]
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: False if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: False if elem == pred else prev
    else:
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: True if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: True if elem == pred else prev

def maybeGlob(pattern):
    if isinstance(pattern, list):
        return [ __maybeGlob(p) for p in pattern ]
    else:
        return None

class __uidGen:
    def __init__(self):
        self.cur = 0
    def get(self):
        self.cur += 1
        return self.cur

uidGen = __uidGen().get

class DigestHasher:
    def __init__(self):
        self.__recipes = bytearray()
        self.__host = bytearray()

    def update(self, real):
        """Add bytes to recipe-internal part of digest."""
        self.__recipes.extend(real)

    def fingerprint(self, imag):
        """Add bytes of fingerprint to host part of digest."""
        self.__host.extend(imag)

    def digest(self):
        """Calculate final digest value.

        If no host fingerprints were added only the recipe-internal digest is
        emitted. Otherwise the fingerprint digest is appended. This keeps the
        calculation backwards compatible (Bob <0.15).
        """
        if self.__host:
            return hashlib.sha1(self.__recipes).digest() + \
                   hashlib.sha1(self.__host).digest()
        else:
            return hashlib.sha1(self.__recipes).digest()

    @staticmethod
    def sliceRecipes(digest):
        """Extract recipe-internal digest part."""
        return digest[:20]

    @staticmethod
    def sliceHost(digest):
        """Extract host fingerprint digest part (if any)."""
        return digest[20:]

def fetchFingerprintScripts(recipe):
    return {
        ScriptLanguage.BASH : recipe.get("fingerprintScriptBash",
            recipe.get("fingerprintScript")),
        ScriptLanguage.PWSH : recipe.get("fingerprintScriptPwsh",
            recipe.get("fingerprintScript")),
    }

def fetchScripts(recipe, prefix, resolveBash, resolvePwsh):
    return {
        ScriptLanguage.BASH : (
            resolveBash(recipe.get(prefix + "SetupBash", recipe.get(prefix + "Setup")),
                        prefix + "Setup[Bash]"),
            resolveBash(recipe.get(prefix + "ScriptBash", recipe.get(prefix + "Script")),
                        prefix + "Script[Bash]"),
        ),
        ScriptLanguage.PWSH : (
            resolvePwsh(recipe.get(prefix + "SetupPwsh", recipe.get(prefix + "Setup")),
                        prefix + "Setup[Pwsh]"),
            resolvePwsh(recipe.get(prefix + "ScriptPwsh", recipe.get(prefix + "Script")),
                        prefix + "Script[Pwsh]"),
        )
    }

def mergeScripts(fragments, glue):
    """Join all scripts of the recipe and its classes.

    The result is a tuple with (setupScript, mainScript, digestScript)
    """
    return (
        joinScripts((f[0][0] for f in fragments), glue),
        joinScripts((f[1][0] for f in fragments), glue),
        joinScripts(
            ( joinScripts((f[0][1] for f in fragments), "\n"),
              joinScripts((f[1][1] for f in fragments), "\n"),
            ), "\n")
    )


class PluginProperty:
    """Base class for plugin property handlers.

    A plugin should sub-class this class to parse custom properties in a
    recipe. For each recipe an object of that class is created then. The
    default constructor just stores the *present* and *value* parameters as
    attributes in the object.

    :param bool present: True if property is present in recipe
    :param value: Unmodified value of property from recipe or None if not present.
    """

    def __init__(self, present, value):
        self.present = present
        self.value = value

    def inherit(self, cls):
        """Inherit from a class.

        The default implementation will use the value from the class if the
        property was not present. Otherwise the class value will be ignored.

        :param cls: The property instance of the class
        :type cls: PluginProperty
        """
        if not self.present:
            self.present = cls.present
            self.value = cls.value

    def isPresent(self):
        """Return True if the property was present in the recipe."""
        return self.present

    def getValue(self):
        """Get (parsed) value of the property."""
        return self.value

    @staticmethod
    def validate(data):
        """Validate type of property.

        Ususally the plugin will reimplement this static method and return True
        only if *data* has the expected type. The default implementation will
        always return True.

        :param data: Parsed property data from the recipe
        :return: True if data has expected type, otherwise False.
        """
        return True


class PluginState:
    """Base class for plugin state trackers.

    State trackers are used by plugins to compute the value of one or more
    properties as the dependency tree of all recipes is traversed.

    .. attention::
        Objects of this class are tested for equivalence. The default
        implementation compares all members of the involved objects. If custom
        types are stored in the object you have to provide a suitable
        ``__eq__`` and ``__ne__`` implementation because Python falls back to
        object identity which might not be correct.  If these operators are not
        working correctly then Bob may slow down considerably.
    """

    def __eq__(self, other):
        return vars(self) == vars(other)

    def __ne__(self, other):
        return vars(self) != vars(other)

    def copy(self):
        """Return a copy of the object.

        The default implementation uses copy.deepcopy() which should usually be
        enough. If the plugin uses a sophisticated state tracker, especially
        when holding references to created packages, it might be usefull to
        provide a specialized implementation.
        """
        return copy.deepcopy(self)

    def onEnter(self, env, properties):
        """Begin creation of a package.

        The state tracker is about to witness the creation of a package. The passed
        environment, tools and (custom) properties are in their initial state that
        was inherited from the parent recipe.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        pass

    def onUse(self, downstream):
        """Use provided state of downstream package.

        This method is called if the user added the name of the state tracker
        to the ``use`` clause in the recipe. A state tracker supporting this
        notion should somehow pick up and merge the state of the downstream
        package.

        The default implementation does nothing.

        :param bob.input.PluginState downstream: State of downstream package
        """
        pass

    def onFinish(self, env, properties):
        """Finish creation of a package.

        The package was computed and is about to be created. The passed *env*
        and *properties* have their final state after all downstream
        dependencies have been resolved. The plugin may modify the *env* and
        *properties* to make final adjustments.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        pass


class PluginSetting:
    priority = 50

    """Base class for plugin settings.

    Plugins can be configured in the user configuration of a project. The
    plugin must derive from this class, create an object with the default value
    and assign it to 'settings' in the plugin manifest. The default
    constructor will just store the passed value in the ``settings`` member.

    :param settings: The default settings
    """

    def __init__(self, settings):
        self.settings = settings

    def merge(self, other):
        """Merge other settings into current ones.

        This method is called when other configuration files with a higher
        precedence have been parsed. The settings in these files are first
        validated by invoking the ``validate`` static method. Then this method
        is called that should update the current object with the value of
        *other*.

        The default implementation implements the following policy:

        * Dictionaries are merged recursively on a key-by-key basis
        * Lists are appended to each other
        * Everything else in *other* replaces the current settings

        It is assumed that the actual settings are stored in the ``settings``
        member variable.

        :param other: Other settings with higher precedence
        """
        if isinstance(self.settings, dict) and isinstance(other, dict):
            self.settings = updateDicRecursive(self.settings, other)
        elif isinstance(self.settings, list) and isinstance(other, list):
            self.settings = self.settings + other
        else:
            self.settings = other

    def getSettings(self):
        """Getter for settings data."""
        return self.settings

    @staticmethod
    def validate(data):
        """Validate type of settings.

        Ususally the plugin will reimplement this method and return True only
        if *data* has the expected type. The default implementation will always
        return True.

        :param data: Parsed settings data from user configuration
        :return: True if data has expected type, otherwise False.
        """
        return True


class SentinelSetting(PluginSetting):
    """Sentinel for "include" and "require" settings"""

    def __init__(self):
        pass

    def merge(self, other):
        pass


class BuiltinSetting(PluginSetting):
    """Tiny wrapper to define Bob built-in settings"""

    def __init__(self, schema, updater, mangle = False, priority=50):
        self.__schema = schema
        self.__updater = updater
        self.__mangle = mangle
        self.priority = priority

    def merge(self, other):
        self.__updater(self.__schema.validate(other) if self.__mangle else other)

    def validate(self, data):
        try:
            self.__schema.validate(data)
            return True
        except schema.SchemaError:
            return False

def Scm(spec, env, overrides, recipeSet):
    # resolve with environment
    spec = { k : ( env.substitute(v, "checkoutSCM::"+k)
                   if isinstance(v, str) and k not in ('__source', 'recipe')
                   else v )
        for (k, v) in spec.items() }

    # apply overrides before creating scm instances. It's possible to switch the Scm type with an override..
    matchedOverrides = []
    for override in overrides:
        matched, spec = override.mangle(spec, env)
        if matched:
            matchedOverrides.append(override)

    # check schema again if any SCM override matched
    if matchedOverrides:
        try:
            recipeSet.SCM_SCHEMA.validate({ k:v for k,v in spec.items()
                if k not in SYNTHETIC_SCM_PROPS })
        except schema.SchemaError as e:
            raise ParseError("Error validating SCM after applying scmOverrides: {}".format(str(e)))

    # apply scmDefaults
    for k, v in recipeSet.scmDefaults().get(spec['scm'], {}).items():
        spec.setdefault(k, v)

    # create scm instance
    return getScm(spec, matchedOverrides, recipeSet)

class CheckoutAssert:
    __slots__ = ('__source', '__file', '__digestSHA1', '__start', '__end')

    SCHEMA = schema.Schema({
        'file' : str,
        'digestSHA1' : str,
        schema.Optional('start') : schema.Or(str, int),
        schema.Optional('end') : schema.Or(str, int)
    })

    def __init__(self, spec, env=None):
        if env is not None:
            spec = {k: ( env.substitute(v, "checkoutAssert::"+ k) if isinstance(v, str) else v)
                    for (k, v) in spec.items()}
        self.__source = spec['__source']
        self.__file = spec['file']
        self.__digestSHA1 = spec['digestSHA1']
        self.__start = int(spec.get('start', 1))
        self.__end = int(spec.get('end', 0xffffffff))
        if self.__start < 1:
            raise ParseError("CheckoutAssert: First line must be greater than zero")
        if self.__end < 1:
            raise ParseError("CheckoutAssert: Last line must be greater than zero")

    def getProperties(self):
        return {
            '__source' : self.__source,
            'file' : self.__file,
            'digestSHA1' : self.__digestSHA1,
            'start' : self.__start,
            'end' : self.__end,
        }

    def getSource(self):
        return self.__source

    async def invoke(self, invoker):
        h = hashlib.sha1()
        i = 0
        try:
            with open(invoker.joinPath(self.__file), "rb") as f:
                for line in f:
                    i += 1
                    if i < self.__start: continue
                    if (i == self.__start) or (i <= self.__end): h.update(line)
                    if i > self.__end: break
            d = h.digest().hex()
            if d != self.__digestSHA1:
                invoker.fail(self.__file, "digest did not match! expected:", self.__digestSHA1, "got:", d)
        except OSError as e:
            invoker.fail(str(e))

    def asDigestScript(self):
        return self.__file + " " + self.__digestSHA1 + " " + str(self.__start) + " " + str(self.__end)


class PathsConfig:
    def __init__(self, pathFormatter, stablePaths):
        self.pathFormatter = pathFormatter
        self.stablePaths = stablePaths


class CoreRef:
    """Reference from one CoreStep/CorePackage to another one.

    The destination must always be deeper or at the same level in the graph.
    The names that are added to the path stack are given in stackAdd. Because
    identical "core" sub-graphs can be visible to the user under different
    "real" paths we only store the difference between source and destination
    to reconstruct the real values on reference resolution.

    The real difficulty with these references is the handling of the ambient
    tools and the sandbox. Each package has a set of tools and a sandbox
    defined as their input. While iterating of the dependencies new tools or a
    new sandbox can be picked up, creating a "diff" to the input tools/sandbox
    of the package. When later re-creating the real Package/Step classes these
    diffs must be applied on refDeref() so that the reference destination gets
    the correct ambient tools/sandbox again.

    diffTools: A dict. If the value of a tool is "None" the tool is deleted. A
    string will copy the tool from an existing "inputTools". Otherwise the
    value is expected to the another CoreRef that needs to be dereferenced too.
    """

    __slots__ = ('__destination', '__stackAdd', '__diffTools', '__diffSandbox')

    def __init__(self, destination, stackAdd=[], diffTools={}, diffSandbox=...):
        self.__destination = destination
        self.__stackAdd = stackAdd
        self.__diffTools = diffTools
        self.__diffSandbox = diffSandbox

    def refGetDestination(self):
        return self.__destination.refGetDestination()

    def refGetStack(self):
        return self.__stackAdd + self.__destination.refGetStack()

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        if cache is None: cache = {}
        if self.__diffTools:
            tools = inputTools.copy()
            for (name, tool) in self.__diffTools.items():
                if tool is None:
                    del tools[name]
                elif isinstance(tool, str):
                    tools[name] = inputTools[tool]
                else:
                    coreTool = cache.get(tool)
                    if coreTool is None:
                        cache[tool] = coreTool = tool.refDeref(stack, inputTools, inputSandbox, pathsConfig, cache)
                    tools[name] = coreTool
        else:
            tools = inputTools

        if self.__diffSandbox is ...:
            sandbox = inputSandbox
        elif self.__diffSandbox is None:
            sandbox = None
        elif self.__diffSandbox in cache:
            sandbox = cache[self.__diffSandbox]
        else:
            sandbox = self.__diffSandbox.refDeref(stack, inputTools, inputSandbox,
                    pathsConfig, cache)
            cache[self.__diffSandbox] = sandbox

        return self.__destination.refDeref(stack + self.__stackAdd, tools, sandbox, pathsConfig)

class CoreItem:
    __slots__ = []

    def refGetDestination(self):
        return self

    def refGetStack(self):
        return []

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        raise NotImplementedError


class AbstractTool:
    __slots__ = ("path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintIf", "fingerprintVars")

    def __init__(self, spec):
        if isinstance(spec, str):
            self.path = spec
            self.libs = []
            self.netAccess = False
            self.environment = {}
            self.fingerprintScript = { lang : "" for lang in ScriptLanguage }
            self.fingerprintIf = False
            self.fingerprintVars = set()
        else:
            self.path = spec['path']
            self.libs = spec.get('libs', [])
            self.netAccess = spec.get('netAccess', False)
            self.environment = spec.get('environment', {})
            self.fingerprintScript = fetchFingerprintScripts(spec)
            self.fingerprintIf = spec.get("fingerprintIf")
            self.fingerprintVars = set(spec.get("fingerprintVars", []))

    def prepare(self, coreStepRef, env):
        """Create concrete tool for given step."""
        path = env.substitute(self.path, "provideTools::path")
        libs = [ env.substitute(l, "provideTools::libs") for l in self.libs ]
        environment = env.substituteCondDict(self.environment,
                                             "provideTools::environment")
        return CoreTool(coreStepRef, path, libs, self.netAccess, environment,
                        self.fingerprintScript, self.fingerprintIf,
                        self.fingerprintVars)

class CoreTool(CoreItem):
    __slots__ = ("coreStep", "path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintIf", "fingerprintVars", "resultId")

    def __init__(self, coreStep, path, libs, netAccess, environment,
                 fingerprintScript, fingerprintIf, fingerprintVars):
        self.coreStep = coreStep
        self.path = path
        self.libs = libs
        self.netAccess = netAccess
        self.environment = environment
        self.fingerprintScript = fingerprintScript
        self.fingerprintIf = fingerprintIf
        self.fingerprintVars = fingerprintVars

        # Calculate a "resultId" so that only identical tools match
        h = hashlib.sha1()
        h.update(coreStep.variantId)
        h.update(struct.pack("<II", len(path), len(libs)))
        h.update(path.encode("utf8"))
        for l in libs:
            h.update(struct.pack("<I", len(l)))
            h.update(l.encode('utf8'))
        h.update(struct.pack("<?I", netAccess, len(environment)))
        for (key, val) in sorted(environment.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        for val in (fingerprintScript[lang] for lang in ScriptLanguage):
            h.update(struct.pack("<I", len(val)))
            h.update(val.encode('utf8'))
        h.update(struct.pack("<I", len(fingerprintVars)))
        for key in sorted(fingerprintVars):
            h.update(key.encode('utf8'))
        fingerprintIfStr = str(fingerprintIf)
        h.update(struct.pack("<I", len(fingerprintIfStr)))
        h.update(fingerprintIfStr.encode('utf8'))
        self.resultId = h.digest()

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        step = self.coreStep.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        return Tool(step, self.path, self.libs, self.netAccess, self.environment,
                    self.fingerprintScript, self.fingerprintVars)

class Tool:
    """Representation of a tool.

    A tool is made of the result of a package, a relative path into this result
    and some optional relative library paths.
    """

    __slots__ = ("step", "path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintVars")

    def __init__(self, step, path, libs, netAccess, environment, fingerprintScript,
                 fingerprintVars):
        self.step = step
        self.path = path
        self.libs = libs
        self.netAccess = netAccess
        self.environment = environment
        self.fingerprintScript = fingerprintScript
        self.fingerprintVars = fingerprintVars

    def __repr__(self):
        return "Tool({}, {}, {})".format(repr(self.step), self.path, self.libs)

    def __eq__(self, other):
        return isinstance(other, Tool) and (self.step == other.step) and (self.path == other.path) and \
            (self.libs == other.libs) and (self.netAccess == other.netAccess) and \
            (self.environment == other.environment)

    def getStep(self):
        """Return package step that produces the result holding the tool
        binaries/scripts.

        :return: :class:`bob.input.Step`
        """
        return self.step

    def getPath(self):
        """Get relative path into the result."""
        return self.path

    def getLibs(self):
        """Get list of relative library paths into the result.

        :return: List[str]
        """
        return self.libs

    def getNetAccess(self):
        """Does tool require network access?

        This reflects the `netAccess` tool property.

        :return: bool
        """
        return self.netAccess

    def getEnvironment(self):
        """Get environment variables.

        Returns the dictionary of environment variables that are defined by the
        tool.
        """
        return self.environment


class CoreSandbox(CoreItem):
    __slots__ = ("coreStep", "enabled", "paths", "mounts", "environment",
        "resultId", "user")

    def __init__(self, coreStep, env, enabled, spec):
        recipeSet = coreStep.corePackage.recipe.getRecipeSet()
        self.coreStep = coreStep
        self.enabled = enabled
        self.paths = recipeSet.getSandboxPaths() + spec['paths']
        self.mounts = []
        for mount in spec.get('mount', []):
            m = (env.substitute(mount[0], "provideSandbox::mount-from"),
                 env.substitute(mount[1], "provideSandbox::mount-to"),
                 mount[2])
            # silently drop empty mount lines
            if (m[0] != "") and (m[1] != ""):
                self.mounts.append(m)
        self.mounts.extend(recipeSet.getSandboxMounts())
        self.environment = env.substituteCondDict(spec.get('environment', {}),
                                                  "providedSandbox::environment")
        self.user = recipeSet.getSandboxUser() or spec.get('user', "nobody")

        # Calculate a "resultId" so that only identical sandboxes match
        h = hashlib.sha1()
        h.update(self.coreStep.variantId)
        h.update(struct.pack("<I", len(self.paths)))
        for p in self.paths:
            h.update(struct.pack("<I", len(p)))
            h.update(p.encode('utf8'))
        h.update(struct.pack("<I", len(self.mounts)))
        for (mntFrom, mntTo, mntOpts) in self.mounts:
            h.update(struct.pack("<III", len(mntFrom), len(mntTo), len(mntOpts)))
            h.update((mntFrom+mntTo+"".join(mntOpts)).encode('utf8'))
        h.update(struct.pack("<I", len(self.environment)))
        for (key, val) in sorted(self.environment.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        h.update(self.user.encode('utf8'))
        self.resultId = h.digest()

    def __eq__(self, other):
        return isinstance(other, CoreSandbox) and \
            (self.coreStep.variantId == other.coreStep.variantId) and \
            (self.enabled == other.enabled) and \
            (self.paths == other.paths) and \
            (self.mounts == other.mounts) and \
            (self.environment == other.environment) and \
            (self.user == other.user)

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        step = self.coreStep.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        return Sandbox(step, self)

class Sandbox:
    """Represents a sandbox that is used when executing a step."""

    __slots__ = ("step", "coreSandbox")

    def __init__(self, step, coreSandbox):
        self.step = step
        self.coreSandbox = coreSandbox

    def __eq__(self, other):
        return isinstance(other, Sandbox) and (self.coreSandbox == other.coreSandbox)

    def getStep(self):
        """Get the package step that yields the content of the sandbox image."""
        return self.step

    def getPaths(self):
        """Return list of global search paths.

        This is the base $PATH in the sandbox."""
        return self.coreSandbox.paths

    def getMounts(self):
        """Get custom mounts.

        This returns a list of tuples where each tuple has the format
        (hostPath, sandboxPath, options).
        """
        return self.coreSandbox.mounts

    def getEnvironment(self):
        """Get environment variables.

        Returns the dictionary of environment variables that are defined by the
        sandbox.
        """
        return self.coreSandbox.environment

    def isEnabled(self):
        """Return True if the sandbox is used in the current build configuration."""
        return self.coreSandbox.enabled

    def getUser(self):
        """Get user identity in sandbox.

        Returns one of 'nobody', 'root' or '$USER'.
        """
        return self.coreSandbox.user


class CoreStep(CoreItem):
    __slots__ = ( "corePackage", "digestEnv", "env", "args",
        "providedEnv", "providedTools", "providedDeps", "providedSandbox",
        "variantId", "deterministic", "isValid", "toolDep", "toolDepWeak" )

    def __init__(self, corePackage, isValid, deterministic, digestEnv, env, args,
                 toolDep, toolDepWeak):
        self.corePackage = corePackage
        self.isValid = isValid
        self.digestEnv = digestEnv.detach()
        self.env = env.detach()
        self.args = args
        self.toolDep = toolDep
        self.toolDepWeak = toolDepWeak
        self.deterministic = deterministic and all(
            arg.isDeterministic() for arg in self.getAllDepCoreSteps())
        self.variantId = self.getDigest(lambda coreStep: coreStep.variantId)
        self.providedEnv = {}
        self.providedTools = {}
        self.providedDeps = []
        self.providedSandbox = None

    def getPreRunCmds(self):
        return []

    def getJenkinsPreRunCmds(self):
        return []

    def getSetupScript(self):
        raise NotImplementedError

    def getMainScript(self):
        raise NotImplementedError

    def getPostRunCmds(self):
        return []

    def getDigestScript(self):
        raise NotImplementedError

    def getUpdateScript(self):
        return ""

    def getLabel(self):
        raise NotImplementedError

    def isDeterministic(self):
        return self.deterministic

    def isUpdateDeterministic(self):
        return True

    def isCheckoutStep(self):
        return False

    def isBuildStep(self):
        return False

    def isPackageStep(self):
        return False

    def getTools(self):
        if self.isValid:
            toolKeys = self.toolDep
            return { name : tool for name,tool in self.corePackage.tools.items()
                                 if name in toolKeys }
        else:
            return {}

    def getSandbox(self):
        sandbox = self.corePackage.sandbox
        if sandbox and sandbox.enabled and self.isValid:
            return sandbox
        else:
            return None

    def getAllDepCoreSteps(self):
        sandbox = self.getSandbox()
        return [ a.refGetDestination() for a in self.args ] + \
            [ d.coreStep for n,d in sorted(self.getTools().items()) ] + (
            [ sandbox.coreStep] if sandbox else [])

    def getDigest(self, calculate):
        h = DigestHasher()
        if self.isFingerprinted() and self.getSandbox():
            # Add the full variant-id (including fingerprint part!) to the
            # fingerprint of this package. This is important to make separate
            # builds for separate sandboxes, including sandboxes that are
            # itself fingerprinted.
            h.fingerprint(calculate(self.getSandbox().coreStep))
        h.update(b'\x00' * 20) # historically the sandbox digest, see sandboxInvariant policy pre-0.25
        script = self.getDigestScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        tools = self.getTools()
        h.update(struct.pack("<I", len(tools)))
        for (name, tool) in sorted(tools.items(), key=lambda t: t[0]):
            h.update(DigestHasher.sliceRecipes(calculate(tool.coreStep)))
            h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
            h.update(tool.path.encode("utf8"))
            for l in tool.libs:
                h.update(struct.pack("<I", len(l)))
                h.update(l.encode('utf8'))
        h.update(struct.pack("<I", len(self.digestEnv)))
        for (key, val) in sorted(self.digestEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        args = [ arg for arg in (a.refGetDestination() for a in self.args) if arg.isValid ]
        h.update(struct.pack("<I", len(args)))
        for arg in args:
            arg = calculate(arg)
            h.update(DigestHasher.sliceRecipes(arg))
            h.fingerprint(DigestHasher.sliceHost(arg))
        return h.digest()

    def getResultId(self):
        h = hashlib.sha1()
        h.update(self.variantId)
        # Include invalid dependencies. They are needed for traversing dummy
        # packages without a buildScript in path queries. Valid dependencies
        # are already included in the variantId.
        args = [ arg for arg in (a.refGetDestination() for a in self.args) if not arg.isValid ]
        h.update(struct.pack("<I", len(args)))
        for arg in args:
            h.update(arg.getResultId())
        # Include used sandbox. Prevents merging of identical packages that
        # are defined under different sandboxes.
        sandbox = self.getSandbox()
        if sandbox:
            h.update(sandbox.coreStep.variantId)
            h.update(struct.pack("<I", len(sandbox.paths)))
            for p in sandbox.paths:
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        # Include weak tools for the same reason as above.
        weakTools = self.toolDepWeak
        for (name, tool) in sorted(self.getTools().items(), key=lambda t: t[0]):
            if name in weakTools:
                h.update(tool.coreStep.variantId)
                h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
                h.update(tool.path.encode("utf8"))
                for l in tool.libs:
                    h.update(struct.pack("<I", len(l)))
                    h.update(l.encode('utf8'))
        # providedEnv
        h.update(struct.pack("<I", len(self.providedEnv)))
        for (key, val) in sorted(self.providedEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        # providedTools
        providedTools = self.providedTools
        h.update(struct.pack("<I", len(providedTools)))
        for (name, tool) in sorted(providedTools.items()):
            h.update(struct.pack("<I", len(name)))
            h.update(name.encode("utf8"))
            h.update(tool.resultId)
        # provideDeps
        providedDeps = self.providedDeps
        h.update(struct.pack("<I", len(providedDeps)))
        for dep in providedDeps:
            h.update(dep.refGetDestination().variantId)
        # sandbox
        providedSandbox = self.providedSandbox
        if providedSandbox:
            h.update(providedSandbox.resultId)
        else:
            h.update(b'\x00' * 20)
        # Add package name if aliased
        pkgName = self.corePackage.packageName
        if pkgName is not None:
            h.update(pkgName.encode('utf8'))

        return h.digest()

    @property
    def fingerprintMask(self):
        raise NotImplementedError

    def isFingerprinted(self):
        return self.fingerprintMask != 0

    @property
    def jobServer(self):
        return self.corePackage.recipe.jobServer

class Step:
    """Represents the smallest unit of execution of a package.

    A step is what gets actually executed when building packages.

    Steps can be compared and sorted. This is done based on the Variant-Id of
    the step. See :meth:`bob.input.Step.getVariantId` for details.
    """

    def __init__(self, coreStep, package, pathsConfig):
        self._coreStep = coreStep
        self.__package = package
        self.__pathsConfig = pathsConfig

    def __repr__(self):
        return "Step({}, {}, {})".format(self.getLabel(), "/".join(self.getPackage().getStack()), asHexStr(self.getVariantId()))

    def __hash__(self):
        return hash(self._coreStep.variantId)

    def __lt__(self, other):
        return self._coreStep.variantId < other._coreStep.variantId

    def __le__(self, other):
        return self._coreStep.variantId <= other._coreStep.variantId

    def __eq__(self, other):
        return self._coreStep.variantId == other._coreStep.variantId

    def __ne__(self, other):
        return self._coreStep.variantId != other._coreStep.variantId

    def __gt__(self, other):
        return self._coreStep.variantId > other._coreStep.variantId

    def __ge__(self, other):
        return self._coreStep.variantId >= other._coreStep.variantId

    def getPreRunCmds(self):
        return self._coreStep.getPreRunCmds()

    def getJenkinsPreRunCmds(self):
        return self._coreStep.getJenkinsPreRunCmds()

    def getScript(self):
        """Return a single big script of the whole step.

        Besides considerations of special backends (such as Jenkins) this
        script is what should be executed to build this step."""
        return joinScripts([self.getSetupScript(), self.getMainScript()],
            self.getPackage().getRecipe().scriptLanguage.glue) or ""

    def getJenkinsScript(self):
        import warnings
        warnings.warn("getJenkinsScript is deprecated", DeprecationWarning)
        """Return the relevant parts as shell script that have no Jenkins plugin.

        Deprecated. Returns the same script as bob.input.Step.getScript()
        """
        return self.getScript()

    def getSetupScript(self):
        return self._coreStep.getSetupScript()

    def getMainScript(self):
        return self._coreStep.getMainScript()

    def getPostRunCmds(self):
        return self._coreStep.getPostRunCmds()

    def getDigestScript(self):
        """Return a long term stable script.

        The digest script will not be executed but is the basis to calculate if
        the step has changed. In case of the checkout step the involved SCMs will
        return a stable representation of *what* is checked out and not the real
        script of *how* this is done.
        """
        return self._coreStep.getDigestScript()

    def getUpdateScript(self):
        return self._coreStep.getUpdateScript()

    def isUpdateDeterministic(self):
        return self._coreStep.isUpdateDeterministic()

    def isDeterministic(self):
        """Return whether the step is deterministic.

        Checkout steps that have a script are considered indeterministic unless
        the recipe declares it otherwise (checkoutDeterministic). Then the SCMs
        are checked if they all consider themselves deterministic. Build and
        package steps are always deterministic.

        The determinism is defined recursively for all arguments, tools and the
        sandbox of the step too. That is, the step is only deterministic if all
        its dependencies and this step itself is deterministic.
        """
        return self._coreStep.isDeterministic()

    def isValid(self):
        """Returns True if this step is valid, False otherwise."""
        return self._coreStep.isValid

    def isCheckoutStep(self):
        """Return True if this is a checkout step."""
        return self._coreStep.isCheckoutStep()

    def isBuildStep(self):
        """Return True if this is a build step."""
        return self._coreStep.isBuildStep()

    def isPackageStep(self):
        """Return True if this is a package step."""
        return self._coreStep.isPackageStep()

    def getPackage(self):
        """Get Package object that is the parent of this Step."""
        return self.__package

    def getVariantId(self):
        """Return Variant-Id of this Step.

        The Variant-Id is used to distinguish different packages or multiple
        variants of a package. Each Variant-Id need only be built once but
        subsequent builds might yield different results (e.g. when building
        from branches)."""
        return self._coreStep.variantId

    def getSandbox(self):
        """Return Sandbox used in this Step.

        Returns a Sandbox object or None if this Step is built without one.
        """
        sandbox = self.__package._getSandboxRaw()
        if sandbox and sandbox.isEnabled() and self._coreStep.isValid:
            return sandbox
        else:
            return None

    def getLabel(self):
        """Return path label for step.

        This is currently defined as "src", "build" and "dist" for the
        respective steps.
        """
        return self._coreStep.getLabel()

    def getWorkspacePath(self):
        """Return the workspace path of the step.

        The workspace path represents the location of the step in the user's
        workspace. When building in a sandbox this path is not passed to the
        script but the one from getExecPath() instead.
        """
        if self.isValid():
            return self.__pathsConfig.pathFormatter(self, self.__package.getPluginStates())
        else:
            return "/invalid/workspace/path/of/{}".format(self.__package.getName())

    def stablePaths(self):
        return self.__pathsConfig.stablePaths

    def getTools(self):
        """Get dictionary of tools.

        The dict maps the tool name to a :class:`bob.input.Tool`.
        """
        if self._coreStep.isValid:
            toolKeys = self._coreStep.toolDep
            return { name : tool for name, tool in self.__package._getAllTools().items()
                                 if name in toolKeys }
        else:
            return {}

    def getArguments(self):
        """Get list of all inputs for this Step.

        The arguments are passed as absolute paths to the script starting from $1.
        """
        p = self.__package
        refCache = {}
        return [ a.refDeref(p.getStack(), p._getInputTools(), p._getInputSandboxRaw(),
                            self.__pathsConfig, refCache)
                    for a in self._coreStep.args ]

    def getAllDepSteps(self):
        """Get all dependent steps of this Step.

        This includes the direct input to the Step as well as indirect inputs
        such as the used tools or the sandbox.
        """
        sandbox = self.getSandbox()
        return self.getArguments() + [ d.step for n,d in sorted(self.getTools().items()) ] + (
            [sandbox.getStep()] if sandbox else [])

    def getEnv(self):
        """Return dict of environment variables."""
        return self._coreStep.env

    def doesProvideTools(self):
        """Return True if this step provides at least one tool."""
        return bool(self._coreStep.providedTools)

    def isShared(self):
        """Returns True if the result of the Step should be shared globally.

        The exact behaviour of a shared step/package depends on the build
        backend. In general a shared package means that the result is put into
        some shared location where it is likely that the same result is needed
        again.
        """
        return False

    def isRelocatable(self):
        """Returns True if the step is relocatable."""
        return False

    def jobServer(self):
        """Returns True if the jobserver should be used to schedule
        builds for this step."""
        return self._coreStep.jobServer()

    def _getProvidedDeps(self):
        p = self.__package
        refCache = {}
        return [ a.refDeref(p.getStack(), p._getInputTools(), p._getInputSandboxRaw(),
                            self.__pathsConfig, refCache)
                    for a in self._coreStep.providedDeps ]

    def _isFingerprinted(self):
        return self._coreStep.isFingerprinted()

    def _getFingerprintScript(self):
        """Generate final fingerprint script.

        The used fingerprint scripts of the tools and the recipe/classes are
        finally stitched together based on the mask that was calculated in
        Recipt.resolveClasses(). For each possible entry there are two bits in
        the mask: bit 0 is set if the script is taken unconditionally and bit 1
        is set if the script is taken if not empty.
        """
        if not self._coreStep.isFingerprinted():
            return ""

        package = self.__package
        recipe = package.getRecipe()
        packageStep = package.getPackageStep()
        mask = self._coreStep.fingerprintMask
        tools = packageStep.getTools()
        scriptsAndVars = chain(
            ((({}, []) if t is None else (t.fingerprintScript, t.fingerprintVars))
                for t in (tools.get(k) for k in sorted(packageStep._coreStep.toolDep))),
            zip(recipe.fingerprintScriptList, recipe.fingerprintVarsList))
        ret = []
        varSet = set()
        for s,v in scriptsAndVars:
            s = s.get(recipe.scriptLanguage.index)
            if (mask & 1) or ((mask & 2) and s):
                ret.append(s)
                varSet.update(v)
            mask >>= 2
        env = self.getEnv()
        env = { k : v for k,v in env.items() if k in varSet }
        return recipe.scriptLanguage.mangleFingerprints(ret, env)

    @property
    def toolDep(self):
        return self._coreStep.toolDep

    @property
    def toolDepWeak(self):
        return self._coreStep.toolDepWeak


class CoreCheckoutStep(CoreStep):
    __slots__ = ( "scmList", "__checkoutUpdateIf", "__checkoutUpdateDeterministic", "__checkoutAsserts" )

    def __init__(self, corePackage, checkout=None, checkoutSCMs=[],
                 fullEnv=Env(), digestEnv=Env(), env=Env(), args=[],
                 checkoutUpdateIf=[], checkoutUpdateDeterministic=True,
                 toolDep=set(), toolDepWeak=set()):
        if checkout:
            recipeSet = corePackage.recipe.getRecipeSet()
            overrides = recipeSet.scmOverrides()
            self.scmList = [ Scm(scm, fullEnv, overrides, recipeSet)
                for scm in checkoutSCMs
                if fullEnv.evaluate(scm.get("if"), "checkoutSCM") ]
            isValid = (checkout[1] is not None) or bool(self.scmList)

            # Validate all SCM paths. Only relative paths are allowed. If they
            # overlap, the following rules must apply:
            #  1. Deeper paths must be checked out later.
            #     -> required for initial checkout to work
            #  2. A Jenkins native SCM (e.g. git) must not overlap with a
            #     previous non-native SCM.
            #     -> required because SCM plugins are running before scripts on
            #        Jenkins jobs
            knownPaths = []
            for s in self.scmList:
                p = s.getDirectory()
                if isAbsPath(p):
                    raise ParseError("SCM paths must be relative! Offending path: " + p)
                for knownPath, knownHasJenkins in knownPaths:
                    if isPrefixPath(p, knownPath):
                        raise ParseError("SCM path '{}' obstructs '{}'."
                                            .format(p, knownPath),
                                         help="Nested SCMs must be specified in a upper-to-lower directory order.")
                    if isPrefixPath(knownPath, p) and s.hasJenkinsPlugin() and not knownHasJenkins:
                        raise ParseError("SCM path '{}' cannot be checked out after '{}' in Jenkins jobs"
                                            .format(p, knownPath),
                                         help="SCM plugins always run first in a Jenkins job but the SCM in '{}' does not have a native Jenkins plugin."
                                                .format(knownPath))
                knownPaths.append((p, s.hasJenkinsPlugin()))
        else:
            isValid = False
            self.scmList = []

        self.__checkoutAsserts = [ CheckoutAssert (a, fullEnv) for a in corePackage.recipe.checkoutAsserts ]
        self.__checkoutUpdateIf = checkoutUpdateIf
        self.__checkoutUpdateDeterministic = checkoutUpdateDeterministic
        deterministic = corePackage.recipe.checkoutDeterministic
        super().__init__(corePackage, isValid, deterministic, digestEnv, env, args, toolDep, toolDepWeak)

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        ret = CheckoutStep(self, package, pathsConfig)
        package._setCheckoutStep(ret)
        return ret

    def getLabel(self):
        return "src"

    def isDeterministic(self):
        return super().isDeterministic() and all(s.isDeterministic() for s in self.scmList)

    def isUpdateDeterministic(self):
        return self.__checkoutUpdateDeterministic

    def hasLiveBuildId(self):
        return super().isDeterministic() and all(s.hasLiveBuildId() for s in self.scmList)

    def isCheckoutStep(self):
        return True

    def getPreRunCmds(self):
        return [s.getProperties(False) for s in self.scmList]

    def getJenkinsPreRunCmds(self):
        return [ s.getProperties(True) for s in self.scmList if not s.hasJenkinsPlugin() ]

    def getSetupScript(self):
        return self.corePackage.recipe.checkoutSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.checkoutMainScript

    def getPostRunCmds(self):
        return [s.getProperties() for s in self.__checkoutAsserts]

    def getDigestScript(self):
        if self.isValid:
            recipe = self.corePackage.recipe
            return "\n".join([s.asDigestScript() for s in self.scmList]
                    + [recipe.checkoutDigestScript]
                    + [s.asDigestScript() for s in self.__checkoutAsserts])
        else:
            return None

    def getUpdateScript(self):
        glue = getLanguage(self.corePackage.recipe.scriptLanguage.index).glue
        return joinScripts(self.__checkoutUpdateIf, glue) or ""

    @property
    def fingerprintMask(self):
        return 0

class CheckoutStep(Step):
    def getJenkinsXml(self, config):
        return [ s.asJenkins(self.getWorkspacePath(), config)
                 for s in self._coreStep.scmList if s.hasJenkinsPlugin() ]

    def getScmList(self):
        return self._coreStep.scmList

    def getScmDirectories(self):
        dirs = {}
        for s in self._coreStep.scmList:
            dirs[s.getDirectory()] = (hashString(s.asDigestScript()), s.getProperties(False))
        return dirs

    def hasLiveBuildId(self):
        """Check if live build-ids are supported.

        This must be supported by all SCMs. Additionally the checkout script
        must be deterministic.
        """
        return self._coreStep.hasLiveBuildId()

    def hasNetAccess(self):
        return True


class CoreBuildStep(CoreStep):
    __slots__ = ["fingerprintMask"]

    def __init__(self, corePackage, script=(None, None, None), digestEnv=Env(),
                 env=Env(), args=[], fingerprintMask=0, toolDep=set(), toolDepWeak=set()):
        isValid = script[1] is not None
        self.fingerprintMask = fingerprintMask
        super().__init__(corePackage, isValid, True, digestEnv, env, args, toolDep, toolDepWeak)

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        ret = BuildStep(self, package, pathsConfig)
        package._setBuildStep(ret)
        return ret

    def getLabel(self):
        return "build"

    def isBuildStep(self):
        return True

    def getSetupScript(self):
        return self.corePackage.recipe.buildSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.buildMainScript

    def getDigestScript(self):
        return self.corePackage.recipe.buildDigestScript

class BuildStep(Step):

    def hasNetAccess(self):
        return self.getPackage().getRecipe().buildNetAccess or any(
            t.getNetAccess() for t in self.getTools().values())


class CorePackageStep(CoreStep):
    __slots__ = ["fingerprintMask"]

    def __init__(self, corePackage, script=(None, None, None), digestEnv=Env(), env=Env(), args=[],
                 fingerprintMask=0, toolDep=set(), toolDepWeak=set()):
        isValid = script[1] is not None
        self.fingerprintMask = fingerprintMask
        super().__init__(corePackage, isValid, True, digestEnv, env, args, toolDep, toolDepWeak)

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        package = self.corePackage.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        ret = PackageStep(self, package, pathsConfig)
        package._setPackageStep(ret)
        return ret

    def getLabel(self):
        return "dist"

    def isPackageStep(self):
        return True

    def getSetupScript(self):
        return self.corePackage.recipe.packageSetupScript

    def getMainScript(self):
        return self.corePackage.recipe.packageMainScript

    def getDigestScript(self):
        return self.corePackage.recipe.packageDigestScript

class PackageStep(Step):

    def isShared(self):
        """Determine if the PackageStep be shared.

        Requires the recipe to be marked as shared and the result must be
        position independent.
        """
        return self.getPackage().getRecipe().isShared() and self.isRelocatable()

    def isRelocatable(self):
        """Returns True if the package step is relocatable."""
        return self.getPackage().isRelocatable()

    def hasNetAccess(self):
        return self.getPackage().getRecipe().packageNetAccess or any(
            t.getNetAccess() for t in self.getTools().values())


class CorePackageInternal(CoreItem):
    __slots__ = []
    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig, cache=None):
        return (inputTools, inputSandbox)

corePackageInternal = CorePackageInternal()

class CorePackage:
    __slots__ = ("recipe", "internalRef", "directDepSteps", "indirectDepSteps",
        "states", "tools", "sandbox", "checkoutStep", "buildStep", "packageStep",
        "pkgId", "metaEnv", "packageName")

    def __init__(self, recipe, tools, diffTools, sandbox, diffSandbox,
                 directDepSteps, indirectDepSteps, states, pkgId, metaEnv,
                 packageName):
        self.recipe = recipe
        self.tools = tools
        self.sandbox = sandbox
        self.internalRef = CoreRef(corePackageInternal, [], diffTools, diffSandbox)
        self.directDepSteps = directDepSteps
        self.indirectDepSteps = indirectDepSteps
        self.states = states
        self.pkgId = pkgId
        self.metaEnv = metaEnv
        self.packageName = packageName

    def refDeref(self, stack, inputTools, inputSandbox, pathsConfig):
        tools, sandbox = self.internalRef.refDeref(stack, inputTools, inputSandbox, pathsConfig)
        return Package(self, stack, pathsConfig, inputTools, tools, inputSandbox, sandbox)

    def createCoreCheckoutStep(self, checkout, checkoutSCMs, fullEnv, digestEnv,
                               env, args, checkoutUpdateIf, checkoutUpdateDeterministic,
                               toolDep, toolDepWeak):
        ret = self.checkoutStep = CoreCheckoutStep(self, checkout, checkoutSCMs,
            fullEnv, digestEnv, env, args, checkoutUpdateIf, checkoutUpdateDeterministic,
            toolDep, toolDepWeak)
        return ret

    def createInvalidCoreCheckoutStep(self):
        ret = self.checkoutStep = CoreCheckoutStep(self)
        return ret

    def createCoreBuildStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak):
        ret = self.buildStep = CoreBuildStep(self, script, digestEnv, env, args,
                                             fingerprintMask, toolDep, toolDepWeak)
        return ret

    def createInvalidCoreBuildStep(self, args):
        ret = self.buildStep = CoreBuildStep(self, args=args)
        return ret

    def createCorePackageStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak):
        ret = self.packageStep = CorePackageStep(self, script, digestEnv, env, args, fingerprintMask, toolDep, toolDepWeak)
        return ret

    def getCorePackageStep(self):
        return self.packageStep

    def getName(self):
        """Name of the package"""
        if self.packageName is None:
            return self.recipe.getPackageName()
        else:
            return self.packageName

    def getMetaEnv(self):
        return self.metaEnv

    @property
    def jobServer(self):
        return self.recipe.jobServer()

    def isAlias(self):
        return self.packageName is not None

class Package(object):
    """Representation of a package that was created from a recipe.

    Usually multiple packages will be created from a single recipe. This is
    either due to multiple upstream recipes or different variants of the same
    package. This does not preclude the possibility that multiple Package
    objects describe exactly the same package (read: same Variant-Id). It is
    the responsibility of the build backend to detect this and build only one
    package.
    """

    def __init__(self, corePackage, stack, pathsConfig, inputTools, tools, inputSandbox, sandbox):
        self.__corePackage = corePackage
        self.__stack = stack
        self.__pathsConfig = pathsConfig
        self.__inputTools = inputTools
        self.__tools = tools
        self.__inputSandbox = inputSandbox
        self.__sandbox = sandbox

    def __eq__(self, other):
        return isinstance(other, Package) and (self.__stack == other.__stack)

    def _getId(self):
        """The package-Id is uniquely representing every package variant.

        On the package level there might be more dependencies than on the step
        level. Meta variables are usually unused and also do not contribute to
        the variant-id. The package-id still guarantees to not collide in these
        cases. OTOH there can be identical packages with different ids, though
        it should be an unusual case.
        """
        return self.__corePackage.pkgId

    def _getInputTools(self):
        return self.__inputTools

    def _getAllTools(self):
        return self.__tools

    def _getInputSandboxRaw(self):
        return self.__inputSandbox

    def _getSandboxRaw(self):
        return self.__sandbox

    def getName(self):
        """Name of the package"""
        return self.__corePackage.getName()

    def getMetaEnv(self):
        """meta variables of package"""
        return self.__corePackage.getMetaEnv()

    def getStack(self):
        """Returns the recipe processing stack leading to this package.

        The method returns a list of package names. The first entry is a root
        recipe and the last entry is this package."""
        return self.__stack

    def getRecipe(self):
        """Return Recipe object that was the template for this package."""
        return self.__corePackage.recipe

    def getDirectDepSteps(self):
        """Return list of the package steps of the direct dependencies.

        Direct dependencies are the ones that are named explicitly in the
        ``depends`` section of the recipe. The order of the items is
        preserved from the recipe.
        """
        refCache = {}
        return [ d.refDeref(self.__stack, self.__inputTools, self.__inputSandbox,
                            self.__pathsConfig, refCache)
                    for d in self.__corePackage.directDepSteps ]

    def getIndirectDepSteps(self):
        """Return list of indirect dependencies of the package.

        Indirect dependencies are dependencies that were provided by downstream
        recipes. They are not directly named in the recipe.
        """
        refCache = {}
        return [ d.refDeref(self.__stack, self.__inputTools, self.__inputSandbox,
                            self.__pathsConfig, refCache)
                    for d in self.__corePackage.indirectDepSteps ]

    def getAllDepSteps(self):
        """Return list of all dependencies of the package.

        This list includes all direct and indirect dependencies. Additionally
        the used sandbox and tools are included too.
        """
        allDeps = set(self.getDirectDepSteps())
        allDeps |= set(self.getIndirectDepSteps())
        if self.__sandbox and self.__sandbox.isEnabled():
            allDeps.add(self.__sandbox.getStep())
        for i in self.getPackageStep().getTools().values(): allDeps.add(i.getStep())
        return sorted(allDeps)

    def _setCheckoutStep(self, checkoutStep):
        self.__checkoutStep = checkoutStep

    def getCheckoutStep(self):
        """Return the checkout step of this package."""
        try:
            ret = self.__checkoutStep
        except AttributeError:
            ret = self.__checkoutStep = CheckoutStep(self.__corePackage.checkoutStep,
                self, self.__pathsConfig)
        return ret

    def _setBuildStep(self, buildStep):
        self.__buildStep = buildStep

    def getBuildStep(self):
        """Return the build step of this package."""
        try:
            ret = self.__buildStep
        except AttributeError:
            ret = self.__buildStep = BuildStep(self.__corePackage.buildStep,
                self, self.__pathsConfig)
        return ret

    def _setPackageStep(self, packageStep):
        self.__packageStep = packageStep

    def getPackageStep(self):
        """Return the package step of this package."""
        try:
            ret = self.__packageStep
        except AttributeError:
            ret = self.__packageStep = PackageStep(self.__corePackage.packageStep,
                self, self.__pathsConfig)
        return ret

    def getPluginStates(self):
        """Return state trackers of this package.

        :return: All plugin defined state trackers of the package
        :rtype: Mapping[str, :class:`bob.input.PluginState`]
        """
        return self.__corePackage.states

    def isRelocatable(self):
        """Returns True if the packages is relocatable."""
        return self.__corePackage.recipe.isRelocatable()

    def isAlias(self):
        """Returns True if the package name is an alias.

        Usually, the package name is based on the recipe name with some
        optional suffix due to (possibly nested) multiPackage(s). In case of
        global or per-dependency alias names, any other name may be used,
        though. In this case, ``isAlias()`` returns true and ``getName()`` does
        not equal ``getRecipe().getPackageName()`` as it normally does.
        """
        return self.__corePackages.isAlias()


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
            return (None, None)

class ScmValidator:
    def __init__(self, scmSpecs):
        self.__scmSpecs = scmSpecs

    def __validateScm(self, scm):
        if 'scm' not in scm:
            raise schema.SchemaMissingKeyError("Missing 'scm' key in {}".format(scm), None)
        if scm['scm'] not in self.__scmSpecs.keys():
            raise schema.SchemaWrongKeyError('Invalid SCM: {}'.format(scm['scm']), None)
        return self.__scmSpecs[scm['scm']].validate(scm)

    def validate(self, data):
        if isinstance(data, dict):
            data = [self.__validateScm(data)]
        elif isinstance(data, list):
            data = [ self.__validateScm(i) for i in data ]
        else:
            raise schema.SchemaUnexpectedTypeError(
                'checkoutSCM must be a SCM spec or a list threreof',
                None)
        return data

class LayerSpec:
    def __init__(self, name, scm=None):
        self.__name = name;
        self.__scm = scm

    def getName(self):
        return self.__name

    def getScm(self):
        return self.__scm

class LayerValidator:
    @staticmethod
    def __validateName(name):
        if name == "":
            raise schema.SchemaError("Layer name must not be empty")
        if any((c in name) for c in '\\/'):
            raise schema.SchemaError("Invalid character in layer name")

    def validate(self, data):
        if isinstance(data, str):
            self.__validateName(data)
            return LayerSpec(data)
        elif not isinstance(data, dict):
            raise schema.SchemaUnexpectedTypeError("Layer entry must be a string or a dict", None)

        if 'name' not in data:
            raise schema.SchemaMissingKeyError("Missing 'name' key in {}".format(data), None)
        elif not isinstance(data['name'], str):
            raise schema.SchemaUnexpectedTypeError("Layer name must be a string", None)

        _data = data.copy();
        name = _data.get('name')
        self.__validateName(name)
        del _data['name']

        return LayerSpec(name, RecipeSet.LAYERS_SCM_SCHEMA.validate(_data)[0])

class VarDefineValidator:
    VAR_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
    VAR_DEF = schema.Schema({
            'value' : str,
            schema.Optional("if"): schema.Or(str, IfExpression),
        })

    def __init__(self, keyword, conditional=True):
        self.__keyword = keyword
        self.__conditional = conditional

    def validate(self, data):
        if not isinstance(data, dict):
            raise schema.SchemaUnexpectedTypeError(
                "{}: must be a dictionary".format(self.__keyword), None)

        data = data.copy()
        for key,value in sorted(data.items()):
            if not isinstance(key, str):
                raise schema.SchemaUnexpectedTypeError(
                    "{}: bad variable '{}'. Environment variable names must be strings!"
                        .format(self.__keyword, key),
                    None)
            if key.startswith("BOB_"):
                raise schema.SchemaWrongKeyError(
                    "{}: bad variable '{}'. Environment variables starting with 'BOB_' are reserved!"
                        .format(self.__keyword, key),
                    None)
            if self.VAR_NAME.match(key) is None:
                raise schema.SchemaWrongKeyError(
                    "{}: bad variable name '{}'.".format(self.__keyword, key),
                    None)
            if isinstance(value, dict) and self.__conditional:
                self.VAR_DEF.validate(value, error=f"{self.__keyword}: {key}: invalid definition!")
                data[key] = (value['value'], value.get('if'))
            elif isinstance(value, str):
                if self.__conditional:
                    data[key] = (value, None)
            else:
                raise schema.SchemaUnexpectedTypeError(
                    "{}: {}: bad variable definition type."
                        .format(self.__keyword, key),
                    None)
        return data


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
        name = step.corePackage.getName()
        ref2 = self.cache.get(name)
        if ref2 is None:
            self.cache[name] = ref
            self.ret.append(ref)
        elif ref2.refGetDestination().variantId != step.variantId:
            self.errorHandler(name, self.stack + ref.refGetStack(), self.stack + ref2.refGetStack())

    def extend(self, gen):
        for i in gen: self.append(i)

    def result(self):
        return self.ret

class DepTracker:

    __slots__ = ('item', 'isNew', 'usedResult')

    def __init__(self, item):
        self.item = item
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
                     'useSandbox', 'condition', 'toolOverride', 'checkoutDep',
                     'alias')

        def __init__(self, recipe, env, fwd, use, cond, tools, checkoutDep, inherit, alias):
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
            self.condition = cond
            self.toolOverride = tools
            self.checkoutDep = checkoutDep
            self.alias = alias

        @staticmethod
        def __parseEntry(dep, env, fwd, use, cond, tools, checkoutDep, inherit):
            if isinstance(dep, str):
                return [ Recipe.Dependency(dep, env, fwd, use, cond, tools, checkoutDep,
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
                        raise ParseError("A dependency must not use 'name' and 'depends' at the same time!")
                    return [ Recipe.Dependency(name, env, fwd, use, cond, tools,
                                               checkoutDep, inherit, dep.get("alias")) ]
                dependencies = dep.get("depends")
                if dependencies is None:
                    raise ParseError("Either 'name' or 'depends' required for dependencies!")
                return Recipe.Dependency.parseEntries(dependencies, env, fwd,
                                                      use, cond, tools,
                                                      checkoutDep, inherit)

        @staticmethod
        def parseEntries(deps, env={}, fwd=False, use=["result", "deps"],
                         cond=None, tools={}, checkoutDep=False, inherit=True):
            """Returns an iterator yielding all dependencies as flat list"""
            # return flattened list of dependencies
            return chain.from_iterable(
                Recipe.Dependency.__parseEntry(dep, env, fwd, use, cond, tools,
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
        self.__sources = [ sourceFile ] if anonBaseClass is None else []
        self.__classesResolved = False
        self.__inherit = recipe.get("inherit", [])
        self.__anonBaseClass = anonBaseClass
        self.__defaultScriptLanguage = scriptLanguage
        self.__deps = list(Recipe.Dependency.parseEntries(recipe.get("depends", [])))
        self.__packageName = packageName
        self.__baseName = baseName
        self.__root = recipe.get("root")
        self.__provideTools = { name : AbstractTool(spec)
            for (name, spec) in recipe.get("provideTools", {}).items() }
        self.__provideVars = recipe.get("provideVars", {})
        self.__provideDeps = set(recipe.get("provideDeps", []))
        self.__provideSandbox = recipe.get("provideSandbox")
        self.__varSelf = recipe.get("environment", {})
        self.__varPrivate = recipe.get("privateEnvironment", {})
        self.__metaEnv = recipe.get("metaEnvironment", {})
        self.__checkoutDeterministic = recipe.get("checkoutDeterministic")
        self.__checkoutVars = set(recipe.get("checkoutVars", []))
        self.__checkoutVarsWeak = set(recipe.get("checkoutVarsWeak", []))
        self.__buildVars = set(recipe.get("buildVars", []))
        self.__buildVars |= self.__checkoutVars
        self.__buildVarsWeak = set(recipe.get("buildVarsWeak", []))
        self.__buildVarsWeak |= self.__checkoutVarsWeak
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

        self.__scriptLanguage = recipe.get("scriptLanguage")
        self.__checkout = fetchScripts(recipe, "checkout", incHelperBash, incHelperPwsh)
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
        self.__build = fetchScripts(recipe, "build", incHelperBash, incHelperPwsh)
        self.__package = fetchScripts(recipe, "package", incHelperBash, incHelperPwsh)
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
        ret = []
        subInherit = [ self.__recipeSet.getClass(c) for c in cls.__inherit ]
        if cls.__anonBaseClass: subInherit.insert(0, cls.__anonBaseClass)
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

        # Consider checkout deterministic by default if no checkoutScript is
        # involved. A potential checkoutSetup is ignored.
        def coDet(r):
            ret = r.__checkoutDeterministic
            if ret is not None:
                return ret
            return r.__checkout[self.__scriptLanguage][1][0] is None

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
            tmp = cls.__provideTools.copy()
            tmp.update(self.__provideTools)
            self.__provideTools = tmp
            tmp = cls.__provideVars.copy()
            tmp.update(self.__provideVars)
            self.__provideVars = tmp
            self.__provideDeps |= cls.__provideDeps
            if self.__provideSandbox is None: self.__provideSandbox = cls.__provideSandbox
            if cls.__varSelf: self.__varSelf.insert(0, cls.__varSelf)
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

        # the package step must always be valid
        if self.__package[1] is None:
            self.__package = (None, "", 'da39a3ee5e6b4b0d3255bfef95601890afd80709')

        # final shared value
        self.__shared = self.__shared == True

        if self.__relocatable is None:
            self.__relocatable = True

        if self.__jobServer is None:
            self.__jobServer = False

        # Optimize provideDeps
        self.__provideDeps = [ getProvideDepsResolver(d) for d in self.__provideDeps ]

        # Evaluate root property
        if isinstance(self.__root, str) or isinstance(self.__root, IfExpression):
            self.__root = rootEnv.evaluate(self.__root, "root")

    def getRecipeSet(self):
        """Get the :class:`RecipeSet` to which the recipe belongs"""
        return self.__recipeSet

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

    def isShared(self):
        return self.__shared

    def jobServer(self):
        """Returns True if the jobserver should be used to schedule builds for
        this recipe.

        :meta private:
        """
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
                inputTools=Env(), inputStack=PackageStack(), packageName=None):
        # Cycle detection is based on the recipe package name. Any alias names
        # are ignored.
        if self.__packageName in inputStack:
            raise ParseError("Recipes are cyclic (1st package in cylce)")
        stack = inputStack.push(self.__packageName,
                                self.__packageName if packageName is None else packageName)
        # already calculated?
        for m in self.__corePackagesByMatch:
            if m.matches(inputEnv.detach(), inputTools.detach(), inputStates, inputSandbox, packageName):
                if stack.intersects(m.subTreePackages):
                    raise ParseError("Recipes are cyclic")
                m.touch(inputEnv, inputTools)
                if DEBUG['pkgck']:
                    reusedCorePackage = m.corePackage
                    break
                return m.corePackage, m.subTreePackages
        else:
            reusedCorePackage = None

        # Track tool and sandbox changes
        diffSandbox = ...
        diffTools = { }

        # make copies because we will modify them
        sandbox = inputSandbox
        inputTools = inputTools.copy()
        inputTools.touchReset()
        tools = inputTools.derive()
        inputEnv = inputEnv.derive()
        inputEnv.touchReset()
        inputEnv.setFunArgs({ "recipe" : self, "sandbox" : bool(sandbox) and sandboxEnabled,
            "__tools" : tools })
        env = inputEnv.derive()
        for i in self.__varSelf:
            env.update(env.substituteCondDict(i, "environment"))
        states = { n : s.copy() for (n,s) in inputStates.items() }

        # update plugin states
        for s in states.values(): s.onEnter(env, self.__properties)

        # traverse dependencies
        subTreePackages = set()
        directPackages = []
        indirectPackages = []
        provideDeps = UniquePackageList(stack.getNameStack(), self.__raiseIncompatibleProvided)
        maybeProvideDeps = []
        checkoutDeps = []
        results = []
        depEnv = env.derive()
        depTools = tools.derive()
        depSandbox = sandbox
        depStates = { n : s.copy() for (n,s) in states.items() }
        depDiffSandbox = diffSandbox
        depDiffTools = diffTools.copy()
        thisDeps = {}
        resolvedDeps = []

        for dep in self.__deps:
            env.setFunArgs({ "recipe" : self, "sandbox" : bool(sandbox) and sandboxEnabled,
                "__tools" : tools })

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
            if not dep.inherit:
                thisDepEnv = self.getRecipeSet().getRootEnv()
                thisDepTools = Env()
                # Compute the diff to remove all tools that were passed to the
                # package.
                thisDepDiffTools = { n : None for n in inputTools.inspect().keys() }
                thisDepSandbox = None
                # Clear sandbox, if any
                thisDepDiffSandbox = None

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
                p, s = r.prepare(thisDepEnv, sandboxEnabled, depStates,
                                 thisDepSandbox, thisDepTools, stack, aliasName)
                subTreePackages.add(recipeName)
                subTreePackages.update(s)
                depCoreStep = p.getCorePackageStep()
                depRef = CoreRef(depCoreStep, [p.getName()], thisDepDiffTools, thisDepDiffSandbox)
            except ParseError as e:
                e.pushFrame(r.getPackageName())
                raise e

            # A dependency should be named only once. Hence we can
            # optimistically create the DepTracker object. If the dependency is
            # named more than one we make sure that it is the same variant.
            depTrack = thisDeps.setdefault(p.getName(), DepTracker(depRef))
            if depTrack.prime():
                directPackages.append(depRef)
            elif depCoreStep.variantId != depTrack.item.refGetDestination().variantId:
                self.__raiseIncompatibleLocal(depCoreStep)
            else:
                raise ParseError("Duplicate dependency '{}'. Each dependency must only be named once!"
                                    .format(p.getName()))

            # Remember dependency diffs before changing them
            origDepDiffTools = thisDepDiffTools
            origDepDiffSandbox = thisDepDiffSandbox

            # pick up various results of package
            for (n, s) in states.items():
                if n in dep.use:
                    s.onUse(depCoreStep.corePackage.states[n])
                    if dep.provideGlobal: depStates[n].onUse(depCoreStep.corePackage.states[n])
            if dep.useDeps:
                indirectPackages.extend(
                    CoreRef(d, [p.getName()], origDepDiffTools, origDepDiffSandbox)
                    for d in depCoreStep.providedDeps)
            if dep.useBuildResult and depTrack.useResultOnce():
                results.append(depRef)
                if dep.checkoutDep: checkoutDeps.append(depRef)
            if dep.useTools:
                tools.update(depCoreStep.providedTools)
                diffTools.update( (n, CoreRef(d, [p.getName()], origDepDiffTools, origDepDiffSandbox))
                    for n, d in depCoreStep.providedTools.items() )
                if dep.provideGlobal:
                    depTools.update(depCoreStep.providedTools)
                    depDiffTools = depDiffTools.copy()
                    depDiffTools.update( (n, CoreRef(d, [p.getName()], origDepDiffTools, origDepDiffSandbox))
                        for n, d in depCoreStep.providedTools.items() )
            if dep.useEnv:
                env.update(depCoreStep.providedEnv)
                if dep.provideGlobal: depEnv.update(depCoreStep.providedEnv)
            if dep.useSandbox and (depCoreStep.providedSandbox is not None):
                sandbox = depCoreStep.providedSandbox
                diffSandbox = CoreRef(depCoreStep.providedSandbox, [p.getName()], origDepDiffTools,
                    origDepDiffSandbox)
                if dep.provideGlobal:
                    depSandbox = sandbox
                    depDiffSandbox = diffSandbox
                if sandboxEnabled:
                    env.update(sandbox.environment)
                    if dep.provideGlobal: depEnv.update(sandbox.environment)

            maybeProvideDeps.append((p.getName(), depRef, origDepDiffTools, origDepDiffSandbox))

        # check provided dependencies
        providedDeps = set()
        for pattern in self.__provideDeps:
            l = pattern.resolve(env, resolvedDeps)
            if not l:
                raise ParseError("Unknown dependency '{}' in provideDeps".format(pattern.pattern))
            providedDeps |= l

        for (name, depRef, origDepDiffTools, origDepDiffSandbox) in maybeProvideDeps:
            if name in providedDeps:
                provideDeps.append(depRef)
                provideDeps.extend([CoreRef(d, [name], origDepDiffTools, origDepDiffSandbox)
                    for d in depRef.refGetDestination().providedDeps])

        # Filter indirect packages and add to result list if necessary. Most
        # likely there are many duplicates that are dropped.
        tmp = indirectPackages
        indirectPackages = []
        for depRef in tmp:
            depCoreStep = depRef.refGetDestination()
            name = depCoreStep.corePackage.getName()
            depTrack = thisDeps.get(name)
            if depTrack is None:
                thisDeps[name] = depTrack = DepTracker(depRef)

            if depTrack.prime():
                indirectPackages.append(depRef)
            elif depCoreStep.variantId != depTrack.item.refGetDestination().variantId:
                self.__raiseIncompatibleProvided(name,
                    stack.getNameStack() + depRef.refGetStack(),
                    stack.getNameStack() + depTrack.item.refGetStack())

            if depTrack.useResultOnce():
                results.append(depRef)

        # Calculate used tools. They are conditional.
        env.setFunArgs({ "recipe" : self, "sandbox" : bool(sandbox) and sandboxEnabled,
            "__tools" : tools })
        toolDepCheckout = set(name for (name, cond) in self.__toolDepCheckout
                              if env.evaluate(cond, "checkoutTools"))
        toolDepCheckoutWeak = set(name for (name, cond) in self.__toolDepCheckoutWeak
                                  if env.evaluate(cond, "checkoutToolsWeak"))
        toolDepBuild = set(name for (name, cond) in self.__toolDepBuild
                           if env.evaluate(cond, "buildTools")) | toolDepCheckout
        toolDepBuildWeak = set(name for (name, cond) in self.__toolDepBuildWeak
                               if env.evaluate(cond, "buildToolsWeak")) | toolDepCheckoutWeak
        toolDepPackage = set(name for (name, cond) in self.__toolDepPackage
                           if env.evaluate(cond, "packageTools")) | toolDepBuild
        toolDepPackageWeak = set(name for (name, cond) in self.__toolDepPackageWeak
                               if env.evaluate(cond, "packageToolsWeak")) | toolDepBuildWeak

        # Only keep weak tools that are not strong at the same time.
        toolDepCheckoutWeak -= toolDepCheckout
        toolDepCheckout |= toolDepCheckoutWeak
        toolDepBuildWeak -= toolDepBuild
        toolDepBuild |= toolDepBuildWeak
        toolDepPackageWeak -= toolDepPackage
        toolDepPackage |= toolDepPackageWeak

        # apply tool environments
        toolsEnv = set()
        toolsView = tools.inspect()
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
                directPackages, indirectPackages, states, uidGen(), metaEnv,
                packageName)

        # optional checkout step
        if self.__checkout != (None, None, None) or self.__checkoutSCMs or self.__checkoutAsserts:
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
                toolDepCheckout, toolDepCheckoutWeak)
        else:
            srcCoreStep = p.createInvalidCoreCheckoutStep()

        # optional build step
        if self.__build != (None, None, None):
            buildDigestEnv = env.prune(self.__buildVars)
            buildEnv = ( env.prune(self.__buildVars | self.__buildVarsWeak)
                if self.__buildVarsWeak else buildDigestEnv )
            buildCoreStep = p.createCoreBuildStep(self.__build, buildDigestEnv, buildEnv,
                [CoreRef(srcCoreStep)] + results, doFingerprintBuild, toolDepBuild, toolDepBuildWeak)
        else:
            buildCoreStep = p.createInvalidCoreBuildStep([CoreRef(srcCoreStep)] + results)

        # mandatory package step
        packageDigestEnv = env.prune(self.__packageVars)
        packageEnv = ( env.prune(self.__packageVars | self.__packageVarsWeak)
            if self.__packageVarsWeak else packageDigestEnv )
        packageCoreStep = p.createCorePackageStep(self.__package, packageDigestEnv, packageEnv,
            [CoreRef(buildCoreStep)], doFingerprint, toolDepPackage, toolDepPackageWeak)

        # provide environment
        packageCoreStep.providedEnv = env.substituteCondDict(self.__provideVars, "provideVars")

        # provide tools
        packageCoreStep.providedTools = { name : tool.prepare(packageCoreStep, env)
            for (name, tool) in self.__provideTools.items() }

        # provide deps (direct and indirect deps)
        packageCoreStep.providedDeps = provideDeps.result()

        # provide Sandbox
        if self.__provideSandbox:
            packageCoreStep.providedSandbox = CoreSandbox(packageCoreStep,
                env, sandboxEnabled, self.__provideSandbox)

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
                inputSandbox, subTreePackages, packageName))
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

    def __raiseIncompatibleProvided(self, name, stack1, stack2):
        raise ParseError("Incompatible variants of package: {} vs. {}"
            .format("/".join(stack1), "/".join(stack2)),
            help=
"""This error is caused by '{PKG}' that is passed upwards via 'provideDeps' from multiple dependencies of '{CUR}'.
These dependencies constitute different variants of '{PKG}' and can therefore not be used in '{CUR}'."""
    .format(PKG=name, CUR=self.__packageName))

    def __raiseIncompatibleLocal(self, r):
        raise ParseError("Multiple incompatible dependencies to package: {}"
            .format(r.corePackage.getName()),
            help=
"""This error is caused by naming '{PKG}' multiple times in the recipe with incompatible variants.
Every dependency must only be given once."""
    .format(PKG=r.corePackage.getName()))

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
            return [ AliasPackage(recipeSet, alias, baseName) ]
        else:
            return [
                AliasPackage(recipeSet, subAlias, baseName + ("-"+subName if subName else ""))
                for (subName, subAlias) in alias["multiPackage"].items()
            ]

    def __init__(self, recipeSet, target, packageName):
        self.__recipeSet = recipeSet
        self.__target = target
        self.__packageName = packageName

    def prepare(self, env, sandboxEnabled, states, sandbox, tools, stack, packageName=None):
        target = env.substitute(self.__target, "alias package")
        if packageName is None:
            packageName = self.__packageName
        return self.__recipeSet.getRecipe(target).prepare(env, sandboxEnabled, states, sandbox,
                                                          tools, stack,
                                                          packageName=packageName)

    def getPackageName(self):
        return self.__packageName

    def resolveClasses(self, env):
        pass

    def isRoot(self):
        return False


class PackageMatcher:
    __slots__ = ( 'corePackage', 'env', 'tools', 'states', 'sandbox',
                  'subTreePackages', 'packageName')

    def __init__(self, corePackage, env, tools, states, sandbox, subTreePackages, packageName):
        self.corePackage = corePackage
        envData = env.inspect()
        self.env = { name : envData.get(name) for name in env.touchedKeys() }
        toolsData = tools.inspect()
        self.tools = { name : (tool.resultId if tool is not None else None)
            for (name, tool) in ( (n, toolsData.get(n)) for n in tools.touchedKeys() ) }
        self.states = { n : s.copy() for (n,s) in states.items() }
        self.sandbox = sandbox.resultId if sandbox is not None else None
        self.subTreePackages = subTreePackages
        self.packageName = packageName

    def matches(self, inputEnv, inputTools, inputStates, inputSandbox, packageName):
        for (name, env) in self.env.items():
            if env != inputEnv.get(name): return False
        for (name, tool) in self.tools.items():
            match = inputTools.get(name)
            match = match.resultId if match is not None else None
            if tool != match: return False
        match = inputSandbox.resultId if inputSandbox is not None else None
        if self.sandbox != match: return False
        if self.states != inputStates: return False
        if self.packageName != packageName: return False
        return True

    def touch(self, inputEnv, inputTools):
        inputEnv.touch(self.env.keys())
        inputTools.touch(self.tools.keys())


class HttpUrlValidator:
    def validate(self, data):
        if not isinstance(data, str):
            raise schema.SchemaError(None, "Url field must be a string!")

        import urllib.parse
        import urllib.error
        try:
            u = urllib.parse.urlparse(data)
            if u.username is not None and u.password is None:
                raise schema.SchemaError(None, "Url with username but without password not supported!")
            if u.scheme not in ('http', 'https'):
                raise schema.SchemaError(None, "Unsupported URL schema: " + u.scheme)
        except urllib.error.URLError as e:
            raise schema.SchemaError(None, "Unparsable URL: " + str(e))

        return data

class ArchiveValidator:
    def __init__(self):
        self.__validTypes = schema.Schema({'backend': schema.Or('none', 'file', 'http', 'shell', 'azure')},
            ignore_extra_keys=True)
        baseArchive = {
            'backend' : str,
            schema.Optional('name') : str,
            schema.Optional('flags') : schema.Schema(["download", "upload", "managed",
                "nofail", "nolocal", "nojenkins", "cache"])
        }
        fileArchive = baseArchive.copy()
        fileArchive["path"] = str
        fileArchive[schema.Optional("fileMode")] = int
        fileArchive[schema.Optional("directoryMode")] = int
        httpArchive = baseArchive.copy()
        httpArchive["url"] = HttpUrlValidator()
        httpArchive[schema.Optional("sslVerify")] = bool
        httpArchive[schema.Optional("retries")] = PositiveValidator()
        shellArchive = baseArchive.copy()
        shellArchive.update({
            schema.Optional('download') : str,
            schema.Optional('upload') : str,
        })
        azureArchive = baseArchive.copy()
        azureArchive.update({
            'account' : str,
            'container' : str,
            schema.Optional('key') : str,
            schema.Optional('sasToken"') : str,
        })
        self.__backends = {
            'none' : schema.Schema(baseArchive),
            'file' : schema.Schema(fileArchive),
            'http' : schema.Schema(httpArchive),
            'shell' : schema.Schema(shellArchive),
            'azure' : schema.Schema(azureArchive),
        }

    def validate(self, data):
        if isinstance(data, dict):
            self.__validTypes.validate(data)
            return [ self.__backends[data['backend']].validate(data) ]
        elif isinstance(data, list):
            return [ self.__validTypes.validate(i) and self.__backends[i['backend']].validate(i)
                     for i in data ]
        else:
            raise schema.SchemaError(None, "Invalid archive specification!")

class MountValidator:
    def __init__(self):
        self.__options = schema.Schema(
            ["nolocal", "nojenkins", "nofail", "rw"],
            error="Invalid mount option specified!")

    def validate(self, data):
        if isinstance(data, str):
            return (data, data, [])
        elif isinstance(data, list) and (len(data) in [2, 3]):
            if not isinstance(data[0], str):
                raise schema.SchemaError(None, "Expected string as first mount argument!")
            if not isinstance(data[1], str):
                raise schema.SchemaError(None, "Expected string as second mount argument!")
            if len(data) == 3:
                self.__options.validate(data[2])
                return tuple(data)
            else:
                return (data[0], data[1], [])

        raise schema.SchemaError(None, "Mount entry must be a string or a two/three items list!")

class ToolValidator:
    def __init__(self, toolNameSchema):
        self.__schema = schema.Schema({
            "name" : toolNameSchema,
            schema.Optional("if"): schema.Or(str, IfExpression),
        })

    def validate(self, data):
        if isinstance(data, str):
            return (data, None)
        else:
            self.__schema.validate(data)
            return (data["name"], data.get("if"))

class PositiveValidator:
    def validate(self, data):
        if not isinstance(data, int):
            raise schema.SchemaError(None, "Value must be an integer")
        if data < 0:
            raise schema.SchemaError(None, "Int value must not be negative")
        return data

class RecipeSet:
    """The RecipeSet corresponds to the project root directory.

    It holds global information about the project.
    """

    BUILD_DEV_SCHEMA = schema.Schema(
        {
            schema.Optional('always_checkout') : [str],
            schema.Optional('attic') : bool,
            schema.Optional('audit') : bool,
            schema.Optional('build_mode') : schema.Or("build-only","normal", "checkout-only"),
            schema.Optional('clean') : bool,
            schema.Optional('clean_checkout') : bool,
            schema.Optional('destination') : str,
            schema.Optional('download') : schema.Or("yes", "no", "deps",
                "forced", "forced-deps", "forced-fallback",
                schema.Regex(r"^packages=.*$")),
            schema.Optional('download_layer') : [schema.Regex(r'^(yes|no|forced)=\S+$')],
            schema.Optional('force') : bool,
            schema.Optional('install') : bool,
            schema.Optional('jobs') : int,
            schema.Optional('link_deps') : bool,
            schema.Optional('no_deps') : bool,
            schema.Optional('no_logfiles') : bool,
            schema.Optional('sandbox') : schema.Or(bool, "no", "slim", "dev", "yes", "strict"),
            schema.Optional('shared') : bool,
            schema.Optional('upload') : bool,
            schema.Optional('verbosity') : int,
        })

    GRAPH_SCHEMA = schema.Schema(
        {
            schema.Optional('options') : schema.Schema({str : schema.Or(str, bool)}),
            schema.Optional('type') : schema.Or("d3", "dot"),
            schema.Optional('max_depth') : int,
        })

    # We do not support the "import" SCM for layers. It just makes no sense.
    # Also, all SCMs lack the "dir" and "if" attributes.
    LAYERS_SCM_SCHEMA = ScmValidator({
        'git' : GitScm.LAYERS_SCHEMA,
        'svn' : SvnScm.LAYERS_SCHEMA,
        'cvs' : CvsScm.LAYERS_SCHEMA,
        'url' : UrlScm.LAYERS_SCHEMA,
    })

    SCM_SCHEMA = ScmValidator({
        'git' : GitScm.SCHEMA,
        'svn' : SvnScm.SCHEMA,
        'cvs' : CvsScm.SCHEMA,
        'url' : UrlScm.SCHEMA,
        'import' : ImportScm.SCHEMA,
    })

    STATIC_CONFIG_LAYER_SPEC = {
        schema.Optional('layersWhitelist') : [str],
        schema.Optional('layersScmOverrides') : schema.Schema([{
            schema.Optional('match') : schema.Schema({ str: object }),
            schema.Optional('del') : [str],
            schema.Optional('set') : schema.Schema({ str: object }),
            schema.Optional('replace') : schema.Schema({
                str : schema.Schema({
                    'pattern' : str,
                    'replacement' : str
                })
            })
        }])
    }

    STATIC_CONFIG_SCHEMA_SPEC = {
        schema.Optional('bobMinimumVersion') : str, # validated separately in preValidate
        schema.Optional('plugins') : [str],
        schema.Optional('policies') : schema.Schema(
            {
                schema.Optional('relativeIncludes') : schema.Schema(True, "Cannot set old behaviour of relativeIncludes policy!"),
                schema.Optional('cleanEnvironment') : schema.Schema(True, "Cannot set old behaviour of cleanEnvironment policy!"),
                schema.Optional('tidyUrlScm') : schema.Schema(True, "Cannot set old behaviour of tidyUrlScm policy!"),
                schema.Optional('allRelocatable') : schema.Schema(True, "Cannot set old behaviour of allRelocatable policy!"),
                schema.Optional('offlineBuild') : schema.Schema(True, "Cannot set old behaviour of offlineBuild policy!"),
                schema.Optional('sandboxInvariant') : schema.Schema(True, "Cannot set old behaviour of sandboxInvariant policy!"),
                schema.Optional('uniqueDependency') : schema.Schema(True, "Cannot set old behaviour of uniqueDependency policy!"),
                schema.Optional('mergeEnvironment') : schema.Schema(True, "Cannot set old behaviour of mergeEnvironment policy!"),
                schema.Optional('secureSSL') : schema.Schema(True, "Cannot set old behaviour of secureSSL policy!"),
                schema.Optional('sandboxFingerprints') : schema.Schema(True, "Cannot set old behaviour of sandboxFingerprints policy!"),
                schema.Optional('fingerprintVars') : schema.Schema(True, "Cannot set old behaviour of fingerprintVars policy!"),
                schema.Optional('noUndefinedTools') : bool,
                schema.Optional('scmIgnoreUser') : bool,
                schema.Optional('pruneImportScm') : bool,
                schema.Optional('gitCommitOnBranch') : bool,
                schema.Optional('fixImportScmVariant') : bool,
                schema.Optional('defaultFileMode') : bool,
                schema.Optional('substituteMetaEnv') : bool,
                schema.Optional('managedLayers') : bool,
                schema.Optional('urlScmSeparateDownload') : bool,
                schema.Optional('failUnstableCheckouts') : bool,
            },
            error="Invalid policy specified! Are you using an appropriate version of Bob?"
        ),
        schema.Optional('layers') : [LayerValidator()],
        schema.Optional('scriptLanguage',
                        default=ScriptLanguage.BASH) : schema.And(schema.Or("bash", "PowerShell"),
                                                                  schema.Use(ScriptLanguage)),
    }

    MIRRORS_SCHEMA = ScmValidator({
        'url' : UrlScm.MIRRORS_SCHEMA,
    })

    POLICIES = {
        'noUndefinedTools' : (
            "0.17.3.dev57",
            InfoOnce("noUndefinedTools policy not set. Included but undefined tools are not detected at parsing time.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#noundefinedtools for more information.")
        ),
        'scmIgnoreUser' : (
            "0.17.3.dev97",
            InfoOnce("scmIgnoreUser policy not set. Authentication part URL is tainting binary artifacts.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#scmignoreuser for more information.")
        ),
        'pruneImportScm' : (
            "0.17.3.dev102",
            InfoOnce("pruneImportScm policy not set. Incremental builds of 'import' SCM may lead to wrong results.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#pruneimportscm for more information.")
        ),
        'gitCommitOnBranch' : (
            "0.21.0.dev5",
            InfoOnce("gitCommitOnBranch policy not set. Will not check if commit / tag is on configured branch.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#gitcommitonbranch for more information.")
        ),
        'fixImportScmVariant' : (
            "0.22.1.dev34",
            InfoOnce("fixImportScmVariant policy not set. Recipe variant calculation w/ import SCM is boguous.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#fiximportscmvariant for more information.")
        ),
        "defaultFileMode": (
            "0.24rc1",
            InfoOnce("defaultFileMode policy not set. File mode of URL SCMs not set for locally copied files.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#defaultfilemode for more information.")
        ),
        "substituteMetaEnv": (
            "0.25rc1",
            InfoOnce("substituteMetaEnv policy is not set. MetaEnv will not be substituted.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#substitutemetaenv for more information.")
        ),
        "managedLayers": (
            "0.25.0rc2.dev6",
            InfoOnce("managedLayers policy is not set. Only unmanaged layers are supported.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#managedlayers for more information.")
        ),
        "urlScmSeparateDownload": (
            "0.25.1.dev27",
            InfoOnce("urlScmSeparateDownload policy is not set. Extracted archives of the 'url' SCM are retained in the workspace.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#urlscmseparatedownload for more information.")
        ),
        "failUnstableCheckouts" : (
            "0.25.1.dev88",
            InfoOnce("failUnstableCheckouts policy is not set. Unexpected indeterministic checkouts are not flagged as error.",
                help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#failunstablecheckouts for more information.")
        ),
    }

    _ignoreCmdConfig = False
    @classmethod
    def ignoreCommandCfg(cls):
        cls._ignoreCmdConfig = True

    _colorModeConfig = None
    @classmethod
    def setColorModeCfg(cls, mode):
        cls._colorModeConfig = mode

    _queryMode = None
    @classmethod
    def setQueryMode(cls, mode):
        cls._queryMode = mode

    def __init__(self):
        self.__defaultEnv = {}
        self.__aliases = {}
        self.__recipes = {}
        self.__classes = {}
        self.__archive = []
        self.__rootFilter = []
        self.__scmOverrides = []
        self.__hooks = {}
        self.__projectGenerators = {}
        self.__configFiles = []
        self.__properties = {}
        self.__states = {}
        self.__cache = YamlCache()
        self.__stringFunctions = DEFAULT_STRING_FUNS.copy()
        self.__plugins = {}
        self.__commandConfig = {}
        self.__uiConfig = {}
        self.__shareConfig = {}
        self.__layers = {}
        self.__buildHooks = {}
        self.__sandboxOpts = {}
        self.__scmDefaults = {}
        self.__preMirrors = []
        self.__fallbackMirrors = []

        def appendArchive(x): self.__archive.extend(x)
        def prependArchive(x): self.__archive[0:0] = x
        def updateArchive(x): self.__archive = x
        def appendPreMirror(x) : self.__preMirrors.extend(x)
        def prependPreMirror(x) : self.__preMirrors[0:0] = x
        def updatePreMirror(x) : self.__preMirrors = x
        def appendFallbackMirror(x) : self.__fallbackMirrors.extend(x)
        def prependFallbackMirror(x) : self.__fallbackMirrors[0:0] = x
        def updateFallbackMirror(x) : self.__fallbackMirrors = x

        def updateWhiteList(x):
            if self.__platform == "win32":
                # Convert to upper case on Windows. The Python interpreter does that
                # too and the variables are considered case insensitive by Windows.
                self.__whiteList.update(i.upper() for i in x)
            else:
                self.__whiteList.update(x)

        def removeWhiteList(x):
            if self.__platform == "win32":
                # Convert to upper case on Windows. The Python interpreter does that
                # too and the variables are considered case insensitive by Windows.
                self.__whiteList.difference_update(i.upper() for i in x)
            else:
                self.__whiteList.difference_update(x)

        archiveValidator = ArchiveValidator()

        self.__settings = {
            "include" : SentinelSetting(),
            "require" : SentinelSetting(),

            "alias" : BuiltinSetting(
                schema.Schema({ schema.Regex(r'^[0-9A-Za-z_-]+$') : str }),
                lambda x: self.__aliases.update(x)
            ),
            "archive" : BuiltinSetting(archiveValidator, updateArchive, True),
            "archiveAppend" : BuiltinSetting(archiveValidator, appendArchive, True, 100),
            "archivePrepend" : BuiltinSetting(archiveValidator, prependArchive, True, 100),
            "command" : BuiltinSetting(
                schema.Schema({
                    schema.Optional('dev') : self.BUILD_DEV_SCHEMA,
                    schema.Optional('build') : self.BUILD_DEV_SCHEMA,
                    schema.Optional('graph') : self.GRAPH_SCHEMA
                }),
                lambda x: updateDicRecursive(self.__commandConfig, x) if not self._ignoreCmdConfig else None
            ),
            "environment" : BuiltinSetting(
                VarDefineValidator("environment", conditional=False),
                lambda x: self.__defaultEnv.update(x)
            ),
            "fallbackMirror"        : BuiltinSetting(self.MIRRORS_SCHEMA, updateFallbackMirror, True),
            "fallbackMirrorAppend"  : BuiltinSetting(self.MIRRORS_SCHEMA, appendFallbackMirror, True, 100),
            "fallbackMirrorPrepend" : BuiltinSetting(self.MIRRORS_SCHEMA, prependFallbackMirror, True, 100),
            "hooks" : BuiltinSetting(
                schema.Schema({
                    schema.Optional('preBuildHook') : str,
                    schema.Optional('postBuildHook') : str,
                }),
                lambda x: self.__buildHooks.update(x)
            ),
            "preMirror"        : BuiltinSetting(self.MIRRORS_SCHEMA, updatePreMirror, True),
            "preMirrorAppend"  : BuiltinSetting(self.MIRRORS_SCHEMA, appendPreMirror, True, 100),
            "preMirrorPrepend" : BuiltinSetting(self.MIRRORS_SCHEMA, prependPreMirror, True, 100),
            "rootFilter" : BuiltinSetting(
                schema.Schema([str]),
                lambda x: self.__rootFilter.extend(x)
            ),
            "sandbox" : BuiltinSetting(
                schema.Schema({
                    schema.Optional('mount') : schema.Schema([ MountValidator() ]),
                    schema.Optional('paths') : [str],
                    schema.Optional('user') : schema.Or("nobody", "root", "$USER"),
                }),
                lambda x: updateDicRecursive(self.__sandboxOpts, x),
                True
            ),
            "scmDefaults" : BuiltinSetting(
                schema.Schema({
                    schema.Optional('cvs') : schema.Schema(CvsScm.DEFAULTS),
                    schema.Optional('git') : schema.Schema(GitScm.DEFAULTS),
                    schema.Optional('import') : schema.Schema(ImportScm.DEFAULTS),
                    schema.Optional('svn') : schema.Schema(SvnScm.DEFAULTS),
                    schema.Optional('url') : schema.Schema(UrlScm.DEFAULTS)
                }),
                lambda x: updateDicRecursive(self.__scmDefaults, x)
            ),
            "scmOverrides" : BuiltinSetting(
                schema.Schema([{
                    schema.Optional('if') : schema.Or(str, IfExpression),
                    schema.Optional('match') : schema.Schema({ str: object }),
                    schema.Optional('del') : [str],
                    schema.Optional('set') : schema.Schema({ str: object }),
                    schema.Optional('replace') : schema.Schema({
                        str : schema.Schema({
                            'pattern' : str,
                            'replacement' : str
                        })
                    })
                }]),
                lambda x: self.__scmOverrides.extend([ ScmOverride(o) for o in x ])
            ),
            "share" : BuiltinSetting(
                schema.Schema({
                    'path' : str,
                    schema.Optional('quota') : schema.Or(None, int,
                        schema.Regex(r'^[0-9]+([KMGT](i?B)?)?$')),
                    schema.Optional('autoClean') : bool,
                }),
                lambda x: updateDicRecursive(self.__shareConfig, x),
            ),
            "ui" : BuiltinSetting(
                schema.Schema({
                    schema.Optional('color') : schema.Or('never', 'always', 'auto'),
                    schema.Optional('parallelTUIThreshold') : int,
                    schema.Optional('queryMode') : schema.Or('nullset', 'nullglob', 'nullfail'),
                }),
                lambda x: updateDicRecursive(self.__uiConfig, x)
            ),
            "whitelist" : BuiltinSetting(
                schema.Schema([ schema.Regex(r'^[^=]*$') ]),
                updateWhiteList
            ),
            "whitelistRemove" : BuiltinSetting(
                schema.Schema([ schema.Regex(r'^[^=]*$') ]),
                removeWhiteList,
                priority=100
            ),
        }

    def __addRecipe(self, recipe):
        name = recipe.getPackageName()
        if name in self.__recipes:
            raise ParseError("Package "+name+" already defined")
        self.__recipes[name] = recipe

    def __addClass(self, recipe):
        name = recipe.getPackageName()
        if name in self.__classes:
            raise ParseError("Class "+name+" already defined")
        self.__classes[name] = recipe

    def __loadPlugins(self, rootDir, layer, plugins):
        for p in plugins:
            name = os.path.join(rootDir, "plugins", p+".py")
            if not os.path.exists(name):
                raise ParseError("Plugin '"+name+"' not found!")
            mangledName = "__bob_plugin_" + "".join("layers_"+l+"_" for l in layer) + p
            self.__plugins[mangledName] = self.__loadPlugin(mangledName, name, p)

    def __loadPlugin(self, mangledName, fileName, name):
        # dummy load file to hash state
        self.loadBinary(fileName)
        pluginStat = binStat(fileName)
        try:
            from importlib.machinery import SourceFileLoader
            from importlib.util import spec_from_loader, module_from_spec
            loader = SourceFileLoader(mangledName, fileName)
            spec = spec_from_loader(mangledName, loader)
            mod = module_from_spec(spec)
            loader.exec_module(mod)
            sys.modules[spec.name] = mod
        except SyntaxError as e:
            import traceback
            raise ParseError("Error loading plugin "+fileName+": "+str(e),
                             help=traceback.format_exc())
        except Exception as e:
            raise ParseError("Error loading plugin "+fileName+": "+str(e))

        try:
            manifest = mod.manifest
        except AttributeError:
            raise ParseError("Plugin '"+fileName+"' did not define 'manifest'!")
        apiVersion = manifest.get('apiVersion')
        if apiVersion is None:
            raise ParseError("Plugin '"+fileName+"' did not define 'apiVersion'!")
        if compareVersion(BOB_VERSION, apiVersion) < 0:
            raise ParseError("Your Bob is too old. Plugin '"+fileName+"' requires at least version "+apiVersion+"!")
        toolsAbiBreak = compareVersion(apiVersion, "0.15") < 0

        hooks = manifest.get('hooks', {})
        if not isinstance(hooks, dict):
            raise ParseError("Plugin '"+fileName+"': 'hooks' has wrong type!")
        for (hook, fun) in hooks.items():
            if not isinstance(hook, str):
                raise ParseError("Plugin '"+fileName+"': hook name must be a string!")
            if not callable(fun):
                raise ParseError("Plugin '"+fileName+"': "+hook+": hook must be callable!")
            self.__hooks.setdefault(hook, []).append((fun, apiVersion))

        projectGenerators = manifest.get('projectGenerators', {})
        if not isinstance(projectGenerators, dict):
            raise ParseError("Plugin '"+fileName+"': 'projectGenerators' has wrong type!")
        if projectGenerators:
            if compareVersion(apiVersion, "0.16.1.dev33") < 0:
                # cut off extra argument for old generators
                projectGenerators = {
                    name : lambda package, args, extra, bobRoot: generator(package, args, extra)
                    for name, generator in projectGenerators.items()
                }
            projectGenerators = {
                name : generator if isinstance(generator, dict) else {'func' :  generator, 'query' : False }
                for name, generator in projectGenerators.items()
            }
            self.__projectGenerators.update(projectGenerators)

        properties = manifest.get('properties', {})
        if not isinstance(properties, dict):
            raise ParseError("Plugin '"+fileName+"': 'properties' has wrong type!")
        if properties:
            self.__pluginPropDeps += pluginStat
        for (i,j) in properties.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+fileName+"': property name must be a string!")
            if i[:1].islower():
                raise ParseError(f"Plugin '{fileName}': property '{i}' must not start lower case!")
            if not issubclass(j, PluginProperty):
                raise ParseError("Plugin '"+fileName+"': property '" +i+"' has wrong type!")
            if i in self.__properties:
                raise ParseError("Plugin '"+fileName+"': property '" +i+"' already defined by other plugin!")
        self.__properties.update(properties)

        states = manifest.get('state', {})
        if not isinstance(states, dict):
            raise ParseError("Plugin '"+fileName+"': 'states' has wrong type!")
        for (i,j) in states.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+fileName+"': state tracker name must be a string!")
            if i[:1].islower():
                raise ParseError(f"Plugin '{fileName}': state tracker '{i}' must not start lower case!")
            if not issubclass(j, PluginState):
                raise ParseError("Plugin '"+fileName+"': state tracker '" +i+"' has wrong type!")
            if i in self.__states:
                raise ParseError("Plugin '"+fileName+"': state tracker '" +i+"' already defined by other plugin!")
        if states and toolsAbiBreak:
            raise ParseError("Plugin '"+fileName+"': state requires at least apiVersion 0.15!")
        self.__states.update(states)

        funs = manifest.get('stringFunctions', {})
        if not isinstance(funs, dict):
            raise ParseError("Plugin '"+fileName+"': 'stringFunctions' has wrong type!")
        for (i,j) in funs.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+fileName+"': string function name must be a string!")
            if i in self.__stringFunctions:
                raise ParseError("Plugin '"+fileName+"': string function '" +i+"' already defined by other plugin!")
        if funs and toolsAbiBreak:
            raise ParseError("Plugin '"+fileName+"': stringFunctions requires at least apiVersion 0.15!")
        self.__stringFunctions.update(funs)

        settings = manifest.get('settings', {})
        if not isinstance(settings, dict):
            raise ParseError("Plugin '"+fileName+"': 'settings' has wrong type!")
        if settings:
            self.__pluginSettingsDeps += pluginStat
        for (i,j) in settings.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+fileName+"': settings name must be a string!")
            if i[:1].islower():
                raise ParseError("Plugin '"+fileName+"': settings name must not start lower case!")
            if not isinstance(j, PluginSetting):
                raise ParseError("Plugin '"+fileName+"': setting '"+i+"' has wrong type!")
            if i in self.__settings:
                raise ParseError("Plugin '"+fileName+"': setting '"+i+"' already defined by other plugin!")
        self.__settings.update(settings)

        return mod

    def defineHook(self, name, value):
        self.__hooks[name] = [(value, BOB_VERSION)]

    def setConfigFiles(self, configFiles):
        self.__configFiles = configFiles

    def getCommandConfig(self):
        return self.__commandConfig

    def getHook(self, name):
        # just return the hook function
        return self.__hooks[name][-1][0]

    def getHookStack(self, name):
        # return list of hook functions with API version
        return self.__hooks.get(name, [])

    def getProjectGenerators(self):
        return self.__projectGenerators

    def envWhiteList(self):
        """The set of all white listed environment variables

        :rtype: Set[str]
        """
        return set(self.__whiteList)

    def archiveSpec(self):
        return self.__archive

    def defaultEnv(self):
        """The default environment that each root recipe inherits

        :rtype: Mapping[str, str]
        """
        return self.__defaultEnv

    def scmDefaults(self):
        return self.__scmDefaults

    def scmOverrides(self):
        return self.__scmOverrides

    def getShareConfig(self):
        return self.__shareConfig

    async def getScmAudit(self):
        try:
            ret = self.__recipeScmAudit
        except AttributeError:
            ret = {}
            try:
                ret[""] = await auditFromDir(".")
            except BobError as e:
                Warn("could not determine recipes state").warn(e.slogan)

            # We look into every layers directory. If the Bob state knows it,
            # use the SCM information from there. Otherwise make a best guess.
            for layer, path in sorted(self.__layers.items()):
                state = None
                try:
                    scmProps = (BobState().getLayerState(path) or {}).get("prop")
                    if scmProps is None:
                        state = await auditFromDir(path)
                    else:
                        state = await auditFromProperties(path, scmProps)
                except BobError as e:
                    Warn(f"could not determine layer '{layer}' state").warn(e.slogan)
                ret[layer] = state

            self.__recipeScmAudit = ret
        return ret

    async def getScmStatus(self):
        scmAudit = await self.getScmAudit()
        return ", ".join(( ((f"layer {name}: " if name else "")
                            + ("unknown" if audit is None else audit.getStatusLine()))
                           for name, audit in sorted(scmAudit.items()) ))

    def getBuildHook(self, name):
        return self.__buildHooks.get(name)

    def getRootEnv(self):
        return self.__rootEnv

    def getSandboxMounts(self):
        return self.__sandboxOpts.get("mount", [])

    def getSandboxPaths(self):
        return list(reversed(self.__sandboxOpts.get("paths", [])))

    def getSandboxUser(self):
        return self.__sandboxOpts.get("user")

    def loadBinary(self, path):
        return self.__cache.loadBinary(path)

    def loadYaml(self, path, schema, default={}, preValidate=lambda x: None):
        return self.__cache.loadYaml(path, schema, default, preValidate)

    def parse(self, envOverrides={}, platform=getPlatformString(), recipesRoot=""):
        if not recipesRoot and os.path.isfile(".bob-project"):
            try:
                with open(".bob-project") as f:
                    recipesRoot = f.read()
            except OSError as e:
                raise ParseError("Broken project link: " + str(e))
        recipesDir = os.path.join(recipesRoot, "recipes")
        if not os.path.isdir(recipesDir):
            raise ParseError("No recipes directory found in " + recipesDir)
        self.__projectRoot = recipesRoot or os.getcwd()
        self.__cache.open()
        try:
            self.__parse(envOverrides, platform, recipesRoot)
        finally:
            self.__cache.close()

    def __parse(self, envOverrides, platform, recipesRoot=""):
        if platform not in ('cygwin', 'darwin', 'linux', 'msys', 'win32'):
            raise ParseError("Invalid platform: " + platform)
        self.__platform = platform
        self.__layers = {}
        self.__whiteList = getPlatformEnvWhiteList(platform)
        self.__pluginPropDeps = b''
        self.__pluginSettingsDeps = b''
        self.__createSchemas()

        # global user config(s)
        if not DEBUG['ngd']:
            self.__parseUserConfig("/etc/bobdefault.yaml")
            self.__parseUserConfig(os.path.join(os.environ.get('XDG_CONFIG_HOME',
                os.path.join(os.path.expanduser("~"), '.config')), 'bob', 'default.yaml'))

        # Begin with root layer
        allLayers = self.__parseLayer(LayerSpec(""), "9999", recipesRoot, None)

        # Parse all recipes and classes of all layers. Need to be done last
        # because only by now we have loaded all plugins.
        for layer, rootDir, scriptLanguage in allLayers:
            classesDir = os.path.join(rootDir, 'classes')
            for root, dirnames, filenames in os.walk(classesDir):
                for path in fnmatch.filter(filenames, "[!.]*.yaml"):
                    try:
                        [r] = Recipe.loadFromFile(self, layer, classesDir,
                            os.path.relpath(os.path.join(root, path), classesDir),
                            self.__properties, self.__classSchema, False)
                        self.__addClass(r)
                    except ParseError as e:
                        e.pushFrame(path)
                        raise

            recipesDir = os.path.join(rootDir, 'recipes')
            for root, dirnames, filenames in os.walk(recipesDir):
                for path in fnmatch.filter(filenames, "[!.]*.yaml"):
                    try:
                        recipes = Recipe.loadFromFile(self, layer, recipesDir,
                            os.path.relpath(os.path.join(root, path), recipesDir),
                            self.__properties, self.__recipeSchema, True, scriptLanguage)
                        for r in recipes:
                            self.__addRecipe(r)
                    except ParseError as e:
                        e.pushFrame(path)
                        raise

            aliasesDir = os.path.join(rootDir, 'aliases')
            for root, dirnames, filenames in os.walk(aliasesDir):
                for path in fnmatch.filter(filenames, "[!.]*.yaml"):
                    try:
                        aliasPackages = AliasPackage.loadFromFile(self, aliasesDir,
                            os.path.relpath(os.path.join(root, path), aliasesDir))
                        for p in aliasPackages:
                            self.__addRecipe(p)
                    except ParseError as e:
                        e.pushFrame(path)
                        raise

        # Out-of-tree builds may have a dedicated default.yaml
        if recipesRoot:
            self.__parseUserConfig("default.yaml")

        # config files overrule everything else
        for c in self.__configFiles:
            c = str(c) + ".yaml"
            if not os.path.isfile(c):
                raise ParseError("Config file {} does not exist!".format(c))
            self.__parseUserConfig(c)

        # calculate start environment
        osEnv = Env(os.environ)
        osEnv.setFuns(self.__stringFunctions)
        env = Env({ k : osEnv.substitute(v, k) for (k, v) in
            self.__defaultEnv.items() })
        env.setFuns(self.__stringFunctions)
        env.update(envOverrides)
        env["BOB_HOST_PLATFORM"] = platform
        self.__rootEnv = env

        # resolve recipes and their classes
        rootRecipes = []
        for recipe in self.__recipes.values():
            try:
                recipeEnv = env.copy()
                recipeEnv.setFunArgs({ "recipe" : recipe, "sandbox" : False,
                    "__tools" : {} })
                recipe.resolveClasses(recipeEnv)
            except ParseError as e:
                e.pushFrame(recipe.getPackageName())
                raise
            if recipe.isRoot():
                rootRecipes.append(recipe.getPackageName())

        filteredRoots = [ root for root in rootRecipes
                if (len(self.__rootFilter) == 0) or checkGlobList(root, maybeGlob(self.__rootFilter)) ]
        # create virtual root package
        self.__rootRecipe = Recipe.createVirtualRoot(self, sorted(filteredRoots), self.__properties)
        self.__addRecipe(self.__rootRecipe)

    @classmethod
    def loadConfigYaml(cls, loadYaml, rootDir):
        configYaml = os.path.join(rootDir, "config.yaml")
        def preValidate(data):
            if not isinstance(data, dict):
                raise ParseError("{}: invalid format".format(configYaml))
            minVer = data.get("bobMinimumVersion", "0.16")
            if not isinstance(minVer, str):
                raise ParseError("{}: bobMinimumVersion must be a string".format(configYaml))
            if not re.fullmatch(r'^[0-9]+(\.[0-9]+){0,2}(rc[0-9]+)?(.dev[0-9]+)?$', minVer):
                raise ParseError("{}: invalid bobMinimumVersion".format(configYaml))
            if compareVersion(BOB_VERSION, minVer) < 0:
                raise ParseError("Your Bob is too old. At least version "+minVer+" is required!")

        config_spec = {**cls.STATIC_CONFIG_SCHEMA_SPEC, **cls.STATIC_CONFIG_LAYER_SPEC}
        ret = loadYaml(configYaml, (schema.Schema(config_spec), b''),
            preValidate=preValidate)

        minVer = ret.get("bobMinimumVersion", "0.16")
        if compareVersion(minVer, "0.16") < 0:
            raise ParseError("Projects before bobMinimumVersion 0.16 are not supported!")

        return ret

    @classmethod
    def calculatePolicies(cls, config):
        minVer = config.get("bobMinimumVersion", "0.16")
        ret = { name : (True if compareVersion(ver, minVer) <= 0 else None, warn)
            for (name, (ver, warn)) in cls.POLICIES.items() }
        for (name, behaviour) in config.get("policies", {}).items():
            ret[name] = (behaviour, None)
        return ret

    def __parseLayer(self, layerSpec, maxVer, recipesRoot, upperLayer):
        layer = layerSpec.getName()
        if layer:
            # Managed layers imply that layers are potentially nested instead
            # of being checked out next to each other in the build directory.
            managedLayers = self.getPolicy('managedLayers')
            if layerSpec.getScm() is not None and not managedLayers:
                raise ParseError("Managed layers aren't enabled! See the managedLayers policy for details.")

            # Pre 0.25, layers could be nested.
            if not managedLayers and upperLayer:
                layer = upperLayer + "/" + layer

            if layer in self.__layers:
                return []

            if managedLayers:
                # SCM backed layers are in build dir, regular layers are in
                # project dir.
                rootDir = recipesRoot if layerSpec.getScm() is None else ""
                rootDir = os.path.join(rootDir, "layers", layer)
                if not os.path.isdir(rootDir):
                    raise ParseError(f"Layer '{layer}' does not exist!",
                                     help="You probably want to run 'bob layers update' to fetch missing layers.")
            else:
                # Before managed layers existed, layers could be nested in the
                # project directory.
                rootDir = os.path.join(recipesRoot, *( os.path.join("layers", l)
                                                       for l in layer.split("/") ))
                if not os.path.isdir(rootDir):
                    raise ParseError(f"Layer '{layer}' does not exist!")

            self.__layers[layer] = rootDir
        else:
            rootDir = recipesRoot

        config = self.loadConfigYaml(self.loadYaml, rootDir)
        minVer = config.get("bobMinimumVersion", "0.16")
        if compareVersion(maxVer, minVer) < 0:
            raise ParseError("Layer '{}' requires a higher Bob version than root project!"
                                .format(layer))
        maxVer = minVer # sub-layers must not have a higher bobMinimumVersion

        # Determine policies. The root layer determines the default settings
        # implicitly by bobMinimumVersion or explicitly via 'policies'. All
        # sub-layer policies must not contradict root layer policies
        if layer:
            for (name, behaviour) in config.get("policies", {}).items():
                if bool(self.__policies[name][0]) != behaviour:
                    raise ParseError("Layer '{}' requires different behaviour for policy '{}' than root project!"
                                        .format(layer, name))
        else:
            self.__policies = self.calculatePolicies(config)

        # First parse any sub-layers. Their settings have a lower precedence
        # and may be overwritten by higher layers.
        allLayers = []
        for l in config.get("layers", []):
            allLayers.extend(self.__parseLayer(l, maxVer, recipesRoot, layer))

        # Load plugins and re-create schemas as new keys may have been added
        self.__loadPlugins(rootDir, layer, config.get("plugins", []))
        self.__createSchemas()

        # project user config(s)
        self.__parseUserConfig(os.path.join(rootDir, "default.yaml"))

        # color mode provided in cmd line takes precedence
        # (if no color mode provided by user, default one will be used)
        setColorMode(self._colorModeConfig or self.__uiConfig.get('color', 'auto'))
        setParallelTUIThreshold(self.__uiConfig.get('parallelTUIThreshold', 16))

        allLayers.append((layer, rootDir, config["scriptLanguage"]))
        return allLayers

    def __parseUserConfig(self, fileName):
        cfg = self.loadYaml(fileName, self.__userConfigSchema)
        # merge settings by priority
        for (name, value) in sorted(cfg.items(), key=lambda i: self.__settings[i[0]].priority):
            self.__settings[name].merge(value)
        for p in cfg.get("require", []):
            p = os.path.join(os.path.dirname(fileName), p) + ".yaml"
            if not os.path.isfile(p):
                raise ParseError("Include file '{}' (required by '{}') does not exist!"
                                    .format(p, fileName))
            self.__parseUserConfig(p)
        for p in cfg.get("include", []):
            p = os.path.join(os.path.dirname(fileName), p)
            self.__parseUserConfig(p + ".yaml")

    def __createSchemas(self):
        varNameUseSchema = schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$')
        varFilterSchema = schema.Regex(r'^!?[][A-Za-z_*?][][A-Za-z0-9_*?]*$')
        recipeFilterSchema = schema.Regex(r'^!?[][0-9A-Za-z_.+:*?-]+$')
        toolNameSchema = schema.Regex(r'^[0-9A-Za-z_.+:-]+$')

        useClauses = ['deps', 'environment', 'result', 'tools', 'sandbox']
        useClauses.extend(self.__states.keys())

        # construct recursive depends clause
        dependsInnerClause = {
            schema.Optional('name') : str,
            schema.Optional('use') : useClauses,
            schema.Optional('forward') : bool,
            schema.Optional('inherit') : bool,
            schema.Optional('environment') : VarDefineValidator("depends::environment"),
            schema.Optional('if') : schema.Or(str, IfExpression),
            schema.Optional('tools') : { toolNameSchema : toolNameSchema },
            schema.Optional('checkoutDep') : bool,
            schema.Optional('alias') : str,
        }
        dependsClause = schema.Schema([
            schema.Or(
                str,
                schema.Schema(dependsInnerClause)
            )
        ])
        dependsInnerClause[schema.Optional('depends')] = dependsClause

        classSchemaSpec = {
            schema.Optional('checkoutScript') : str,
            schema.Optional('checkoutScriptBash') : str,
            schema.Optional('checkoutScriptPwsh') : str,
            schema.Optional('checkoutSetup') : str,
            schema.Optional('checkoutSetupBash') : str,
            schema.Optional('checkoutSetupPwsh') : str,
            schema.Optional('checkoutUpdateIf', default=False) : schema.Or(None, str, bool, IfExpression),
            schema.Optional('buildScript') : str,
            schema.Optional('buildScriptBash') : str,
            schema.Optional('buildScriptPwsh') : str,
            schema.Optional('buildSetup') : str,
            schema.Optional('buildSetupBash') : str,
            schema.Optional('buildSetupPwsh') : str,
            schema.Optional('packageScript') : str,
            schema.Optional('packageScriptBash') : str,
            schema.Optional('packageScriptPwsh') : str,
            schema.Optional('packageSetup') : str,
            schema.Optional('packageSetupBash') : str,
            schema.Optional('packageSetupPwsh') : str,
            schema.Optional('checkoutTools') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('buildTools') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('packageTools') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('checkoutToolsWeak') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('buildToolsWeak') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('packageToolsWeak') : [ ToolValidator(toolNameSchema) ],
            schema.Optional('checkoutVars') : [ varNameUseSchema ],
            schema.Optional('buildVars') : [ varNameUseSchema ],
            schema.Optional('packageVars') : [ varNameUseSchema ],
            schema.Optional('checkoutVarsWeak') : [ varNameUseSchema ],
            schema.Optional('buildVarsWeak') : [ varNameUseSchema ],
            schema.Optional('packageVarsWeak') : [ varNameUseSchema ],
            schema.Optional('checkoutDeterministic') : bool,
            schema.Optional('checkoutSCM') : self.SCM_SCHEMA,
            schema.Optional('checkoutAssert') : [ CheckoutAssert.SCHEMA ],
            schema.Optional('depends') : dependsClause,
            schema.Optional('environment') : VarDefineValidator("environment"),
            schema.Optional('inherit') : [str],
            schema.Optional('privateEnvironment') : VarDefineValidator("privateEnvironment"),
            schema.Optional('metaEnvironment') : VarDefineValidator("metaEnvironment"),
            schema.Optional('provideDeps') : [str],
            schema.Optional('provideTools') : schema.Schema({
                str: schema.Or(
                    str,
                    schema.Schema({
                        'path' : str,
                        schema.Optional('libs') : [str],
                        schema.Optional('netAccess') : bool,
                        schema.Optional('environment') : VarDefineValidator("provideTools::environment"),
                        schema.Optional('fingerprintScript', default="") : str,
                        schema.Optional('fingerprintScriptBash') : str,
                        schema.Optional('fingerprintScriptPwsh') : str,
                        schema.Optional('fingerprintIf') : schema.Or(None, str, bool, IfExpression),
                        schema.Optional('fingerprintVars') : [ varNameUseSchema ],
                    })
                )
            }),
            schema.Optional('provideVars') : VarDefineValidator("provideVars"),
            schema.Optional('provideSandbox') : schema.Schema({
                'paths' : [str],
                schema.Optional('mount') : schema.Schema([ MountValidator() ],
                    error="provideSandbox: invalid 'mount' property"),
                schema.Optional('environment') : VarDefineValidator("provideSandbox::environment"),
                schema.Optional('user') : schema.Or("nobody", "root", "$USER"),
            }),
            schema.Optional('root') : schema.Or(bool, str, IfExpression),
            schema.Optional('shared') : bool,
            schema.Optional('relocatable') : bool,
            schema.Optional('buildNetAccess') : bool,
            schema.Optional('packageNetAccess') : bool,
            schema.Optional('fingerprintScript', default="") : str,
            schema.Optional('fingerprintScriptBash') : str,
            schema.Optional('fingerprintScriptPwsh') : str,
            schema.Optional('fingerprintIf') : schema.Or(None, str, bool, IfExpression),
            schema.Optional('fingerprintVars') : [ varNameUseSchema ],
            schema.Optional('scriptLanguage') : schema.And(schema.Or("bash", "PowerShell"),
                                                           schema.Use(ScriptLanguage)),
            schema.Optional('jobServer') : bool,
        }
        for (name, prop) in self.__properties.items():
            classSchemaSpec[schema.Optional(name)] = schema.Schema(prop.validate,
                error="property '"+name+"' has an invalid type")

        self.__classSchema = (schema.Schema(classSchemaSpec), self.__pluginPropDeps)

        recipeSchemaSpec = classSchemaSpec.copy()
        recipeSchemaSpec[schema.Optional('multiPackage')] = schema.Schema({
            MULTIPACKAGE_NAME_SCHEMA : recipeSchemaSpec
        })
        self.__recipeSchema = (schema.Schema(recipeSchemaSpec), self.__pluginPropDeps)

        userConfigSchemaSpec = {
            schema.Optional('include') : schema.Schema([str]),
            schema.Optional('require') : schema.Schema([str]),
        }
        for (name, setting) in self.__settings.items():
            userConfigSchemaSpec[schema.Optional(name)] = schema.Schema(setting.validate,
                error="setting '"+name+"' has an invalid type")
        self.__userConfigSchema = (schema.Schema(userConfigSchemaSpec), self.__pluginSettingsDeps)


    def getRecipe(self, packageName):
        if packageName not in self.__recipes:
            raise ParseError("Package {} requested but not found.".format(packageName))
        return self.__recipes[packageName]

    def getClass(self, className):
        if className not in self.__classes:
            raise ParseError("Class {} requested but not found.".format(className))
        return self.__classes[className]

    def __generatePackages(self, pathsConfig, cacheKey, sandboxEnabled):
        # use separate caches with and without sandbox
        if sandboxEnabled:
            cacheName = ".bob-packages-sb.pickle"
        else:
            cacheName = ".bob-packages.pickle"

        # try to load the persisted packages
        try:
            with open(cacheName, "rb") as f:
                persistedCacheKey = f.read(len(cacheKey))
                if cacheKey == persistedCacheKey:
                    tmp = PackageUnpickler(f, self.getRecipe, self.__plugins,
                                           pathsConfig).load()
                    return tmp.refDeref([], {}, None, pathsConfig)
        except FileNotFoundError:
            pass
        except Exception as e:
            Warn("Could not load package cache: " + str(e)).show(cacheName)

        # not cached -> calculate packages
        states = { n:s() for (n,s) in self.__states.items() }
        result = self.__rootRecipe.prepare(self.__rootEnv, sandboxEnabled, states)[0]

        # save package tree for next invocation
        try:
            newCacheName = cacheName + ".new"
            with open(newCacheName, "wb") as f:
                f.write(cacheKey)
                PackagePickler(f, pathsConfig).dump(result)
            replacePath(newCacheName, cacheName)
        except OSError as e:
            Warn("Could not save package cache: " + str(e)).show(cacheName)

        return result.refDeref([], {}, None, pathsConfig)

    def generatePackages(self, nameFormatter, sandboxEnabled=False, stablePaths=None):
        """Generate package set.

        :param nameFormatter: Function returning path for a given step.
        :type nameFormatter: Callable[[Step, Mapping[str, PluginState]], str]
        :param sandboxEnabled: Enable sandbox image dependencies.
        :type sandboxEnabled: bool
        :param stablePaths: Configure usage of stable execution paths (/bob/...).
            * ``None``: Use stable path in sandbox image, otherwise workspace path.
            * ``False``: Always use workspace path.
            * ``True``: Always use stable path (/bob/...).
        :type stablePaths: None | bool
        """
        pathsConfig = PathsConfig(nameFormatter, stablePaths)
        # calculate cache key for persisted packages
        h = hashlib.sha1()
        h.update(BOB_INPUT_HASH)
        h.update(self.__cache.getDigest())
        h.update(struct.pack("<I", len(self.__rootEnv)))
        for (key, val) in sorted(self.__rootEnv.inspect().items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        h.update(b'\x01' if sandboxEnabled else b'\x00')
        cacheKey = h.digest()

        return PackageSet(cacheKey, self.__aliases, self.__stringFunctions,
            lambda: self.__generatePackages(pathsConfig, cacheKey, sandboxEnabled),
            self._queryMode or  self.__uiConfig.get('queryMode', 'nullglob'))

    def getPolicy(self, name, location=None):
        (policy, warning) = self.__policies[name]
        if policy is None:
            warning.show(location)
        return policy

    def getProjectRoot(self):
        """Get project root directory.

        The project root is where the recipes, classes and layers are located.
        In case of out-of-tree builds it will be distinct from the build
        directory.
        """
        return self.__projectRoot

    def getPreMirrors(self):
        return self.__preMirrors

    def getFallbackMirrors(self):
        return self.__fallbackMirrors


class YamlCache:
    def __if_expression_constructor(loader, node):
        expr = loader.construct_scalar(node)
        return IfExpression(expr)

    YamlSafeLoader.add_constructor(u'!expr', __if_expression_constructor)

    def open(self):
        try:
            self.__con = sqlite3.connect(".bob-cache.sqlite3", isolation_level=None)
            self.__cur = self.__con.cursor()
            self.__cur.execute("CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY, value)")
            self.__cur.execute("CREATE TABLE IF NOT EXISTS yaml(name PRIMARY KEY, stat, digest, data)")

            # check if Bob was changed
            self.__cur.execute("BEGIN")
            self.__cur.execute("SELECT value FROM meta WHERE key='vsn'")
            vsn = self.__cur.fetchone()
            if (vsn is None) or (vsn[0] != BOB_INPUT_HASH):
                # Bob was changed or new workspace -> purge cache
                self.__cur.execute("INSERT OR REPLACE INTO meta VALUES ('vsn', ?)", (BOB_INPUT_HASH,))
                self.__cur.execute("DELETE FROM yaml")
                self.__hot = False
            else:
                # This could work
                self.__hot = True
        except sqlite3.Error as e:
            raise ParseError("Cannot access cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        self.__files = {}

    def close(self):
        try:
            self.__cur.execute("END")
            self.__cur.close()
            self.__con.close()
        except sqlite3.Error as e:
            raise ParseError("Cannot commit cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        h = hashlib.sha1()
        for (name, data) in sorted(self.__files.items()):
            h.update(struct.pack("<I", len(name)))
            h.update(name.encode('utf8'))
            h.update(data)
        self.__digest = h.digest()

    def getDigest(self):
        return self.__digest

    def loadYaml(self, name, yamlSchema, default={}, preValidate=lambda x: None):
        try:
            bs = binStat(name) + yamlSchema[1]
            if self.__hot:
                self.__cur.execute("SELECT digest, data FROM yaml WHERE name=? AND stat=?",
                                    (name, bs))
                cached = self.__cur.fetchone()
                if cached is not None:
                    self.__files[name] = cached[0]
                    return pickle.loads(cached[1])

            with open(name, "r", encoding='utf8') as f:
                try:
                    rawData = f.read()
                    data = yamlLoad(rawData, Loader=YamlSafeLoader)
                    digest = hashlib.sha1(rawData.encode('utf8')).digest()
                except Exception as e:
                    raise ParseError("Error while parsing {}: {}".format(name, str(e)))

            if data is None: data = default
            preValidate(data)
            try:
                data = yamlSchema[0].validate(data)
            except schema.SchemaError as e:
                raise ParseError("Error while validating {}: {}".format(name, str(e)))

            self.__files[name] = digest
            self.__cur.execute("INSERT OR REPLACE INTO yaml VALUES (?, ?, ?, ?)",
                (name, bs, digest, pickle.dumps(data)))
        except sqlite3.Error as e:
            raise ParseError("Cannot access cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        except FileNotFoundError:
            return yamlSchema[0].validate(default)
        except OSError as e:
            raise ParseError("Error loading yaml file: " + str(e))

        return data

    def loadBinary(self, name):
        with open(name, "rb") as f:
            result = f.read()
        self.__files[name] = hashlib.sha1(result).digest()
        return result

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


class PackagePickler(pickle.Pickler):
    def __init__(self, file, pathsConfig):
        super().__init__(file, -1, fix_imports=False)
        self.__pathsConfig = pathsConfig

    def persistent_id(self, obj):
        if obj is self.__pathsConfig:
            return ("pathscfg", None)
        elif isinstance(obj, Recipe):
            return ("recipe", obj.getPackageName())
        else:
            return None

class PackageUnpickler(pickle.Unpickler):
    def __init__(self, file, recipeGetter, plugins, pathsConfig):
        super().__init__(file)
        self.__recipeGetter = recipeGetter
        self.__plugins = plugins
        self.__pathsConfig = pathsConfig

    def persistent_load(self, pid):
        (tag, key) = pid
        if tag == "pathscfg":
            return self.__pathsConfig
        elif tag == "recipe":
            return self.__recipeGetter(key)
        else:
            raise pickle.UnpicklingError("unsupported object")

    def find_class(self, module, name):
        if module.startswith("__bob_plugin_"):
            return getattr(self.__plugins[module], name)
        else:
            return super().find_class(module, name)

