# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tools, sandboxes and interpreters."""

from ..languages import ScriptLanguage
from .common import fetchFingerprintScripts
import hashlib
import struct


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
    tools, interpreters and the sandbox. Each package has a set of tools,
    interpreters and a sandbox defined as their input. While iterating of the
    dependencies new tools, interpreters or a new sandbox can be picked up,
    creating a "diff" to the input tools/sandbox of the package. When later
    re-creating the real Package/Step classes these diffs must be applied on
    refDeref() so that the reference destination gets the correct ambient
    tools/sandbox again.

    diffTools: A dict. If the value of a tool is "None" the tool is deleted. A
    string will copy the tool from an existing "inputTools". Otherwise the
    value is expected to the another CoreRef that needs to be dereferenced too.
    """

    __slots__ = ('__destination', '__stackAdd', '__diffTools', '__diffSandbox', '__diffInterpreters')

    def __init__(self, destination, stackAdd=[], diffTools={}, diffSandbox=..., diffInterpreters={}):
        self.__destination = destination
        self.__stackAdd = stackAdd
        self.__diffTools = diffTools
        self.__diffSandbox = diffSandbox
        self.__diffInterpreters = diffInterpreters

    def refGetDestination(self):
        return self.__destination.refGetDestination()

    def refGetStack(self):
        return self.__stackAdd + self.__destination.refGetStack()

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
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
                        cache[tool] = coreTool = tool.refDeref(stack, inputTools, inputSandbox,
                                                               inputInterpreters, pathsConfig, cache)
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
                    inputInterpreters, pathsConfig, cache)
            cache[self.__diffSandbox] = sandbox

        if self.__diffInterpreters:
            interpreters = inputInterpreters.copy()
            for name, interp in self.__diffInterpreters.items():
                if interp is None:
                    del interpreters[name]
                else:
                    coreInterp = cache.get(interp)
                    if coreInterp is None:
                        cache[interp] = coreInterp = interp.refDeref(stack, inputTools, inputSandbox,
                                                                     inputInterpreters, pathsConfig, cache)
                    interpreters[name] = coreInterp
        else:
            interpreters = inputInterpreters

        return self.__destination.refDeref(stack + self.__stackAdd, tools, sandbox, interpreters, pathsConfig)

class CoreItem:
    __slots__ = []

    def refGetDestination(self):
        return self

    def refGetStack(self):
        return []

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        raise NotImplementedError

class AbstractTool:
    __slots__ = ("path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintIf", "fingerprintVars", "dependTools",
        "dependToolsWeak")

    def __init__(self, spec):
        if isinstance(spec, str):
            self.path = spec
            self.libs = []
            self.netAccess = False
            self.environment = {}
            self.fingerprintScript = { lang : "" for lang in ScriptLanguage }
            self.fingerprintIf = False
            self.fingerprintVars = set()
            self.dependTools = []
            self.dependToolsWeak = []
        else:
            self.path = spec['path']
            self.libs = spec.get('libs', [])
            self.netAccess = spec.get('netAccess', False)
            self.environment = spec.get('environment', {})
            self.fingerprintScript = fetchFingerprintScripts(spec)
            self.fingerprintIf = spec.get("fingerprintIf")
            self.fingerprintVars = set(spec.get("fingerprintVars", []))
            self.dependTools = spec.get("dependTools", [])
            self.dependToolsWeak = spec.get("dependToolsWeak", [])

    def prepare(self, coreStepRef, env):
        """Create concrete tool for given step."""
        path = env.substitute(self.path, "provideTools::path")
        libs = [ env.substitute(l, "provideTools::libs") for l in self.libs ]
        environment = env.substituteCondDict(self.environment,
                                             "provideTools::environment")

        dependTools = set(name for (name, cond) in self.dependTools
                               if env.evaluate(cond, "provideTools::dependTools"))
        dependToolsWeak = set(name for (name, cond) in self.dependToolsWeak
                                   if env.evaluate(cond, "provideTools::dependToolsWeak"))

        return CoreTool(coreStepRef, path, libs, self.netAccess, environment,
                        self.fingerprintScript, self.fingerprintIf,
                        self.fingerprintVars, dependTools, dependToolsWeak)

class CoreTool(CoreItem):
    __slots__ = ("coreStep", "path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintIf", "fingerprintVars", "resultId",
        "dependTools", "dependToolsWeak")

    def __init__(self, coreStep, path, libs, netAccess, environment,
                 fingerprintScript, fingerprintIf, fingerprintVars, dependTools,
                 dependToolsWeak):
        self.coreStep = coreStep
        self.path = path
        self.libs = libs
        self.netAccess = netAccess
        self.environment = environment
        self.fingerprintScript = fingerprintScript
        self.fingerprintIf = fingerprintIf
        self.fingerprintVars = fingerprintVars
        self.dependTools = dependTools
        self.dependToolsWeak = dependToolsWeak

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
        h.update(struct.pack("<II", len(dependTools), len(dependToolsWeak)))
        for key in sorted(dependTools):
            h.update(key.encode('utf8'))
        for key in sorted(dependToolsWeak):
            h.update(key.encode('utf8'))
        self.resultId = h.digest()

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        step = self.coreStep.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
        return Tool(step, self.path, self.libs, self.netAccess, self.environment,
                    self.fingerprintScript, self.fingerprintVars, self.dependTools,
                    self.dependToolsWeak)

class Tool:
    """Representation of a tool.

    A tool is made of the result of a package, a relative path into this result
    and some optional relative library paths.
    """

    __slots__ = ("step", "path", "libs", "netAccess", "environment",
        "fingerprintScript", "fingerprintVars", "dependTools", "dependToolsWeak")

    def __init__(self, step, path, libs, netAccess, environment, fingerprintScript,
                 fingerprintVars, dependTools, dependToolsWeak):
        self.step = step
        self.path = path
        self.libs = libs
        self.netAccess = netAccess
        self.environment = environment
        self.fingerprintScript = fingerprintScript
        self.fingerprintVars = fingerprintVars
        self.dependTools = dependTools
        self.dependToolsWeak = dependToolsWeak

    def __repr__(self):
        return "Tool({}, {}, {})".format(repr(self.step), self.path, self.libs)

    def __eq__(self, other):
        return isinstance(other, Tool) and (self.step == other.step) and (self.path == other.path) and \
            (self.libs == other.libs) and (self.netAccess == other.netAccess) and \
            (self.environment == other.environment) and \
            (self.dependTools == other.dependTools) and \
            (self.dependToolsWeak == other.dependToolsWeak)

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

    def getDependTools(self):
        """Get tool dependencies of this tool.

        :return: Set[str]
        """
        return self.dependTools

    def getDependToolsWeak(self):
        """Get weak tool dependencies of this tool.

        :return: Set[str]
        """
        return self.dependToolsWeak

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

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        step = self.coreStep.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
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

class CoreInterpreter(CoreItem):
    __slots__ = ("coreStep", "path", "resultId")

    def __init__(self, coreStep, path):
        self.coreStep = coreStep
        self.path = path
        h = hashlib.sha1()
        h.update(coreStep.variantId)
        h.update(struct.pack("<I", len(path)))
        h.update(path.encode("utf8"))
        self.resultId = h.digest()

    def refDeref(self, stack, inputTools, inputSandbox, inputInterpreters, pathsConfig, cache=None):
        step = self.coreStep.refDeref(stack, inputTools, inputSandbox, inputInterpreters, pathsConfig)
        return Interpreter(step, self.path)

class Interpreter:
    """Representation of a script interpreter.

    An interpreter is the executable used to run the scripts of a step. It is
    made of the result of a package and a relative path into this result.
    """

    __slots__ = ("step", "path")

    def __init__(self, step, path):
        self.step = step
        self.path = path

    def getStep(self):
        """Return package step that produces the result holding the interpreter."""
        return self.step

    def getPath(self):
        """Get relative path into the result."""
        return self.path
