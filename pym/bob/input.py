# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_VERSION, BOB_INPUT_HASH, DEBUG
from .errors import ParseError
from .pathspec import PackageSet
from .scm import CvsScm, GitScm, SvnScm, UrlScm, ScmOverride, auditFromDir
from .state import BobState
from .stringparser import checkGlobList, Env, DEFAULT_STRING_FUNS
from .tty import colorize, InfoOnce, WarnOnce, setColorMode
from .utils import asHexStr, joinScripts, sliceString, compareVersion, binStat, updateDicRecursive
from abc import ABCMeta, abstractmethod
from base64 import b64encode
from itertools import chain
from glob import glob
from pipes import quote
from os.path import expanduser
from string import Template
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
import yaml

warnFilter = WarnOnce("The filter keyword is experimental and might change or vanish in the future.")
warnDepends = WarnOnce("The same package is named multiple times as dependency!",
    help="Only the first such incident is reported. This behavior will be treated as an error in the future.")

def overlappingPaths(p1, p2):
    p1 = os.path.normcase(os.path.normpath(p1)).split(os.sep)
    if p1 == ["."]: p1 = []
    p2 = os.path.normcase(os.path.normpath(p2)).split(os.sep)
    if p2 == ["."]: p2 = []
    for i in range(min(len(p1), len(p2))):
        if p1[i] != p2[i]: return False
    return True

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

    def onEnter(self, env, tools, properties):
        """Begin creation of a package.

        The state tracker is about to witness the creation of a package. The passed
        environment, tools and (custom) properties are in their initial state that
        was inherited from the parent recipe.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param tools: All upstream declared or inherited tools
        :type tools: Mapping[str, :class:`bob.input.Tool`]
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

    def onFinish(self, env, tools, properties, package):
        """Finish creation of a package.

        The package was computed and the result is available as parameter
        *package*. The passed *env*, *tools*, and *properties* have their final
        state after all downstream dependencies have been resolved.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param tools: All upstream declared or inherited tools
        :type tools: Mapping[str, :class:`bob.input.Tool`]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        :param bob.input.Package packages: The created package
        """
        pass

def Scm(spec, env, overrides, recipeSet):
    # resolve with environment
    spec = { k : ( env.substitute(v, "checkoutSCM::"+k) if isinstance(v, str) else v)
        for (k, v) in spec.items() }

    # apply overrides before creating scm instances. It's possible to switch the Scm type with an override..
    matchedOverrides = []
    for override in overrides:
        matched, spec = override.mangle(spec)
        if matched:
            matchedOverrides.append(override)

    # create scm instance
    scm = spec["scm"]
    if scm == "git":
        return GitScm(spec, matchedOverrides)
    elif scm == "svn":
        return SvnScm(spec, matchedOverrides)
    elif scm == "cvs":
        return CvsScm(spec, matchedOverrides)
    elif scm == "url":
        return UrlScm(spec, matchedOverrides, recipeSet.getPolicy('tidyUrlScm'))
    else:
        raise ParseError("Unknown SCM '{}'".format(scm))

class AbstractTool:
    __slots__ = ("path", "libs")

    def __init__(self, spec):
        if isinstance(spec, str):
            self.path = spec
            self.libs = []
        else:
            self.path = spec['path']
            self.libs = spec.get('libs', [])

    def prepare(self, step, env):
        """Create concrete tool for given step."""
        path = env.substitute(self.path, "provideTools::path")
        libs = [ env.substitute(l, "provideTools::libs") for l in self.libs ]
        return Tool(step, path, libs)

class CoreTool:
    __slots__ = ("step", "path", "libs")

    def __init__(self, tool, upperPackage):
        self.step = CoreStepRef(upperPackage, tool.step)
        self.path = tool.path
        self.libs = tool.libs

    def toTool(self, pathFormatter, upperPackage):
        return Tool(self.step.toStep(pathFormatter, upperPackage),
            self.path, self.libs)

class Tool:
    """Representation of a tool.

    A tool is made of the result of a package, a relative path into this result
    and some optional relative library paths.
    """

    __slots__ = ("step", "path", "libs")

    def __init__(self, step, path, libs):
        self.step = step
        self.path = path
        self.libs = libs

    def __repr__(self):
        return "Tool({}, {}, {})".format(repr(self.step), self.path, self.libs)

    def __eq__(self, other):
        return isinstance(other, Tool) and (self.step == other.step) and (self.path == other.path) and \
            (self.libs == other.libs)

    def toCoreTool(self, upperPackage):
        return CoreTool(self, upperPackage)

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

class CoreSandbox:
    __slots__ = ("step", "enabled", "paths", "mounts")

    def __init__(self, sandbox, upperPackage):
        self.step = CoreStepRef(upperPackage, sandbox.step)
        self.enabled = sandbox.enabled
        self.paths = sandbox.paths
        self.mounts = sandbox.mounts

    def toSandbox(self, pathFormatter, upperPackage):
        ret = Sandbox()
        ret.reconstruct(self.step.toStep(pathFormatter, upperPackage),
            self.enabled, self.paths, self.mounts)
        return ret

class Sandbox:
    """Represents a sandbox that is used when executing a step."""

    __slots__ = ("step", "enabled", "paths", "mounts")

    def __eq__(self, other):
        return isinstance(other, Sandbox) and (self.step == other.step) and (self.enabled == other.enabled) and \
            (self.paths == other.paths) and (self.mounts == other.mounts)

    def construct(self, step, env, enabled, spec):
        self.step = step
        self.enabled = enabled
        self.paths = spec['paths']
        self.mounts = []
        for mount in spec.get('mount', []):
            m = (env.substitute(mount[0], "provideSandbox::mount-from"),
                 env.substitute(mount[1], "provideSandbox::mount-to"),
                 mount[2])
            # silently drop empty mount lines
            if (m[0] != "") and (m[1] != ""):
                self.mounts.append(m)
        return self

    def reconstruct(self, step, enabled, paths, mounts):
        self.step = step
        self.enabled = enabled
        self.paths = paths
        self.mounts = mounts
        return self

    def toCoreSandbox(self, upperPackage):
        return CoreSandbox(self, upperPackage)

    def getStep(self):
        """Get the package step that yields the content of the sandbox image."""
        return self.step

    def getPaths(self):
        """Return list of global search paths.

        This is the base $PATH in the sandbox."""
        return self.paths

    def getMounts(self):
        """Get custom mounts.

        This returns a list of tuples where each tuple has the format
        (hostPath, sandboxPath, options).
        """
        return self.mounts

    def isEnabled(self):
        """Return True if the sandbox is used in the current build configuration."""
        return self.enabled

def diffTools(upperTools, argTools, upperPackage, relevantTools):
    ret = {}
    for name in ((set(upperTools.keys()) | set(argTools.keys())) & relevantTools):
        if name not in argTools:
            # filtered tool
            ret[name] = None
        elif upperTools.get(name) != argTools[name]:
            # new or changed tool
            ret[name] = argTools[name].toCoreTool(upperPackage)

    return ret

def diffSandbox(upperSandbox, argSandbox, upperPackage):
    if upperSandbox == argSandbox:
        return ...
    elif argSandbox is None:
        return None
    else:
        return argSandbox.toCoreSandbox(upperPackage)

def patchTools(inputTools, patch, pathFormatter, upperPackage):
    if patch != {}:
        tools = inputTools.copy()
        for (name, tool) in patch.items():
            if tool is None:
                del tools[name]
            else:
                tools[name] = tool.toTool(pathFormatter, upperPackage)
    else:
        tools = inputTools

    return tools

def patchSandbox(inputSandbox, patch, pathFormatter, upperPackage):
    if patch is ...:
        return inputSandbox
    elif patch is None:
        return None
    else:
        return patch.toSandbox(pathFormatter, upperPackage)

class CoreStepRef:
    __slots__ = ("step", "stackAdder", "inputTools", "inputSandbox")

    def __init__(self, upperPackage, argStep):
        self.step = argStep._getCoreStep()
        argPackage = argStep.getPackage()

        stackPrefixLen = len(upperPackage.getStack())
        argStack = argPackage.getStack()
        self.stackAdder = argStack[stackPrefixLen:]

        self.inputTools = diffTools(upperPackage._getInputTools(),
                                    argPackage._getInputTools(),
                                    upperPackage, argPackage._getTouchedTools())
        self.inputSandbox = diffSandbox(upperPackage._getInputSandboxRaw(), argPackage._getInputSandboxRaw(), upperPackage)

    def toStep(self, pathFormatter, upperPackage):
        packageInputTools = patchTools(upperPackage._getInputTools(),
            self.inputTools, pathFormatter, upperPackage)
        packageInputSandbox = patchSandbox(upperPackage._getInputSandboxRaw(),
            self.inputSandbox, pathFormatter, upperPackage)

        return self.step.getStep(pathFormatter, upperPackage.getStack() + self.stackAdder,
            packageInputTools, packageInputSandbox)

class CoreStep(metaclass=ABCMeta):
    __slots__ = ( "package", "label", "tools", "digestEnv", "env", "args",
        "shared", "doesProvideTools", "providedEnv", "providedTools",
        "providedDeps", "providedSandbox", "variantId", "sandbox", "sbxVarId",
        "deterministic" )

    def __init__(self, step, label, tools, sandbox, digestEnv, env, args, shared):
        package = step.getPackage()
        self.package = package._getCorePackage()
        self.label = label
        self.tools = list(tools.keys())
        self.sandbox = sandbox is not None
        self.digestEnv = digestEnv
        self.env = env
        self.args = [ CoreStepRef(package, a) for a in args ]
        self.shared = shared
        self.doesProvideTools = False
        self.providedEnv = {}
        self.providedTools = {}
        self.providedDeps = []
        self.providedSandbox = None

    @abstractmethod
    def _createStep(self, package):
        pass

    def getStep(self, pathFormatter, stack, inputTools, inputSandbox):
        package = Package()
        package.reconstruct(self.package, pathFormatter, stack, inputTools, inputSandbox)
        step = self._createStep(package)
        step.reconstruct(package, self, pathFormatter,
            package._getSandboxRaw() if self.sandbox else None)
        return step

    def getStepOfPackage(self, package, pathFormatter):
        step = self._createStep(package)
        step.reconstruct(package, self, pathFormatter,
            package._getSandboxRaw() if self.sandbox else None)
        return step

class Step(metaclass=ABCMeta):
    """Represents the smallest unit of execution of a package.

    A step is what gets actually executed when building packages.

    Steps can be compared and sorted. This is done based on the Variant-Id of
    the step. See :meth:`bob.input.Step.getVariantId` for details.
    """

    def construct(self, package, pathFormatter, sandbox, label, digestEnv=Env(),
                 env=Env(), tools=Env(), args=[], shared=False):
        # detach from tracking
        digestEnv = digestEnv.detach()
        env = env.detach()
        tools = tools.detach()

        # always present fields
        self.__package = package
        self.__pathFormatter = pathFormatter
        self.__sandbox = sandbox

        # lazy created fields
        self.__tools = tools
        self.__args = args

        # only used during package calculation
        self.__providedTools = {}
        self.__providedDeps = []
        self.__providedSandbox = None

        # will call back to us!
        self._coreStep = self._createCoreStep(label, tools, sandbox, digestEnv,
            env, args, shared)

    def reconstruct(self, package, coreStep, pathFormatter, sandbox):
        self.__package = package
        self._coreStep = coreStep
        self.__pathFormatter = pathFormatter
        self.__sandbox = sandbox

    def __repr__(self):
        return "Step({}, {}, {})".format(self.getLabel(), "/".join(self.getPackage().getStack()), asHexStr(self.getVariantId()))

    def __hash__(self):
        return hash(self.getVariantId())

    def __lt__(self, other):
        return self.getVariantId() < other.getVariantId()

    def __le__(self, other):
        return self.getVariantId() <= other.getVariantId()

    def __eq__(self, other):
        return self.getVariantId() == other.getVariantId()

    def __ne__(self, other):
        return self.getVariantId() != other.getVariantId()

    def __gt__(self, other):
        return self.getVariantId() > other.getVariantId()

    def __ge__(self, other):
        return self.getVariantId() >= other.getVariantId()

    @abstractmethod
    def _createCoreStep(self, label, tools, sandbox, digestEnv, env, args, shared):
        pass

    def _getCoreStep(self):
        return self._coreStep

    @abstractmethod
    def getScript(self):
        """Return a single big script of the whole step.

        Besides considerations of special backends (such as Jenkins) this
        script is what should be executed to build this step."""
        pass

    @abstractmethod
    def getJenkinsScript(self):
        """Return the relevant parts as shell script that have no Jenkins plugin."""
        pass

    @abstractmethod
    def getDigestScript(self):
        """Return a long term stable script.

        The digest script will not be executed but is the basis to calculate if
        the step has changed. In case of the checkout step the involved SCMs will
        return a stable representation of _what_ is checked out and not the real
        script of _how_ this is done.
        """
        pass

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
        try:
            ret = self._coreStep.deterministic
        except AttributeError:
            ret = self._coreStep.deterministic = all(
                arg.isDeterministic() for arg in self.getAllDepSteps(True))
        return ret

    def isValid(self):
        """Returns True if this step is valid, False otherwise."""
        return self.getScript() is not None

    def isCheckoutStep(self):
        """Return True if this is a checkout step."""
        return False

    def isBuildStep(self):
        """Return True if this is a build step."""
        return False

    def isPackageStep(self):
        """Return True if this is a package step."""
        return False

    def getPackage(self):
        """Get Package object that is the parent of this Step."""
        return self.__package

    def getDigest(self, calculate, forceSandbox=False, hasher=hashlib.sha1):
        h = hasher()
        if self.__sandbox and (self.__sandbox.isEnabled() or forceSandbox):
            d = calculate(self.__sandbox.getStep())
            if d is None: return None
            h.update(d)
            h.update(struct.pack("<I", len(self.__sandbox.getPaths())))
            for p in self.__sandbox.getPaths():
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        else:
            h.update(b'\x00' * 20)
        script = self.getDigestScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        h.update(struct.pack("<I", len(self.getTools())))
        for (name, tool) in sorted(self.getTools().items(), key=lambda t: t[0]):
            d = calculate(tool.step)
            if d is None: return None
            h.update(d)
            h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
            h.update(tool.path.encode("utf8"))
            for l in tool.libs:
                h.update(struct.pack("<I", len(l)))
                h.update(l.encode('utf8'))
        h.update(struct.pack("<I", len(self._coreStep.digestEnv)))
        for (key, val) in sorted(self._coreStep.digestEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        args = [ a for a in self.getArguments() if a.isValid() ]
        h.update(struct.pack("<I", len(args)))
        for arg in args:
            d = calculate(arg)
            if d is None: return None
            h.update(d)
        return h.digest()

    def getVariantId(self):
        """Return Variant-Id of this Step.

        The Variant-Id is used to distinguish different packages or multiple
        variants of a package. Each Variant-Id need only be built once but
        successive builds might yield different results (e.g. when building
        from branches)."""
        try:
            ret = self._coreStep.variantId
        except AttributeError:
            ret = self._coreStep.variantId = self.getDigest(lambda step: step.getVariantId())
        return ret

    def _getSandboxVariantId(self):
        try:
            ret = self._coreStep.sbxVarId
        except AttributeError:
            ret = self._coreStep.sbxVarId = self.getDigest(
                lambda step: step._getSandboxVariantId(),
                True)
        return ret

    def _getResultId(self):
        h = hashlib.sha1()
        h.update(self.getVariantId())
        # providedEnv
        h.update(struct.pack("<I", len(self._coreStep.providedEnv)))
        for (key, val) in sorted(self._coreStep.providedEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        # providedTools
        providedTools = self._getProvidedTools()
        h.update(struct.pack("<I", len(providedTools)))
        for (name, tool) in sorted(providedTools.items()):
            h.update(tool.step.getVariantId())
            h.update(struct.pack("<III", len(name), len(tool.path), len(tool.libs)))
            h.update(name.encode("utf8"))
            h.update(tool.path.encode("utf8"))
            for l in tool.libs:
                h.update(struct.pack("<I", len(l)))
                h.update(l.encode('utf8'))
        # provideDeps
        providedDeps = self._getProvidedDeps()
        h.update(struct.pack("<I", len(providedDeps)))
        for dep in providedDeps:
            h.update(dep.getVariantId())
        # sandbox
        providedSandbox = self._getProvidedSandbox()
        if providedSandbox and providedSandbox.isEnabled():
            h.update(providedSandbox.getStep().getVariantId())
            h.update(struct.pack("<I", len(providedSandbox.getPaths())))
            for p in providedSandbox.getPaths():
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        else:
            h.update(b'\x00' * 20)

        return h.digest()

    def getSandbox(self, forceSandbox=False):
        """Return Sandbox used in this Step.

        Returns a Sandbox object or None if this Step is built without one.
        """
        if self.__sandbox and (self.__sandbox.isEnabled() or forceSandbox):
            return self.__sandbox
        else:
            return None

    def getLabel(self):
        """Return path label for step.

        This is currently defined as "src", "build" and "dist" for the
        respective steps.
        """
        return self._coreStep.label

    def getExecPath(self):
        """Return the execution path of the step.

        The execution path is where the step is actually run. It may be distinct
        from the workspace path if the build is performed in a sandbox.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'exec', self.__package._getStates())
        else:
            return "/invalid/exec/path/of/{}".format(self.__package.getName())

    def getWorkspacePath(self):
        """Return the workspace path of the step.

        The workspace path represents the location of the step in the users
        workspace. When building in a sandbox this path is not passed to the
        script but the one from getExecPath() instead.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'workspace', self.__package._getStates())
        else:
            return "/invalid/workspace/path/of/{}".format(self.__package.getName())

    def getPaths(self):
        """Get sorted list of execution paths to used tools.

        The returned list is intended to be passed as PATH environment variable.
        The paths are sorted by name.
        """
        return sorted([ os.path.join(tool.step.getExecPath(), tool.path)
            for tool in self.getTools().values() ])

    def getLibraryPaths(self):
        """Get sorted list of library paths of used tools.

        The returned list is intended to be passed as LD_LIBRARY_PATH environment
        variable. The paths are first sorted by tool name. The order of paths of
        a single tool is kept.
        """
        paths = []
        for (name, tool) in sorted(self.getTools().items()):
            paths.extend([ os.path.join(tool.step.getExecPath(), l) for l in tool.libs ])
        return paths

    def getTools(self):
        """Get dictionary of tools.

        The dict maps the tool name to a :class:`bob.input.Tool`.
        """
        try:
            ret = self.__tools
        except AttributeError:
            ret = { name : self.__package._getAllTools()[name] for name in self._coreStep.tools }
        return ret

    def getArguments(self):
        """Get list of all inputs for this Step.

        The arguments are passed as absolute paths to the script starting from $1.
        """
        try:
            ret = self.__args
        except AttributeError:
            ret = [ a.toStep(self.__pathFormatter, self.__package)
                                  for a in self._coreStep.args ]
        return ret

    def getAllDepSteps(self, forceSandbox=False):
        """Get all dependent steps of this Step.

        This includes the direct input to the Step as well as indirect inputs
        such as the used tools or the sandbox.
        """
        return self.getArguments() + [ d.step for n,d in sorted(self.getTools().items()) ] + (
            [self.__sandbox.getStep()]
                if (self.__sandbox and (self.__sandbox.isEnabled() or forceSandbox))
                else [])

    def getEnv(self):
        """Return dict of environment variables."""
        return self._coreStep.env

    def doesProvideTools(self):
        """Return True if this step provides at least one tool."""
        return self._coreStep.doesProvideTools

    def isShared(self):
        """Returns True if the result of the Step should be shared globally.

        The exact behaviour of a shared step/package depends on the build
        backend. In general a shared package means that the result is put into
        some shared location where it is likely that the same result is needed
        again.
        """
        return self._coreStep.shared

    def isRelocatable(self):
        """Returns True if the step is relocatable."""
        return False

    def _setProvidedEnv(self, provides):
        self._coreStep.providedEnv = provides

    def _getProvidedEnv(self):
        return self._coreStep.providedEnv

    def _setProvidedTools(self, provides):
        self.__providedTools = provides
        self._coreStep.doesProvideTools = provides != {}
        self._coreStep.providedTools = { n : p.toCoreTool(self.__package) for (n,p) in provides.items() }

    def _getProvidedTools(self):
        try:
            ret = self.__providedTools
        except AttributeError:
            ret = { n: p.toTool(self.__pathFormatter, self.__package)
                for (n,p) in self._coreStep.providedTools.items() }
        return ret

    def _setProvidedDeps(self, deps):
        self.__providedDeps = deps
        self._coreStep.providedDeps = [ CoreStepRef(self.__package, d) for d in deps ]

    def _getProvidedDeps(self):
        try:
            ret = self.__providedDeps
        except AttributeError:
            ret = [ d.toStep(self.__pathFormatter, self.__package)
                                  for d in self._coreStep.providedDeps ]
        return ret

    def _setProvidedSandbox(self, sandbox):
        self.__providedSandbox = sandbox
        self._coreStep.providedSandbox = sandbox.toCoreSandbox(self.__package) \
            if sandbox is not None else None

    def _getProvidedSandbox(self):
        try:
            ret = self.__providedSandbox
        except AttributeError:
            sandbox = self._coreStep.providedSandbox
            ret = sandbox.toSandbox(self.__pathFormatter, self.__package) \
                if sandbox is not None else None
        return ret

class CoreCheckoutStep(CoreStep):
    __slots__ = ( "script", "digestScript", "scmList", "coDeterministic" )

    def _createStep(self, package):
        ret = CheckoutStep()
        package._reconstructCheckoutStep(ret)
        return ret

class CheckoutStep(Step):
    def construct(self, package, pathFormatter, sandbox=None, checkout=None,
                  fullEnv=Env(), digestEnv=Env(), env=Env(), tools=Env(),
                  deterministic=False):
        super().construct(package, pathFormatter, sandbox, "src", digestEnv,
                         env, tools)

        if checkout:
            self._coreStep.script = checkout[0] if checkout[0] is not None else ""
            self._coreStep.digestScript = checkout[1] if checkout[1] is not None else ""
            self._coreStep.coDeterministic = deterministic

            recipeSet = package.getRecipe().getRecipeSet()
            overrides = recipeSet.scmOverrides()
            self._coreStep.scmList = [ Scm(scm, fullEnv, overrides, recipeSet)
                for scm in checkout[2]
                if fullEnv.evaluate(scm.get("if"), "checkoutSCM") ]

            # Validate that SCM paths do not overlap
            knownPaths = []
            for s in self._coreStep.scmList:
                for p in s.getDirectories().keys():
                    if os.path.isabs(p):
                        raise ParseError("SCM paths must be relative! Offending path: " + p)
                    for known in knownPaths:
                        if overlappingPaths(known, p):
                            raise ParseError("SCM paths '{}' and '{}' overlap."
                                                .format(known, p))
                    knownPaths.append(p)
        else:
            self._coreStep.script = None
            self._coreStep.digestScript = None
            self._coreStep.scmList = []
            self._coreStep.coDeterministic = True

        return self

    def _createCoreStep(self, label, tools, sandbox, digestEnv, env, args, shared):
        return CoreCheckoutStep(self, label, tools, sandbox, digestEnv, env, args, shared)

    def isCheckoutStep(self):
        return True

    def getScript(self):
        script = self._coreStep.script
        if script is not None:
            return joinScripts([s.asScript() for s in self._coreStep.scmList] + [script])
        else:
            return None

    def getJenkinsScript(self):
        return joinScripts([ s.asScript() for s in self._coreStep.scmList if not s.hasJenkinsPlugin() ]
            + [self._coreStep.script])

    def getDigestScript(self):
        if self._coreStep.script is not None:
            return "\n".join([s.asDigestScript() for s in self._coreStep.scmList] + [self._coreStep.digestScript])
        else:
            return None

    def getJenkinsXml(self, credentials, options):
        return [ s.asJenkins(self.getWorkspacePath(), credentials, options)
                 for s in self._coreStep.scmList if s.hasJenkinsPlugin() ]

    def getScmList(self):
        return self._coreStep.scmList

    def getScmDirectories(self):
        dirs = {}
        for s in self._coreStep.scmList:
            dirs.update(s.getDirectories())
        return dirs

    def isDeterministic(self):
        return ( self._coreStep.coDeterministic and
                 all([ s.isDeterministic() for s in self._coreStep.scmList ]) and
                 super().isDeterministic() )

    def hasLiveBuildId(self):
        """Check if live build-ids are supported.

        This must be supported by all SCMs. Additionally the checkout script
        must be deterministic.
        """
        return ( self._coreStep.coDeterministic and
                 all(s.hasLiveBuildId() for s in self._coreStep.scmList) and
                 super().isDeterministic() )

    def predictLiveBuildId(self):
        """Query server to predict live build-id.

        Returns the live-build-id or None if an SCM query failed.
        """
        if not self.hasLiveBuildId():
            return None
        h = hashlib.sha1()
        h.update(self._getSandboxVariantId())
        for s in self._coreStep.scmList:
            liveBId = s.predictLiveBuildId()
            if liveBId is None: return None
            h.update(liveBId)
        return h.digest()

    def calcLiveBuildId(self):
        """Calculate live build-id from workspace."""
        if not self.hasLiveBuildId():
            return None
        workspacePath = self.getWorkspacePath()
        h = hashlib.sha1()
        h.update(self._getSandboxVariantId())
        for s in self._coreStep.scmList:
            liveBId = s.calcLiveBuildId(workspacePath)
            if liveBId is None: return None
            h.update(liveBId)
        return h.digest()

    def getLiveBuildIdSpec(self):
        """Generate spec lines for bob-hash-engine.

        May return None if an SCM does not support live-build-ids on Jenkins.
        """
        if not self.hasLiveBuildId():
            return None
        workspacePath = self.getWorkspacePath()
        lines = [ "{sha1", "=" + asHexStr(self._getSandboxVariantId()) ]
        for s in self._coreStep.scmList:
            liveBIdSpec = s.getLiveBuildIdSpec(workspacePath)
            if liveBIdSpec is None: return None
            lines.append(liveBIdSpec)
        lines.append("}")
        return "\n".join(lines)


class RegularStep(Step):
    def construct(self, package, pathFormatter, sandbox, label, script=(None, None),
                 digestEnv=Env(), env=Env(), tools=Env(), args=[], shared=False):
        super().construct(package, pathFormatter, sandbox, label, digestEnv,
                          env, tools, args, shared)
        self._coreStep.script = script[0]
        self._coreStep.digestScript = script[1]

    def getScript(self):
        return self._coreStep.script

    def getJenkinsScript(self):
        return self._coreStep.script

    def getDigestScript(self):
        return self._coreStep.digestScript

class CoreBuildStep(CoreStep):
    __slots__ = [ "script", "digestScript" ]

    def _createStep(self, package):
        ret = BuildStep()
        package._reconstructBuildStep(ret)
        return ret

class BuildStep(RegularStep):
    def construct(self, package, pathFormatter, sandbox=None, script=(None, None),
                  digestEnv=Env(), env=Env(), tools=Env(), args=[]):
        super().construct(package, pathFormatter, sandbox, "build", script,
                          digestEnv, env, tools, args)
        return self

    def _createCoreStep(self, label, tools, sandbox, digestEnv, env, args, shared):
        return CoreBuildStep(self, label, tools, sandbox, digestEnv, env, args, shared)

    def isBuildStep(self):
        return True

class CorePackageStep(CoreStep):
    __slots__ = [ "script", "digestScript" ]

    def _createStep(self, package):
        ret = PackageStep()
        package._reconstructPackageStep(ret)
        return ret

class PackageStep(RegularStep):
    def construct(self, package, pathFormatter, sandbox=None, script=(None, None),
                  digestEnv=Env(), env=Env(), tools=Env(), args=[], shared=False):
        super().construct(package, pathFormatter, sandbox, "dist", script,
                          digestEnv, env, tools, args, shared)
        return self

    def _createCoreStep(self, label, tools, sandbox, digestEnv, env, args, shared):
        return CorePackageStep(self, label, tools, sandbox, digestEnv, env, args, shared)

    def isPackageStep(self):
        return True

    def isRelocatable(self):
        """Returns True if the package step is relocatable."""
        return self.getPackage().isRelocatable()


class CorePackage:
    __slots__ = ("name", "recipe", "directDepSteps", "indirectDepSteps",
        "states", "metaEnv", "tools", "sandbox", "checkoutStep", "buildStep", "packageStep",
        "touchedTools", "pkgId")

    def __init__(self, package, name, recipe, directDepSteps, indirectDepSteps,
                 states, metaEnv, pkgId):
        self.name = name
        self.recipe = recipe
        self.directDepSteps = [ CoreStepRef(package, d) for d in directDepSteps ]
        self.indirectDepSteps = [ CoreStepRef(package, d) for d in indirectDepSteps ]
        self.states = states
        self.metaEnv = metaEnv
        self.tools = {}
        self.sandbox = ...
        self.pkgId = pkgId

class Package(object):
    """Representation of a package that was created from a recipe.

    Usually multiple packages will be created from a single recipe. This is
    either due to multiple upstream recipes or different variants of the same
    package. This does not preclude the possibility that multiple Package
    objects describe exactly the same package (read: same Variant-Id). It is
    the responsibility of the build backend to detect this and build only one
    package.
    """

    def __eq__(self, other):
        return isinstance(other, Package) and (self.__stack == other.__stack)

    def construct(self, name, stack, pathFormatter, recipe,
                  directDepSteps, indirectDepSteps, states, inputTools,
                  allTools, inputSandbox, sandbox, touchedTools, metaEnv,
                  pkgId):
        self.__stack = stack
        self.__pathFormatter = pathFormatter
        self.__directDepSteps = directDepSteps
        self.__indirectDepSteps = indirectDepSteps
        self.__inputTools = inputTools
        self.__allTools = allTools
        self.__inputSandbox = inputSandbox
        self.__sandbox = sandbox

        # this will call back
        self.__corePackage = CorePackage(self, name, recipe, directDepSteps,
            indirectDepSteps, states, metaEnv, pkgId)

        # these already need our __corePackage
        self._setCheckoutStep(CheckoutStep().construct(self, pathFormatter))
        self._setBuildStep(BuildStep().construct(self, pathFormatter))
        self._setPackageStep(PackageStep().construct(self, pathFormatter))

        # calculate local tools and sandbox
        self.__corePackage.touchedTools = set(touchedTools)
        self.__corePackage.tools = diffTools(inputTools, allTools, self, self.__corePackage.touchedTools)
        self.__corePackage.sandbox = diffSandbox(inputSandbox, sandbox, self)

        return self

    def reconstruct(self, corePackage, pathFormatter, stack, inputTools, inputSandbox):
        self.__corePackage = corePackage
        self.__stack = stack
        self.__pathFormatter = pathFormatter
        self.__inputTools = inputTools
        self.__inputSandbox = inputSandbox

        self.__allTools = patchTools(inputTools, corePackage.tools, pathFormatter, self)
        self.__sandbox = patchSandbox(inputSandbox, corePackage.sandbox, pathFormatter, self)

        return self

    def _getId(self):
        """The package-Id is uniquely representing every package variant.

        On the package level there might be more dependencies than on the step
        level. Meta variables are usually unused and also do not contribute to
        the variant-id. The package-id still guarantees to not collide in these
        cases. OTOH there can be identical packages with different ids, though
        it should be an unusual case.
        """
        return self.__corePackage.pkgId

    def _getTouchedTools(self):
        return self.__corePackage.touchedTools

    def _getCorePackage(self):
        return self.__corePackage

    def _getInputTools(self):
        return self.__inputTools

    def _getAllTools(self):
        return self.__allTools

    def _getInputSandboxRaw(self):
        return self.__inputSandbox

    def _getSandboxRaw(self):
        return self.__sandbox

    def getName(self):
        """Name of the package"""
        return self.__corePackage.name

    def getMetaEnv(self):
        """meta variables of package"""
        return self.__corePackage.metaEnv

    def getStack(self):
        """Returns the recipe processing stack leading to this package.

        The method returns a list of package names. The first entry is a root
        recipe and the last entry is this package."""
        return self.__stack

    def getRecipe(self):
        """Return Recipe object that was the template for this package."""
        return self.__corePackage.recipe

    def getDirectDepSteps(self):
        """Return list to the package steps of the direct dependencies.

        Direct dependencies are the ones that are named explicitly in the
        ``depends`` section of the recipe. The order of the items is
        preserved from the recipe.
        """
        try:
            ret = self.__directDepSteps
        except AttributeError:
            ret = [ d.toStep(self.__pathFormatter, self)
                for d in self.__corePackage.directDepSteps ]
        return ret

    def getIndirectDepSteps(self):
        """Return list of indirect dependencies of the package.

        Indirect dependencies are dependencies that were provided by downstream
        recipes. They are not directly named in the recipe.
        """
        try:
            ret = self.__indirectDepSteps
        except AttributeError:
            ret = [ d.toStep(self.__pathFormatter, self)
                for d in self.__corePackage.indirectDepSteps ]
        return ret

    def getAllDepSteps(self, forceSandbox=False):
        """Return list of all dependencies of the package.

        This list includes all direct and indirect dependencies. Additionally
        the used sandbox and tools are included too."""
        allDeps = set(self.getDirectDepSteps())
        allDeps |= set(self.getIndirectDepSteps())
        if self.__sandbox and (self.__sandbox.isEnabled() or forceSandbox):
            allDeps.add(self.__sandbox.getStep())
        for i in self.getPackageStep().getTools().values(): allDeps.add(i.getStep())
        return sorted(allDeps)

    def _reconstructCheckoutStep(self, checkoutStep):
        self.__checkoutStep = checkoutStep

    def _setCheckoutStep(self, checkoutStep):
        self.__corePackage.checkoutStep = checkoutStep._getCoreStep()
        self.__checkoutStep = checkoutStep

    def getCheckoutStep(self):
        """Return the checkout step of this package."""
        try:
            ret = self.__checkoutStep
        except AttributeError:
            ret = self.__corePackage.checkoutStep.getStepOfPackage(self,
                self.__pathFormatter)
        return ret

    def _reconstructBuildStep(self, buildStep):
        self.__buildStep = buildStep

    def _setBuildStep(self, buildStep):
        self.__corePackage.buildStep = buildStep._getCoreStep()
        self.__buildStep = buildStep

    def getBuildStep(self):
        """Return the build step of this package."""
        try:
            ret = self.__buildStep
        except AttributeError:
            ret = self.__corePackage.buildStep.getStepOfPackage(self,
                self.__pathFormatter)
        return ret

    def _reconstructPackageStep(self, packageStep):
        self.__packageStep = packageStep

    def _setPackageStep(self, packageStep):
        self.__corePackage.packageStep = packageStep._getCoreStep()
        self.__packageStep = packageStep

    def getPackageStep(self):
        """Return the package step of this package."""
        try:
            ret = self.__packageStep
        except AttributeError:
            ret = self.__corePackage.packageStep.getStepOfPackage(self,
                self.__pathFormatter)
        return ret

    def _getStates(self):
        return self.__corePackage.states

    def isRelocatable(self):
        """Returns True if the packages is relocatable."""
        return self.__corePackage.recipe.isRelocatable()


# FIXME: implement this on our own without the Template class. How to do proper
# escaping?
class IncludeHelper:

    class Resolver:
        def __init__(self, fileLoader, baseDir, varBase, origText):
            self.fileLoader = fileLoader
            self.baseDir = baseDir
            self.varBase = varBase
            self.prolog = []
            self.incDigests = [ asHexStr(hashlib.sha1(origText.encode('utf8')).digest()) ]
            self.count = 0

        def __getitem__(self, item):
            mode = item[0]
            item = item[1:]
            content = []
            try:
                paths = sorted(glob(os.path.join(self.baseDir, item)))
                if not paths:
                    raise ParseError("No files matched in include pattern '{}'!"
                        .format(item))
                for path in paths:
                    content.append(self.fileLoader(path))
            except OSError as e:
                raise ParseError("Error including '"+item+"': " + str(e))
            content = b''.join(content)

            self.incDigests.append(asHexStr(hashlib.sha1(content).digest()))
            if mode == '<':
                var = "_{}{}".format(self.varBase, self.count)
                self.count += 1
                self.prolog.extend([
                    "{VAR}=$(mktemp)".format(VAR=var),
                    "_BOB_TMP_CLEANUP+=( ${VAR} )".format(VAR=var),
                    "base64 -d > ${VAR} <<EOF".format(VAR=var)])
                self.prolog.extend(sliceString(b64encode(content).decode("ascii"), 76))
                self.prolog.append("EOF")
                ret = "${" + var + "}"
            else:
                assert mode == "'"
                ret = quote(content.decode('utf8'))

            return ret

    def __init__(self, fileLoader, baseDir, varBase, sourceName):
        self.__pattern = re.compile(r"""
            \$<(?:
                (?P<escaped>\$)     |
                (?P<named>[<'][^'>]+)['>]>  |
                (?P<braced>[<'][^'>]+)['>]> |
                (?P<invalid>)
            )
            """, re.VERBOSE)
        self.__baseDir = baseDir
        self.__varBase = re.sub(r'[^a-zA-Z0-9_]', '_', varBase, flags=re.DOTALL)
        self.__fileLoader = fileLoader
        self.__sourceName = sourceName

    def resolve(self, text, section):
        if isinstance(text, str):
            resolver = IncludeHelper.Resolver(self.__fileLoader, self.__baseDir, self.__varBase, text)
            t = Template(text)
            t.delimiter = '$<'
            t.pattern = self.__pattern
            try:
                ret = t.substitute(resolver)
            except ValueError as e:
                raise ParseError("Bad substiturion in {}: {}".format(section, str(e)))
            sourceAnchor = "_BOB_SOURCES[$LINENO]=" + quote(self.__sourceName)
            return ("\n".join(resolver.prolog + [sourceAnchor, ret]), "\n".join(resolver.incDigests))
        else:
            return (None, None)

def mergeFilter(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left + right

class ScmValidator:
    def __init__(self, scmSpecs):
        self.__scmSpecs = scmSpecs

    def __validateScm(self, scm):
        if 'scm' not in scm:
            raise schema.SchemaMissingKeyError("Missing 'scm' key in {}".format(scm), None)
        if scm['scm'] not in self.__scmSpecs.keys():
            raise schema.SchemaWrongKeyError('Invalid SCM: {}'.format(scm['scm']), None)
        self.__scmSpecs[scm['scm']].validate(scm)

    def validate(self, data):
        if isinstance(data, dict):
            self.__validateScm(data)
        elif isinstance(data, list):
            for i in data: self.__validateScm(i)
        else:
            raise schema.SchemaUnexpectedTypeError(
                'checkoutSCM must be a SCM spec or a list threreof',
                None)
        return data


RECIPE_NAME_SCHEMA = schema.Regex(r'^[0-9A-Za-z_.+-]+$')
MULTIPACKAGE_NAME_SCHEMA = schema.Regex(r'^[0-9A-Za-z_.+-]*$')

class UniquePackageList:
    def __init__(self, errorHandler):
        self.errorHandler = errorHandler
        self.ret = []
        self.cache = {}

    def append(self, p):
        p2 = self.cache.setdefault(p.getPackage().getName(), p)
        if p2 is p:
            self.ret.append(p)
        elif p2.getVariantId() != p.getVariantId():
            self.errorHandler(p, p2)

    def result(self):
        return self.ret

class Recipe(object):
    """Representation of a single recipe

    Multiple instaces of this class will be created if the recipe used the
    ``multiPackage`` keyword.  In this case the getName() method will return
    the name of the original recipe but the getPackageName() method will return
    it with some addition suffix. Without a ``multiPackage`` keyword there will
    only be one Recipe instance.
    """

    class Dependency(object):
        def __init__(self, recipe, env, fwd, use, cond):
            self.recipe = recipe
            self.envOverride = env
            self.provideGlobal = fwd
            self.use = use
            self.useEnv = "environment" in self.use
            self.useTools = "tools" in self.use
            self.useBuildResult = "result" in self.use
            self.useDeps = "deps" in self.use
            self.useSandbox = "sandbox" in self.use
            self.condition = cond

        @staticmethod
        def __parseEntry(dep, env, fwd, use, cond):
            if isinstance(dep, str):
                return [ Recipe.Dependency(dep, env, fwd, use, cond) ]
            else:
                envOverride = dep.get("environment")
                if envOverride:
                    env = env.copy()
                    env.update(envOverride)
                fwd = dep.get("forward", fwd)
                use = dep.get("use", use)
                newCond = dep.get("if")
                if newCond is not None:
                    cond = "$(and,{},{})".format(cond, newCond) if cond is not None else newCond
                name = dep.get("name")
                if name:
                    if "depends" in dep:
                        raise ParseError("A dependency must not use 'name' and 'depends' at the same time!")
                    return [ Recipe.Dependency(name, env, fwd, use, cond) ]
                dependencies = dep.get("depends")
                if dependencies is None:
                    raise ParseError("Either 'name' or 'depends' required for dependencies!")
                return Recipe.Dependency.parseEntries(dependencies, env, fwd, use, cond)

        @staticmethod
        def parseEntries(deps, env={}, fwd=False, use=["result", "deps"], cond=None):
            """Returns an iterator yielding all dependencies as flat list"""
            # return flattened list of dependencies
            return chain.from_iterable(
                Recipe.Dependency.__parseEntry(dep, env, fwd, use, cond)
                for dep in deps )

    @staticmethod
    def loadFromFile(recipeSet, fileName, properties, fileSchema, isRecipe):
        # MultiPackages are handled as separate recipes with an anonymous base
        # class. Ignore first dir in path, which is 'recipes' by default.
        # Following dirs are treated as categories separated by '::'.
        baseName = os.path.splitext( fileName )[0].split( os.sep )[1:]
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
                anonBaseClass = Recipe(recipeSet, recipe, fileName, baseDir,
                    anonNameCalculator(suffix), baseName, properties, isRecipe,
                    anonBaseClass)
                return chain.from_iterable(
                    collect(subSpec, suffix + ("-"+subName if subName else ""),
                            anonBaseClass)
                    for (subName, subSpec) in recipe["multiPackage"].items() )
            else:
                packageName = baseName + suffix
                return [ Recipe(recipeSet, recipe, fileName, baseDir, packageName,
                                baseName, properties, isRecipe, anonBaseClass) ]

        return list(collect(recipeSet.loadYaml(fileName, fileSchema), "", None))

    @staticmethod
    def createVirtualRoot(recipeSet, roots, properties):
        recipe = {
            "depends" : [
                { "name" : name, "use" : ["result"] } for name in roots
            ],
            "buildScript" : "true",
            "packageScript" : "true"
        }
        ret = Recipe(recipeSet, recipe, "", ".", "", "", properties)
        ret.resolveClasses()
        return ret

    def __init__(self, recipeSet, recipe, sourceFile, baseDir, packageName, baseName,
                 properties, isRecipe=True, anonBaseClass=None):
        self.__recipeSet = recipeSet
        self.__sources = [ sourceFile ] if anonBaseClass is None else []
        self.__classesResolved = False
        self.__inherit = recipe.get("inherit", [])
        self.__anonBaseClass = anonBaseClass
        self.__deps = list(Recipe.Dependency.parseEntries(recipe.get("depends", [])))
        filt = recipe.get("filter", {})
        if filt: warnFilter.warn(baseName)
        self.__filterEnv = maybeGlob(filt.get("environment"))
        self.__filterTools = maybeGlob(filt.get("tools"))
        self.__filterSandbox = maybeGlob(filt.get("sandbox"))
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
        self.__toolDepCheckout = set(recipe.get("checkoutTools", []))
        self.__toolDepBuild = set(recipe.get("buildTools", []))
        self.__toolDepBuild |= self.__toolDepCheckout
        self.__toolDepPackage = set(recipe.get("packageTools", []))
        self.__toolDepPackage |= self.__toolDepBuild
        self.__shared = recipe.get("shared")
        self.__relocatable = recipe.get("relocatable")
        self.__properties = {
            n : p(n in recipe, recipe.get(n))
            for (n, p) in properties.items()
        }
        self.__corePackages = []

        sourceName = ("Recipe " if isRecipe else "Class  ") + packageName
        incHelper = IncludeHelper(recipeSet.loadBinary, baseDir, packageName,
                                  sourceName)

        (checkoutScript, checkoutDigestScript) = incHelper.resolve(recipe.get("checkoutScript"), "checkoutScript")
        checkoutSCMs = recipe.get("checkoutSCM", [])
        if isinstance(checkoutSCMs, dict):
            checkoutSCMs = [checkoutSCMs]
        elif not isinstance(checkoutSCMs, list):
            raise ParseError("checkoutSCM must be a dict or a list")
        i = 0
        for scm in checkoutSCMs:
            scm["__source"] = sourceName
            scm["recipe"] = "{}#{}".format(sourceFile, i)
            i += 1
        self.__checkout = (checkoutScript, checkoutDigestScript, checkoutSCMs)
        self.__build = incHelper.resolve(recipe.get("buildScript"), "buildScript")
        self.__package = incHelper.resolve(recipe.get("packageScript"), "packageScript")

        # Consider checkout deterministic by default if no checkout script is
        # involved.
        self.__checkoutDeterministic = recipe.get("checkoutDeterministic", checkoutScript is None)

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

    def resolveClasses(self):
        # must be done only once
        if self.__classesResolved: return
        self.__classesResolved = True

        # calculate order of classes (depth first) but ignore ourself
        inherit = self.__resolveClassesOrder(self, [], set(), True)

        # inherit classes
        inherit.reverse()
        for cls in inherit:
            self.__sources.extend(cls.__sources)
            self.__deps[0:0] = cls.__deps
            self.__filterEnv = mergeFilter(self.__filterEnv, cls.__filterEnv)
            self.__filterTools = mergeFilter(self.__filterTools, cls.__filterTools)
            self.__filterSandbox = mergeFilter(self.__filterSandbox, cls.__filterSandbox)
            if self.__root is None: self.__root = cls.__root
            if self.__shared is None: self.__shared = cls.__shared
            if self.__relocatable is None: self.__relocatable = cls.__relocatable
            tmp = cls.__provideTools.copy()
            tmp.update(self.__provideTools)
            self.__provideTools = tmp
            tmp = cls.__provideVars.copy()
            tmp.update(self.__provideVars)
            self.__provideVars = tmp
            self.__provideDeps |= cls.__provideDeps
            if self.__provideSandbox is None: self.__provideSandbox = cls.__provideSandbox
            tmp = cls.__varSelf.copy()
            tmp.update(self.__varSelf)
            self.__varSelf = tmp
            tmp = cls.__varPrivate.copy()
            tmp.update(self.__varPrivate)
            self.__varPrivate = tmp
            self.__checkoutVars |= cls.__checkoutVars
            tmp = cls.__metaEnv.copy()
            tmp.update(self.__metaEnv)
            self.__metaEnv = tmp
            self.__checkoutVarsWeak |= cls.__checkoutVarsWeak
            self.__buildVars |= cls.__buildVars
            self.__buildVarsWeak |= cls.__buildVarsWeak
            self.__packageVars |= cls.__packageVars
            self.__packageVarsWeak |= cls.__packageVarsWeak
            self.__toolDepCheckout |= cls.__toolDepCheckout
            self.__toolDepBuild |= cls.__toolDepBuild
            self.__toolDepPackage |= cls.__toolDepPackage
            (checkoutScript, checkoutDigestScript, checkoutSCMs) = self.__checkout
            self.__checkoutDeterministic = self.__checkoutDeterministic and cls.__checkoutDeterministic
            # merge scripts
            checkoutScript = joinScripts([cls.__checkout[0], checkoutScript])
            checkoutDigestScript = joinScripts([cls.__checkout[1], checkoutDigestScript], "\n")
            # merge SCMs
            scms = cls.__checkout[2][:]
            scms.extend(checkoutSCMs)
            checkoutSCMs = scms
            # store result
            self.__checkout = (checkoutScript, checkoutDigestScript, checkoutSCMs)
            self.__build = (
                joinScripts([cls.__build[0], self.__build[0]]),
                joinScripts([cls.__build[1], self.__build[1]], "\n")
            )
            self.__package = (
                joinScripts([cls.__package[0], self.__package[0]]),
                joinScripts([cls.__package[1], self.__package[1]], "\n")
            )
            for (n, p) in self.__properties.items():
                p.inherit(cls.__properties[n])

        # the package step must always be valid
        if self.__package[0] is None:
            self.__package = ("", 'da39a3ee5e6b4b0d3255bfef95601890afd80709')

        # final shared value
        self.__shared = self.__shared == True

        # Either 'relocatable' was set in the recipe/class(es) or it defaults
        # to True unless a tool is defined. This was the legacy behaviour
        # before Bob 0.14. If the allRelocatable policy is enabled we always
        # default to True.
        if self.__relocatable is None:
            self.__relocatable = self.__recipeSet.getPolicy('allRelocatable') \
                or not self.__provideTools

        # check provided dependencies
        availDeps = [ d.recipe for d in self.__deps ]
        providedDeps = set()
        for pattern in self.__provideDeps:
            l = set(d for d in availDeps if fnmatch.fnmatchcase(d, pattern))
            if not l:
                raise ParseError("Unknown dependency '{}' in provideDeps".format(pattern))
            providedDeps |= l
        self.__provideDeps = providedDeps

    def getRecipeSet(self):
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
        """Returns True if the packages of this recipe are relocatable."""
        return self.__relocatable

    def prepare(self, pathFormatter, inputEnv, sandboxEnabled, inputStates, inputSandbox=None,
                inputTools=Env(), stack=[]):
        # already calculated?
        for m in self.__corePackages:
            if m.matches(inputEnv.detach(), inputTools.detach(), inputStates, inputSandbox):
                if set(stack) & m.subTreePackages:
                    raise ParseError("Recipes are cyclic")
                reusedPackage = p = Package()
                p.reconstruct(m.corePackage, pathFormatter, stack,
                    inputTools.detach(), inputSandbox)
                m.touch(inputEnv, inputTools)
                if DEBUG['pkgck']: break
                return p, m.subTreePackages
        else:
            reusedPackage = None

        # make copies because we will modify them
        sandbox = inputSandbox
        inputTools = inputTools.filter(self.__filterTools)
        inputTools.touchReset()
        tools = inputTools.derive()
        inputEnv = inputEnv.derive()
        inputEnv.touchReset()
        inputEnv.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
            "tools" : inputTools })
        varSelf = {}
        for (key, value) in self.__varSelf.items():
            varSelf[key] = inputEnv.substitute(value, "environment::"+key)
        env = inputEnv.filter(self.__filterEnv).derive(varSelf)
        if sandbox is not None:
            if not checkGlobList(sandbox.getStep().getPackage().getName(), self.__filterSandbox):
                sandbox = None
        states = { n : s.copy() for (n,s) in inputStates.items() }

        # update plugin states
        for s in states.values(): s.onEnter(env, tools, self.__properties)

        # traverse dependencies
        subTreePackages = set()
        directPackages = []
        indirectPackages = []
        results = []
        depEnv = env.derive()
        depTools = tools.derive()
        depSandbox = sandbox
        depStates = { n : s.copy() for (n,s) in states.items() }
        thisDeps = {}
        for dep in self.__deps:
            env.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
                "tools" : tools })

            if not env.evaluate(dep.condition, "dependency "+dep.recipe): continue
            r = self.__recipeSet.getRecipe(dep.recipe)
            try:
                if r.__packageName in stack:
                    raise ParseError("Recipes are cyclic (1st package in cylce)")
                depStack = stack + [r.__packageName]
                p, s = r.prepare(pathFormatter, depEnv.derive(dep.envOverride),
                                 sandboxEnabled, depStates, depSandbox, depTools,
                                 depStack)
                subTreePackages.add(p.getName())
                subTreePackages.update(s)
                p = p.getPackageStep()
            except ParseError as e:
                e.pushFrame(r.getPackageName())
                raise e

            p2 = thisDeps.setdefault(dep.recipe, p)
            if p2 is p:
                # new pacakage
                directPackages.append(p)
                # add provided dependencies at the end
                if dep.useDeps: indirectPackages.extend(p._getProvidedDeps())
                # result is only picked once
                if dep.useBuildResult: results.append(p)
            elif p.getVariantId() != p2.getVariantId():
                self.__raiseIncompatibleLocal(p)
            else:
                warnDepends.show("{} -> {}".format(self.__packageName, dep.recipe))

            # pick up various other results of package
            for (n, s) in states.items():
                if n in dep.use:
                    s.onUse(p.getPackage()._getStates()[n])
                    if dep.provideGlobal: depStates[n].onUse(p.getPackage()._getStates()[n])
            if dep.useTools:
                tools.update(p._getProvidedTools())
                if dep.provideGlobal: depTools.update(p._getProvidedTools())
            if dep.useEnv:
                env.update(p._getProvidedEnv())
                if dep.provideGlobal: depEnv.update(p._getProvidedEnv())
            if dep.useSandbox:
                sandbox = p._getProvidedSandbox()
                if dep.provideGlobal: depSandbox = p._getProvidedSandbox()

        # filter indirect packages and add to result list
        tmp = indirectPackages
        indirectPackages = []
        for p in tmp:
            p2 = thisDeps.setdefault(p.getPackage().getName(), p)
            if p2 is p:
                results.append(p)
                indirectPackages.append(p)
            elif p.getVariantId() != p2.getVariantId():
                self.__raiseIncompatibleProvided(p, p2)

        # apply private environment
        env.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
            "tools" : tools })
        varPrivate = {}
        for (key, value) in self.__varPrivate.items():
            varPrivate[key] = env.substitute(value, "privateEnvironment::"+key)
        env.update(varPrivate)

        # meta variables override existing variables but can not be substituted
        env.update(self.__metaEnv)

        # record used environment and tools
        env.touch(self.__packageVars | self.__packageVarsWeak)
        tools.touch(self.__toolDepPackage)

        # create package
        p = Package().construct(self.__packageName, stack, pathFormatter, self,
            directPackages, indirectPackages, states, inputTools.detach(), tools.detach(),
            inputSandbox, sandbox, tools.touchedKeys(), self.__metaEnv, uidGen())

        # optional checkout step
        if self.__checkout != (None, None, []):
            checkoutDigestEnv = env.prune(self.__checkoutVars)
            checkoutEnv = ( env.prune(self.__checkoutVars | self.__checkoutVarsWeak)
                if self.__checkoutVarsWeak else checkoutDigestEnv )
            srcStep = CheckoutStep().construct(p, pathFormatter, sandbox, self.__checkout,
                env, checkoutDigestEnv, checkoutEnv,
                tools.prune(self.__toolDepCheckout), self.__checkoutDeterministic)
            p._setCheckoutStep(srcStep)
        else:
            srcStep = p.getCheckoutStep() # return invalid step

        # optional build step
        if self.__build != (None, None):
            buildDigestEnv = env.prune(self.__buildVars)
            buildEnv = ( env.prune(self.__buildVars | self.__buildVarsWeak)
                if self.__buildVarsWeak else buildDigestEnv )
            buildStep = BuildStep().construct(p, pathFormatter, sandbox, self.__build,
                buildDigestEnv, buildEnv, tools.prune(self.__toolDepBuild),
                [srcStep] + results)
            p._setBuildStep(buildStep)
        else:
            buildStep = p.getBuildStep() # return invalid step

        # mandatory package step
        packageDigestEnv = env.prune(self.__packageVars)
        packageEnv = ( env.prune(self.__packageVars | self.__packageVarsWeak)
            if self.__packageVarsWeak else packageDigestEnv )
        packageStep = PackageStep().construct(p, pathFormatter, sandbox, self.__package,
            packageDigestEnv, packageEnv, tools.prune(self.__toolDepPackage),
            [buildStep], self.__shared)
        p._setPackageStep(packageStep)

        # provide environment
        provideEnv = {}
        for (key, value) in self.__provideVars.items():
            provideEnv[key] = env.substitute(value, "provideVars::"+key)
        packageStep._setProvidedEnv(provideEnv)

        # provide tools
        provideTools = { name : tool.prepare(packageStep, env)
            for (name, tool) in self.__provideTools.items() }
        packageStep._setProvidedTools(provideTools)

        # provide deps (direct and indirect deps)
        provideDeps = UniquePackageList(self.__raiseIncompatibleProvided)
        for dep in self.__deps:
            if dep.recipe not in self.__provideDeps: continue
            subDep = thisDeps.get(dep.recipe)
            if subDep is not None:
                provideDeps.append(subDep)
                for d in subDep._getProvidedDeps(): provideDeps.append(d)
        packageStep._setProvidedDeps(provideDeps.result())

        # provide Sandbox
        if self.__provideSandbox:
            packageStep._setProvidedSandbox(Sandbox().construct(packageStep,
                env, sandboxEnabled, self.__provideSandbox))

        # update plugin states
        for s in states.values(): s.onFinish(env, tools, self.__properties, p)

        if self.__shared:
            if not packageStep.isDeterministic():
                raise ParseError("Shared packages must be deterministic!")

        # remember calculated package
        if reusedPackage is None:
            self.__corePackages.append(PackageMatcher(p, inputEnv, inputTools,
                inputStates, inputSandbox, subTreePackages))
        elif packageStep._getResultId() != reusedPackage.getPackageStep()._getResultId():
            #print("original", sorted(packageStep.getEnv()))
            #print("reused", sorted(reusedPackage.getPackageStep().getEnv()))
            raise AssertionError("Wrong reusage for " + "/".join(stack))
        else:
            # drop calculated package to keep memory consumption low
            p = reusedPackage

        return p, subTreePackages

    def __raiseIncompatibleProvided(self, r, r2):
        raise ParseError("Incompatible variants of package: {} vs. {}"
            .format("/".join(r.getPackage().getStack()),
                    "/".join(r2.getPackage().getStack())),
            help=
"""This error is caused by '{PKG}' that is passed upwards via 'provideDeps' from multiple dependencies of '{CUR}'.
These dependencies constitute different variants of '{PKG}' and can therefore not be used in '{CUR}'."""
    .format(PKG=r.getPackage().getName(), CUR=self.__packageName))

    def __raiseIncompatibleLocal(self, r):
        raise ParseError("Multiple incompatible dependencies to package: {}"
            .format(r.getPackage().getName()),
            help=
"""This error is caused by naming '{PKG}' multiple times in the recipe with incompatible variants.
Every dependency must only be given once."""
    .format(PKG=r.getPackage().getName(), CUR=self.__packageName))

class PackageMatcher:
    def __init__(self, package, env, tools, states, sandbox, subTreePackages):
        self.corePackage = package._getCorePackage()
        envData = env.inspect()
        self.env = { name : envData.get(name) for name in env.touchedKeys() }
        toolsData = tools.inspect()
        self.tools = { name : (tool.getStep().getVariantId() if tool is not None else None)
            for (name, tool) in ( (n, toolsData.get(n)) for n in tools.touchedKeys() ) }
        self.states = { n : s.copy() for (n,s) in states.items() }
        self.sandbox = sandbox.getStep().getVariantId() if sandbox is not None else None
        self.subTreePackages = subTreePackages

    def matches(self, inputEnv, inputTools, inputStates, inputSandbox):
        for (name, env) in self.env.items():
            if env != inputEnv.get(name): return False
        for (name, tool) in self.tools.items():
            match = inputTools.get(name)
            match = match.getStep().getVariantId() if match is not None else None
            if tool != match: return False
        match = inputSandbox.getStep().getVariantId() \
            if inputSandbox is not None else None
        if self.sandbox != match: return False
        if self.states != inputStates: return False
        return True

    def touch(self, inputEnv, inputTools):
        inputEnv.touch(self.env.keys())
        inputTools.touch(self.tools.keys())


class ArchiveValidator:
    def __init__(self):
        self.__validTypes = schema.Schema({'backend': schema.Or('none', 'file', 'http', 'shell', 'azure')},
            ignore_extra_keys=True)
        baseArchive = {
            'backend' : str,
            schema.Optional('flags') : schema.Schema(["download", "upload",
                "nofail", "nolocal", "nojenkins"])
        }
        fileArchive = baseArchive.copy()
        fileArchive["path"] = str
        httpArchive = baseArchive.copy()
        httpArchive["url"] = str
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
        self.__validTypes.validate(data)
        return self.__backends[data['backend']].validate(data)

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
                return data
            else:
                return (data[0], data[1], [])

        raise schema.SchemaError(None, "Mount entry must be a string or a two/three items list!")

class RecipeSet:

    BUILD_DEV_SCHEMA = schema.Schema(
        {
            schema.Optional('destination') : str,
            schema.Optional('force') : bool,
            schema.Optional('no_deps') : bool,
            schema.Optional('build_mode') : schema.Or("build-only","normal", "checkout-only"),
            schema.Optional('checkout_only') : bool,
            schema.Optional('clean') : bool,
            schema.Optional('verbosity') : int,
            schema.Optional('no_logfiles') : bool,
            schema.Optional('upload') : bool,
            schema.Optional('download') : schema.Or("yes", "no", "deps", "forced", "forced-deps"),
            schema.Optional('sandbox') : bool,
            schema.Optional('clean_checkout') : bool,
            schema.Optional('always_checkout') : [str],
        })

    GRAPH_SCHEMA = schema.Schema(
        {
            schema.Optional('options') : schema.Schema({str : schema.Or(str, bool)}),
            schema.Optional('type') : schema.Or("d3", "dot"),
            schema.Optional('max_depth') : int,
        })

    USER_CONFIG_SCHEMA = schema.Schema(
        {
            schema.Optional('environment') : schema.Schema({
                schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$') : str
            }),
            schema.Optional('alias') : schema.Schema({
                schema.Regex(r'^[0-9A-Za-z_-]+$') : str
            }),
            schema.Optional('whitelist') : schema.Schema([
                schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$')
            ]),
            schema.Optional('archive') : schema.Or(
                ArchiveValidator(),
                schema.Schema( [ArchiveValidator()] )
            ),
            schema.Optional('include') : schema.Schema([str]),
            schema.Optional('scmOverrides') : [ schema.Schema({
                schema.Optional('match') : schema.Schema({ str: str }),
                schema.Optional('del') : [
                    "branch", "commit", "digestSHA1", "digestSHA256", "dir",
                    "extract", "fileName", "if", "rev", "revision", "tag"
                ],
                schema.Optional('set') : schema.Schema({ str : str }),
                schema.Optional('replace') : schema.Schema({
                    str : schema.Schema({
                        'pattern' : str,
                        'replacement' : str
                    })
                })
            }) ],
            schema.Optional('command') : schema.Schema({
                schema.Optional('dev') : BUILD_DEV_SCHEMA,
                schema.Optional('build') : BUILD_DEV_SCHEMA,
                schema.Optional('graph') : GRAPH_SCHEMA
            }),
            schema.Optional('hooks') : schema.Schema({
                schema.Optional('preBuildHook') : str,
                schema.Optional('postBuildHook') : str,
            }),
            schema.Optional('ui') : schema.Schema({
                schema.Optional('color') : schema.Or('never', 'always', 'auto')
            }),
        })

    STATIC_CONFIG_SCHEMA = schema.Schema({
        schema.Optional('bobMinimumVersion') : schema.Regex(r'^[0-9]+(\.[0-9]+){0,2}$'),
        schema.Optional('plugins') : [str],
        schema.Optional('policies') : schema.Schema(
            {
                schema.Optional('relativeIncludes') : bool,
                schema.Optional('cleanEnvironment') : bool,
                schema.Optional('tidyUrlScm') : bool,
            },
            error="Invalid policy specified! Maybe your Bob is too old?"
        )
    })

    _ignoreCmdConfig = False
    @classmethod
    def ignoreCommandCfg(cls):
        cls._ignoreCmdConfig = True

    _colorModeConfig = None
    @classmethod
    def setColorModeCfg(cls, mode):
        cls._colorModeConfig = mode

    def __init__(self):
        self.__defaultEnv = {}
        self.__aliases = {}
        self.__recipes = {}
        self.__classes = {}
        self.__whiteList = set(["TERM", "SHELL", "USER", "HOME"])
        self.__archive = { "backend" : "none" }
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
        self.__policies = {
            'relativeIncludes' : (
                "0.13",
                InfoOnce("relativeIncludes policy not set. Using project root directory as base for all includes!",
                    help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#relativeincludes for more information.")
            ),
            'cleanEnvironment' : (
                "0.13",
                InfoOnce("cleanEnvironment policy not set. Initial environment tainted by whitelisted variables!",
                    help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#cleanenvironment for more information.")
            ),
            'tidyUrlScm' : (
                "0.14",
                InfoOnce("tidyUrlScm policy not set. Updating URL SCMs in develop build mode is not entirely safe!",
                    help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#tidyurlscm for more information.")
            ),
            'allRelocatable' : (
                "0.14",
                InfoOnce("allRelocatable policy not set. Packages that define tools are not up- or downloaded.",
                    help="See http://bob-build-tool.readthedocs.io/en/latest/manual/policies.html#allrelocatable for more information.")
            ),
        }
        self.__buildHooks = {}

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

    def __loadPlugins(self, plugins):
        for p in plugins:
            name = os.path.join("plugins", p+".py")
            if not os.path.exists(name):
                raise ParseError("Plugin '"+name+"' not found!")
            mangledName = "__bob_plugin_"+p
            self.__plugins[mangledName] = self.__loadPlugin(mangledName, name)

    def __loadPlugin(self, mangledName, name):
        # dummy load file to hash state
        self.loadBinary(name)
        try:
            from importlib.machinery import SourceFileLoader
            loader = SourceFileLoader(mangledName, name)
            mod = loader.load_module()
        except SyntaxError as e:
            import traceback
            raise ParseError("Error loading plugin "+name+": "+str(e),
                             help=traceback.format_exc())
        except Exception as e:
            raise ParseError("Error loading plugin "+name+": "+str(e))

        try:
            manifest = mod.manifest
        except AttributeError:
            raise ParseError("Plugin '"+name+"' did not define 'manifest'!")
        apiVersion = manifest.get('apiVersion')
        if apiVersion is None:
            raise ParseError("Plugin '"+name+"' did not define 'apiVersion'!")
        if compareVersion(BOB_VERSION, apiVersion) < 0:
            raise ParseError("Your Bob is too old. Plugin '"+name+"' requires at least version "+apiVersion+"!")

        hooks = manifest.get('hooks', {})
        if not isinstance(hooks, dict):
            raise ParseError("Plugin '"+name+"': 'hooks' has wrong type!")
        for (hook, fun) in hooks.items():
            if not isinstance(hook, str):
                raise ParseError("Plugin '"+name+"': hook name must be a string!")
            if not callable(fun):
                raise ParseError("Plugin '"+name+"': "+hook+": hook must be callable!")
            self.__hooks.setdefault(hook, []).append(fun)

        projectGenerators = manifest.get('projectGenerators', {})
        if not isinstance(projectGenerators, dict):
            raise ParseError("Plugin '"+name+"': 'projectGenerators' has wrong type!")
        self.__projectGenerators.update(projectGenerators)

        properties = manifest.get('properties', {})
        if not isinstance(properties, dict):
            raise ParseError("Plugin '"+name+"': 'properties' has wrong type!")
        for (i,j) in properties.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': property name must be a string!")
            if not issubclass(j, PluginProperty):
                raise ParseError("Plugin '"+name+"': property '" +i+"' has wrong type!")
            if i in self.__properties:
                raise ParseError("Plugin '"+name+"': property '" +i+"' already defined by other plugin!")
        self.__properties.update(properties)

        states = manifest.get('state', {})
        if not isinstance(states, dict):
            raise ParseError("Plugin '"+name+"': 'states' has wrong type!")
        for (i,j) in states.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': state tracker name must be a string!")
            if i in ["environment", "tools", "result", "deps", "sandbox"]:
                raise ParseError("Plugin '"+name+"': state tracker has reserved name!")
            if not issubclass(j, PluginState):
                raise ParseError("Plugin '"+name+"': state tracker '" +i+"' has wrong type!")
            if i in self.__states:
                raise ParseError("Plugin '"+name+"': state tracker '" +i+"' already defined by other plugin!")
        self.__states.update(states)

        funs = manifest.get('stringFunctions', {})
        if not isinstance(funs, dict):
            raise ParseError("Plugin '"+name+"': 'stringFunctions' has wrong type!")
        for (i,j) in funs.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': string function name must be a string!")
            if i in self.__stringFunctions:
                raise ParseError("Plugin '"+name+"': string function '" +i+"' already defined by other plugin!")
        self.__stringFunctions.update(funs)

        return mod

    def defineHook(self, name, value):
        self.__hooks[name] = [value]

    def setConfigFiles(self, configFiles):
        self.__configFiles = configFiles

    def getCommandConfig(self):
        return self.__commandConfig

    def getHook(self, name):
        return self.__hooks[name][-1]

    def getHookStack(self, name):
        return self.__hooks.get(name, [])

    def getProjectGenerators(self):
        return self.__projectGenerators

    def envWhiteList(self):
        return set(self.__whiteList)

    def archiveSpec(self):
        return self.__archive

    def defaultEnv(self):
        return self.__defaultEnv

    def scmOverrides(self):
        return self.__scmOverrides

    def getScmAudit(self):
        try:
            ret = self.__recipeScmAudit
        except AttributeError:
            ret = self.__recipeScmAudit = auditFromDir(".")
        return ret

    def getScmStatus(self):
        audit = self.getScmAudit()
        if audit is None:
            return "unknown"
        else:
            return audit.getStatusLine()

    def getBuildHook(self, name):
        return self.__buildHooks.get(name)

    def loadBinary(self, path):
        return self.__cache.loadBinary(path)

    def loadYaml(self, path, schema, default={}):
        if os.path.exists(path):
            return self.__cache.loadYaml(path, schema, default)
        else:
            return default

    def parse(self):
        if not os.path.isdir("recipes"):
            raise ParseError("No recipes directory found.")
        self.__cache.open()
        try:
            self.__parse()

            # config files overrule everything else
            for c in self.__configFiles:
                c = str(c) + ".yaml"
                if not os.path.isfile(c):
                    raise ParseError("Config file {} does not exist!".format(c))
                self.__parseUserConfig(c)
        finally:
            self.__cache.close()

    def __parse(self):
        config = self.loadYaml("config.yaml", RecipeSet.STATIC_CONFIG_SCHEMA)
        minVer = config.get("bobMinimumVersion", "0.1")
        if compareVersion(BOB_VERSION, minVer) < 0:
            raise ParseError("Your Bob is too old. At least version "+minVer+" is required!")
        self.__loadPlugins(config.get("plugins", []))
        self.__createSchemas()

        # determine policies
        self.__policies = { name : (True if compareVersion(ver, minVer) <= 0 else None, warn)
            for (name, (ver, warn)) in self.__policies.items() }
        for (name, behaviour) in config.get("policies", {}).items():
            self.__policies[name] = (behaviour, None)

        # user config(s)
        if not DEBUG['ngd']:
            self.__parseUserConfig("/etc/bobdefault.yaml", True)
            self.__parseUserConfig(os.path.join(os.environ.get('XDG_CONFIG_HOME',
                os.path.join(os.path.expanduser("~"), '.config')), 'bob', 'default.yaml'), True)
        self.__parseUserConfig("default.yaml")

        # color mode provided in cmd line takes precedence
        # (if no color mode provided by user, default one will be used)
        setColorMode(self._colorModeConfig or self.__uiConfig.get('color', 'auto'))

        # finally parse recipes
        for root, dirnames, filenames in os.walk('classes'):
            for path in fnmatch.filter(filenames, "*.yaml"):
                try:
                    [r] = Recipe.loadFromFile(self, os.path.join(root, path),
                        self.__properties, self.__classSchema, False)
                    self.__addClass(r)
                except ParseError as e:
                    e.pushFrame(path)
                    raise

        for root, dirnames, filenames in os.walk('recipes'):
            for path in fnmatch.filter(filenames, "*.yaml"):
                try:
                    for r in Recipe.loadFromFile(self,  os.path.join(root, path),
                                                 self.__properties, self.__recipeSchema,
                                                 True):
                        self.__addRecipe(r)
                except ParseError as e:
                    e.pushFrame(path)
                    raise

        # resolve recipes and their classes
        rootRecipes = []
        for recipe in self.__recipes.values():
            try:
                recipe.resolveClasses()
            except ParseError as e:
                e.pushFrame(recipe.getPackageName())
                raise
            if recipe.isRoot():
                rootRecipes.append(recipe.getPackageName())

        # create virtual root package
        self.__rootRecipe = Recipe.createVirtualRoot(self, sorted(rootRecipes), self.__properties)
        self.__addRecipe(self.__rootRecipe)

    def __parseUserConfig(self, fileName, relativeIncludes=None):
        if relativeIncludes is None:
            relativeIncludes = self.getPolicy("relativeIncludes")
        cfg = self.loadYaml(fileName, RecipeSet.USER_CONFIG_SCHEMA)
        self.__defaultEnv.update(cfg.get("environment", {}))
        self.__whiteList |= set(cfg.get("whitelist", []))
        if "archive" in cfg:
            self.__archive = cfg["archive"]
        self.__scmOverrides.extend([ ScmOverride(o) for o in cfg.get("scmOverrides", []) ])
        self.__aliases.update(cfg.get("alias", {}))
        if not self._ignoreCmdConfig:
            self.__commandConfig = updateDicRecursive(self.__commandConfig, cfg.get("command", {}))
        self.__buildHooks.update(cfg.get("hooks", {}))
        self.__uiConfig = updateDicRecursive(self.__uiConfig, cfg.get("ui", {}))
        for p in cfg.get("include", []):
            p = os.path.join(os.path.dirname(fileName), p) if relativeIncludes else p
            self.__parseUserConfig(p + ".yaml", relativeIncludes)

    def __createSchemas(self):
        varNameSchema = schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$')
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
            schema.Optional('environment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('if') : str
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
            schema.Optional('buildScript') : str,
            schema.Optional('packageScript') : str,
            schema.Optional('checkoutTools') : [ toolNameSchema ],
            schema.Optional('buildTools') : [ toolNameSchema ],
            schema.Optional('packageTools') : [ toolNameSchema ],
            schema.Optional('checkoutVars') : [ varNameSchema ],
            schema.Optional('buildVars') : [ varNameSchema ],
            schema.Optional('packageVars') : [ varNameSchema ],
            schema.Optional('checkoutVarsWeak') : [ varNameSchema ],
            schema.Optional('buildVarsWeak') : [ varNameSchema ],
            schema.Optional('packageVarsWeak') : [ varNameSchema ],
            schema.Optional('checkoutDeterministic') : bool,
            schema.Optional('checkoutSCM') : ScmValidator({
                'git' : GitScm.SCHEMA,
                'svn' : SvnScm.SCHEMA,
                'cvs' : CvsScm.SCHEMA,
                'url' : UrlScm.SCHEMA
            }),
            schema.Optional('depends') : dependsClause,
            schema.Optional('environment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('filter') : schema.Schema({
                schema.Optional('environment') : [ varFilterSchema ],
                schema.Optional('tools') : [ recipeFilterSchema ],
                schema.Optional('sandbox') : [ recipeFilterSchema ]
            }),
            schema.Optional('inherit') : [str],
            schema.Optional('privateEnvironment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('metaEnvironment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('provideDeps') : [str],
            schema.Optional('provideTools') : schema.Schema({
                str: schema.Or(
                    str,
                    schema.Schema({
                        'path' : str,
                        schema.Optional('libs') : [str]
                    })
                )
            }),
            schema.Optional('provideVars') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('provideSandbox') : schema.Schema({
                'paths' : [str],
                schema.Optional('mount') : schema.Schema([ MountValidator() ],
                    error="provideSandbox: invalid 'mount' property")
            }),
            schema.Optional('root') : bool,
            schema.Optional('shared') : bool,
            schema.Optional('relocatable') : bool,
        }
        for (name, prop) in self.__properties.items():
            classSchemaSpec[schema.Optional(name)] = schema.Schema(prop.validate,
                error="property '"+name+"' has an invalid type")

        self.__classSchema = schema.Schema(classSchemaSpec)

        recipeSchemaSpec = classSchemaSpec.copy()
        recipeSchemaSpec[schema.Optional('multiPackage')] = schema.Schema({
            MULTIPACKAGE_NAME_SCHEMA : recipeSchemaSpec
        })
        self.__recipeSchema = schema.Schema(recipeSchemaSpec)

    def getRecipe(self, packageName):
        if packageName not in self.__recipes:
            raise ParseError("Package {} requested but not found.".format(packageName))
        return self.__recipes[packageName]

    def getClass(self, className):
        if className not in self.__classes:
            raise ParseError("Class {} requested but not found.".format(className))
        return self.__classes[className]

    def __getEnvWithCacheKey(self, envOverrides, sandboxEnabled):
        # calculate start environment
        if self.getPolicy("cleanEnvironment"):
            osEnv = Env(os.environ)
            osEnv.setFuns(self.__stringFunctions)
            env = Env({ k : osEnv.substitute(v, k) for (k, v) in
                self.__defaultEnv.items() })
        else:
            env = Env(os.environ).prune(self.__whiteList)
            env.update(self.__defaultEnv)
        env.setFuns(self.__stringFunctions)
        env.update(envOverrides)

        # calculate cache key for persisted packages
        h = hashlib.sha1()
        h.update(BOB_INPUT_HASH)
        h.update(self.__cache.getDigest())
        h.update(struct.pack("<I", len(env)))
        for (key, val) in sorted(env.inspect().items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        h.update(b'\x01' if sandboxEnabled else b'\x00')
        return (env, h.digest())

    def __generatePackages(self, nameFormatter, env, cacheKey, sandboxEnabled):
        # use separate caches with and without sandbox
        if sandboxEnabled:
            cacheName = ".bob-packages-sb.pickle"
        else:
            cacheName = ".bob-packages.pickle"

        # try to load the persisted packages
        states = { n:s() for (n,s) in self.__states.items() }
        rootPkg = Package()
        rootPkg.construct("<root>", [], nameFormatter, None, [], [], states,
            {}, {}, None, None, [], {}, -1)
        try:
            with open(cacheName, "rb") as f:
                persistedCacheKey = f.read(len(cacheKey))
                if cacheKey == persistedCacheKey:
                    tmp = PackageUnpickler(f, self.getRecipe, self.__plugins,
                                           nameFormatter).load()
                    return tmp.toStep(nameFormatter, rootPkg).getPackage()
        except (EOFError, OSError, pickle.UnpicklingError):
            pass

        # not cached -> calculate packages
        result = self.__rootRecipe.prepare(nameFormatter, env, sandboxEnabled,
                                           states)[0]

        # save package tree for next invocation
        tmp = CoreStepRef(rootPkg, result.getPackageStep())
        try:
            newCacheName = cacheName + ".new"
            with open(newCacheName, "wb") as f:
                f.write(cacheKey)
                PackagePickler(f, nameFormatter).dump(tmp)
            os.replace(newCacheName, cacheName)
        except OSError as e:
            print("Error saving internal state:", str(e), file=sys.stderr)

        return result

    def generatePackages(self, nameFormatter, envOverrides={}, sandboxEnabled=False):
        (env, cacheKey) = self.__getEnvWithCacheKey(envOverrides, sandboxEnabled)
        return PackageSet(cacheKey, self.__aliases, self.__stringFunctions,
            lambda: self.__generatePackages(nameFormatter, env, cacheKey, sandboxEnabled))

    def getPolicy(self, name, location=None):
        (policy, warning) = self.__policies[name]
        if policy is None:
            warning.show(location)
        return policy


class YamlCache:

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

    def loadYaml(self, name, yamlSchema, default):
        try:
            bs = binStat(name)
            if self.__hot:
                self.__cur.execute("SELECT digest, data FROM yaml WHERE name=? AND stat=?",
                                    (name, bs))
                cached = self.__cur.fetchone()
                if cached is not None:
                    self.__files[name] = cached[0]
                    return pickle.loads(cached[1])

            with open(name, "r") as f:
                try:
                    rawData = f.read()
                    data = yaml.safe_load(rawData)
                    digest = hashlib.sha1(rawData.encode('utf8')).digest()
                except Exception as e:
                    raise ParseError("Error while parsing {}: {}".format(name, str(e)))

            if data is None: data = default
            try:
                data = yamlSchema.validate(data)
            except schema.SchemaError as e:
                raise ParseError("Error while validating {}: {}".format(name, str(e)))

            self.__files[name] = digest
            self.__cur.execute("INSERT OR REPLACE INTO yaml VALUES (?, ?, ?, ?)",
                (name, bs, digest, pickle.dumps(data)))
        except sqlite3.Error as e:
            raise ParseError("Cannot access cache: " + str(e),
                help="You probably executed Bob concurrently in the same workspace. Try again later.")
        except OSError as e:
            raise ParseError("Error loading yaml file: " + str(e))

        return data

    def loadBinary(self, name):
        with open(name, "rb") as f:
            result = f.read()
        self.__files[name] = hashlib.sha1(result).digest()
        return result


class PackagePickler(pickle.Pickler):
    def __init__(self, file, pathFormatter):
        super().__init__(file, -1, fix_imports=False)
        self.__pathFormatter = pathFormatter

    def persistent_id(self, obj):
        if obj is self.__pathFormatter:
            return ("pathfmt", None)
        elif isinstance(obj, Recipe):
            return ("recipe", obj.getPackageName())
        else:
            return None

class PackageUnpickler(pickle.Unpickler):
    def __init__(self, file, recipeGetter, plugins, pathFormatter):
        super().__init__(file)
        self.__recipeGetter = recipeGetter
        self.__plugins = plugins
        self.__pathFormatter = pathFormatter

    def persistent_load(self, pid):
        (tag, key) = pid
        if tag == "pathfmt":
            return self.__pathFormatter
        elif tag == "recipe":
            return self.__recipeGetter(key)
        else:
            raise pickle.UnpicklingError("unsupported object")

    def find_class(self, module, name):
        if module.startswith("__bob_plugin_"):
            return getattr(self.__plugins[module], name)
        else:
            return super().find_class(module, name)

