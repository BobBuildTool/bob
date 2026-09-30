# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The set of all recipes, classes, layers and the user configuration."""

from .. import BOB_VERSION, BOB_INPUT_HASH, DEBUG
from ..errors import ParseError, BobError
from ..languages import ScriptLanguage
from ..pathspec import PackageSet
from ..scm import CvsScm, GitScm, ImportScm, SvnScm, UrlScm, ScmOverride, \
    auditFromDir, auditFromProperties
from ..state import BobState
from ..stringparser import checkGlobList, Env, DEFAULT_STRING_FUNS, \
    IfExpression, EXTRA_STRING_FUNS
from ..tty import InfoOnce, Warn, setColorMode
from ..utils import compareVersion, binStat, updateDicRecursive, \
    getPlatformString, replacePath, getPlatformEnvWhiteList
from .cache import PackagePickler, PackageUnpickler, YamlCache
from .checkout import CheckoutAssert
from .common import maybeGlob
from .core import PathsConfig
from .plugins import BuiltinSetting, PluginSetting, SentinelSetting, \
    isPluginPropertyClass, isPluginStateClass
from .recipe import AliasPackage, MULTIPACKAGE_NAME_SCHEMA, Recipe
from .validators import ArchiveValidator, AuditFilesValidator, MountValidator, \
    ScmValidator, ToolValidator, VarDefineValidator, wrapValidator, StrDictValidator
import hashlib
import fnmatch
import os, os.path
import re
import schema
import struct
import sys


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

def isUpperCaseName(name):
    return isinstance(name, str) and not name[:1].islower()

class LayersConfig:
    def __init__(self):
        self.__platform = getPlatformString()
        self.__whiteList = getPlatformEnvWhiteList(self.__platform)
        self.__scmOverrides = []

    def derive(self, config):
        """Create a new LayersConfig by adding the passed config to this one."""
        ret = LayersConfig()
        ret.__whiteList = set(self.__whiteList)
        ret.__scmOverrides = self.__scmOverrides[:]

        ret.__whiteList.update([c.upper() if self.__platform == "win32" else c
            for c in config.get("layersWhitelist", []) ])
        ret.__scmOverrides[0:0] = [ ScmOverride(o) for o in config.get("layersScmOverrides", []) ]

        return ret

    def envWhiteList(self):
        return self.__whiteList

    def scmOverrides(self):
        return self.__scmOverrides

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

    PLUGIN_MANIFEST_SCHEMA = schema.Schema({
        'apiVersion' : str,
        schema.Optional('hooks') : schema.Schema({
            str : schema.Schema(wrapValidator(callable, "hook must be callable!"))
        }),
        schema.Optional('projectGenerators') : schema.Schema({
            str : schema.Or(
                schema.Schema(wrapValidator(callable, "generator must be callable!")),
                {
                    'func' : schema.Schema(wrapValidator(callable, "generator 'func' must be callable!")),
                    schema.Optional('query') : bool,
                })
        }),
        schema.Optional('commands') : schema.Schema({
            str : schema.Or(
                schema.Schema(wrapValidator(callable, "command must be callable!")),
                {
                    'func' : schema.Schema(wrapValidator(callable, "command 'func' must be callable!")),
                    schema.Optional('help') : str,
                })
        }),
        schema.Optional('properties') : schema.Schema({
            schema.Schema(wrapValidator(isUpperCaseName,
                "property name must be a string and must not start lower case!")) :
                schema.Schema(wrapValidator(isPluginPropertyClass, "property has wrong type!"))
        }),
        schema.Optional('state') : schema.Schema({
            schema.Schema(wrapValidator(isUpperCaseName,
                "state tracker name must be a string and must not start lower case!")) :
                schema.Schema(wrapValidator(isPluginStateClass, "state tracker has wrong type!"))
        }),
        schema.Optional('stringFunctions') : schema.Schema({
            str : schema.Schema(wrapValidator(callable, "string function must be callable!"))
        }),
        schema.Optional('settings') : schema.Schema({
            schema.Schema(wrapValidator(isUpperCaseName,
                "settings name must be a string and must not start lower case!")) :
                PluginSetting
        }),
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
                    'replacement' : str,
                    schema.Optional('substituteReplace') : bool,
                })
            })
        }]),
        schema.Optional('layersInclude') : [str],
        schema.Optional('layersRequire') : [str],
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
        schema.Optional('inheritAppend') : [str],
        schema.Optional('inheritPrepend') : [str],
        schema.Optional('layers') : [LayerValidator()],
        schema.Optional('scriptLanguage',
                        default=ScriptLanguage.BASH) : schema.And(schema.Or("bash", "PowerShell", "python"),
                                                                  schema.Use(ScriptLanguage)),
        schema.Optional('windowsPowerShellExecutable') : str,
    }

    MIRRORS_SCHEMA = ScmValidator({
        'svn' : SvnScm.MIRRORS_SCHEMA,
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
        self.__commands = {}
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
        self.__inheritAppend = []
        self.__inheritPrepend = []
        self.__windowsPowerShellExecutable = "powershell"

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
                            'replacement' : str,
                            schema.Optional('substituteReplace') : bool,
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
        existing = self.__recipes.get(name)
        if existing is not None:
            source = existing.getPrimarySource()
            raise ParseError(f"Package {name} already defined by {source}!")
        self.__recipes[name] = recipe

    def __addClass(self, recipe):
        name = recipe.getPackageName()
        existing = self.__classes.get(name)
        if existing is not None:
            source = existing.getPrimarySource()
            raise ParseError(f"Class {name} already defined by {source}!")
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

        # Check the API version first so that a plugin requiring a newer Bob
        # gets the proper "too old" error instead of a confusing schema
        # error about manifest keys that this Bob version does not know yet.
        apiVersion = manifest.get('apiVersion') if isinstance(manifest, dict) else None
        if apiVersion is None:
            raise ParseError("Plugin '"+fileName+"' did not define 'apiVersion'!")
        if compareVersion(BOB_VERSION, apiVersion) < 0:
            raise ParseError("Your Bob is too old. Plugin '"+fileName+"' requires at least version "+apiVersion+"!")
        toolsAbiBreak = compareVersion(apiVersion, "0.15") < 0

        try:
            manifest = self.PLUGIN_MANIFEST_SCHEMA.validate(manifest)
        except schema.SchemaError as e:
            raise ParseError("Plugin '"+fileName+"': "+str(e))

        hooks = manifest.get('hooks', {})
        for (hook, fun) in hooks.items():
            self.__hooks.setdefault(hook, []).append((fun, apiVersion))

        projectGenerators = manifest.get('projectGenerators', {})
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

        commands = manifest.get('commands', {})
        if commands and compareVersion(apiVersion, "1.2.1.dev1") < 0:
            raise ParseError("Plugin '"+fileName+"': 'commands' requires at least apiVersion 1.2.1.dev1!")
        for (i, j) in commands.items():
            entry = j if isinstance(j, dict) else {'func' : j}
            if i in self.__commands:
                raise ParseError("Plugin '"+fileName+"': command '"+i+"' already defined by other plugin!")
            self.__commands[i] = entry

        properties = manifest.get('properties', {})
        if properties:
            self.__pluginPropDeps += pluginStat
        for i in properties:
            if i in self.__properties:
                raise ParseError("Plugin '"+fileName+"': property '" +i+"' already defined by other plugin!")
        self.__properties.update(properties)

        states = manifest.get('state', {})
        for i in states:
            if i in self.__states:
                raise ParseError("Plugin '"+fileName+"': state tracker '" +i+"' already defined by other plugin!")
        if states and toolsAbiBreak:
            raise ParseError("Plugin '"+fileName+"': state requires at least apiVersion 0.15!")
        self.__states.update(states)

        funs = manifest.get('stringFunctions', {})
        for i in funs:
            if i in self.__stringFunctions:
                raise ParseError("Plugin '"+fileName+"': string function '" +i+"' already defined by other plugin!")
        if funs and toolsAbiBreak:
            raise ParseError("Plugin '"+fileName+"': stringFunctions requires at least apiVersion 0.15!")
        self.__stringFunctions.update(funs)

        settings = manifest.get('settings', {})
        if settings:
            self.__pluginSettingsDeps += pluginStat
        for i in settings:
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

    def getCommands(self):
        return self.__commands

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

    def parse(self, envOverrides={}, platform=getPlatformString(), recipesRoot="", command=None):
        try:
            self.__parseConfigs(platform, recipesRoot, command, True)
            self.__parseRecipes(envOverrides)
        finally:
            self.__cache.close()

    def parseConfigs(self, platform=getPlatformString(), recipesRoot="", command=None,
                     requireRecipes=True):
        try:
            self.__parseConfigs(platform, recipesRoot, command, requireRecipes)
        finally:
            self.__cache.close()

    def __parseConfigs(self, platform, recipesRoot, command, requireRecipes):
        if platform not in ('cygwin', 'darwin', 'linux', 'msys', 'win32'):
            raise ParseError("Invalid platform: " + platform)
        self.__platform = platform
        self.__layers = {}
        self.__whiteList = getPlatformEnvWhiteList(platform)
        self.__pluginPropDeps = b''
        self.__pluginSettingsDeps = b''
        self.__createSchemas()

        # Find actual project root
        if not recipesRoot and os.path.isfile(".bob-project"):
            try:
                with open(".bob-project") as f:
                    recipesRoot = f.read()
            except OSError as e:
                raise ParseError("Broken project link: " + str(e))
        recipesDir = os.path.join(recipesRoot, "recipes")
        if not os.path.isdir(recipesDir):
            if requireRecipes:
                raise ParseError("No recipes directory found in " + recipesDir)
            else:
                return
        self.__projectRoot = recipesRoot or os.getcwd()

        # We know we are in a project directory. Open or create the Yaml cache.
        self.__cache.open()

        # global user config(s)
        if not DEBUG['ngd']:
            self.__parseUserConfig("/etc/bobdefault.yaml")
            self.__parseUserConfig(os.path.join(os.environ.get('XDG_CONFIG_HOME',
                os.path.join(os.path.expanduser("~"), '.config')), 'bob', 'default.yaml'))

        # Begin with root layer
        allLayerSpecs = { "" : LayerSpec("") }
        self.__allLayers = self.__parseLayer("", allLayerSpecs, "9999", recipesRoot, None)

        # If a specific command is requested, verify that it's available.
        if command is not None and command not in self.__commands:
            raise BobError(f"{command}: unknown command! Use 'bob -h' for help.",
                             returncode=2)

        # Add string functions added after 1.0. We did not reserve a namespace
        # and we better not break existing recipes.
        collisions = set(self.__stringFunctions.keys()) & set(EXTRA_STRING_FUNS.keys())
        if collisions:
            for k, v in EXTRA_STRING_FUNS.items():
                if k not in collisions:
                    self.__stringFunctions[k] = v
            collisions = ", ".join(sorted(collisions))
            Warn(f"Bob internal string functions shadowed by plugins: {collisions}").warn()
        else:
            self.__stringFunctions.update(EXTRA_STRING_FUNS)

        # Out-of-tree builds may have a dedicated default.yaml
        if recipesRoot:
            self.__parseUserConfig("default.yaml")

        # config files overrule everything else
        for c in self.__configFiles:
            c = str(c) + ".yaml"
            if not os.path.isfile(c):
                raise ParseError("Config file {} does not exist!".format(c))
            self.__parseUserConfig(c)

    def __parseRecipes(self, envOverrides):
        # Parse all recipes and classes of all layers. Need to be done last
        # because only by now we have loaded all plugins.
        for layer, rootDir, scriptLanguage in self.__allLayers:
            classesDir = os.path.join(rootDir, 'classes')
            for root, dirnames, filenames in os.walk(classesDir):
                for path in fnmatch.filter(filenames, "[!.]*.yaml"):
                    try:
                        [r] = Recipe.loadFromFile(self, layer, classesDir,
                            os.path.relpath(os.path.join(root, path), classesDir),
                            self.__properties, self.__classSchema, False)
                        self.__addClass(r)
                    except ParseError as e:
                        e.setPath(os.path.join(root, path))
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
                        e.setPath(os.path.join(root, path))
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
                        e.setPath(os.path.join(root, path))
                        raise

        # calculate start environment
        osEnv = Env(os.environ)
        osEnv.setFuns(self.__stringFunctions)
        osEnv.setFunArgs({ "recipeSet" : self })
        env = Env({ k : osEnv.substitute(v, k) for (k, v) in
            self.__defaultEnv.items() })
        env.setFuns(self.__stringFunctions)
        env.setFunArgs({ "recipeSet" : self })
        env.update(envOverrides)
        env["BOB_HOST_PLATFORM"] = self.__platform
        self.__rootEnv = env

        # resolve recipes and their classes
        rootRecipes = []
        states = { n:s() for (n,s) in self.__states.items() }
        for recipe in self.__recipes.values():
            try:
                recipeEnv = env.copy()
                recipeEnv.setFunArgs({ "recipe" : recipe, "recipeSet" : self,
                    "sandbox" : False, "__tools" : {}, "states" : states })
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
    def loadConfigYaml(cls, loadYaml, rootDir, upperLayerConfig = LayersConfig()):
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

        layerConfig = cls.__parseLayersConfigYaml(ret, loadYaml, configYaml, upperLayerConfig)

        return (ret, layerConfig)

    @classmethod
    def loadLayersConfigYaml(cls, loadYaml, fileName, upperLayerConfig):
        configSchema = (schema.Schema(RecipeSet.STATIC_CONFIG_LAYER_SPEC), b'')
        cfg = loadYaml(fileName, configSchema)
        return cls.__parseLayersConfigYaml(cfg, loadYaml, fileName, upperLayerConfig)

    @classmethod
    def __parseLayersConfigYaml(cls, cfg, loadYaml, fileName, upperLayerConfig):
        ret = upperLayerConfig

        for p in reversed(cfg.get("layersInclude", [])):
            p = os.path.join(os.path.dirname(fileName), p) + ".yaml"
            ret = cls.loadLayersConfigYaml(loadYaml, p, ret)

        for p in reversed(cfg.get("layersRequire", [])):
            p = os.path.join(os.path.dirname(fileName), p) + ".yaml"
            if not os.path.isfile(p):
                raise ParseError(f"Include file '{p}' (required by '{fileName}') does not exist!")
            ret = cls.loadLayersConfigYaml(loadYaml, p, ret)

        return ret.derive(cfg)

    @classmethod
    def calculatePolicies(cls, config):
        minVer = config.get("bobMinimumVersion", "0.16")
        ret = { name : (True if compareVersion(ver, minVer) <= 0 else None, warn)
            for (name, (ver, warn)) in cls.POLICIES.items() }
        for (name, behaviour) in config.get("policies", {}).items():
            ret[name] = (behaviour, None)
        return ret

    def __parseLayer(self, layer, allLayerSpecs, maxVer, recipesRoot, upperLayer):
        layerSpec = allLayerSpecs[layer]
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

        config, _ = self.loadConfigYaml(self.loadYaml, rootDir)
        minVer = config.get("bobMinimumVersion", "0.16")
        if compareVersion(maxVer, minVer) < 0:
            if upperLayer:
                upperLayer = f"layer '{upperLayer}'"
            else:
                upperLayer = "the root project"
            raise ParseError(f"Layer '{layer}' requires a higher Bob version ({minVer}) than {upperLayer} ({maxVer})!",
                             help=f"You need to adjust the bobMinimumVersion of {upperLayer} to be higher or equal to layer '{layer}'. Watch out for newly introduced policies between the two versions!")
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

        # First update layer specs of yet unknown layers. Already discovered
        # layers are kept because they were declared by higher layers.
        for l in config.get("layers", []):
            if l.getName() not in allLayerSpecs:
                allLayerSpecs[l.getName()] = l

        # Next parse any sub-layers. Their settings have a lower precedence
        # and may be overwritten by higher layers.
        allLayers = []
        for l in config.get("layers", []):
            allLayers.extend(self.__parseLayer(l.getName(), allLayerSpecs, maxVer, recipesRoot, layer))

        # The PowerShell executable on Windows is a project wide setting.
        self.__windowsPowerShellExecutable = config.get(
            "windowsPowerShellExecutable", self.__windowsPowerShellExecutable)

        # Load plugins and re-create schemas as new keys may have been added
        self.__loadPlugins(rootDir, layer, config.get("plugins", []))
        self.__createSchemas()

        # project user config(s)
        self.__parseUserConfig(os.path.join(rootDir, "default.yaml"))

        # For "inherit", classes are named from lowest to highest precedence.
        # Thus, gather inheritPrepend/inheritAppend after parsing lower layers
        # so that higher layers (higher precedence) come last.
        self.__inheritPrepend.extend(config.get("inheritPrepend", []))
        self.__inheritAppend.extend(config.get("inheritAppend", []))

        # color mode provided in cmd line takes precedence
        # (if no color mode provided by user, default one will be used)
        setColorMode(self._colorModeConfig or self.__uiConfig.get('color', 'auto'))

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

        useClauses = ['deps', 'environment', 'result', 'tools', 'sandbox', 'interpreters']
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
                StrDictValidator(dependsInnerClause)
            )
        ])
        dependsInnerClause[schema.Optional('depends')] = dependsClause

        classSchemaSpec = {
            schema.Optional('checkoutFinalize') : str,
            schema.Optional('checkoutFinalizeBash') : str,
            schema.Optional('checkoutFinalizePwsh') : str,
            schema.Optional('checkoutFinalizePython') : str,
            schema.Optional('checkoutScript') : str,
            schema.Optional('checkoutScriptBash') : str,
            schema.Optional('checkoutScriptPwsh') : str,
            schema.Optional('checkoutScriptPython') : str,
            schema.Optional('checkoutSetup') : str,
            schema.Optional('checkoutSetupBash') : str,
            schema.Optional('checkoutSetupPwsh') : str,
            schema.Optional('checkoutSetupPython') : str,
            schema.Optional('checkoutUpdateIf', default=False) : schema.Or(None, str, bool, IfExpression),
            schema.Optional('buildFinalize') : str,
            schema.Optional('buildFinalizeBash') : str,
            schema.Optional('buildFinalizePwsh') : str,
            schema.Optional('buildFinalizePython') : str,
            schema.Optional('buildScript') : str,
            schema.Optional('buildScriptBash') : str,
            schema.Optional('buildScriptPwsh') : str,
            schema.Optional('buildScriptPython') : str,
            schema.Optional('buildSetup') : str,
            schema.Optional('buildSetupBash') : str,
            schema.Optional('buildSetupPwsh') : str,
            schema.Optional('buildSetupPython') : str,
            schema.Optional('packageFinalize') : str,
            schema.Optional('packageFinalizeBash') : str,
            schema.Optional('packageFinalizePwsh') : str,
            schema.Optional('packageFinalizePython') : str,
            schema.Optional('packageScript') : str,
            schema.Optional('packageScriptBash') : str,
            schema.Optional('packageScriptPwsh') : str,
            schema.Optional('packageScriptPython') : str,
            schema.Optional('packageSetup') : str,
            schema.Optional('packageSetupBash') : str,
            schema.Optional('packageSetupPwsh') : str,
            schema.Optional('packageSetupPython') : str,
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
            schema.Optional('localEnvironment') : VarDefineValidator("localEnvironment"),
            schema.Optional('privateEnvironment') : VarDefineValidator("privateEnvironment"),
            schema.Optional('metaEnvironment') : VarDefineValidator("metaEnvironment"),
            schema.Optional('provideDeps') : [str],
            schema.Optional('provideTools') : schema.Schema({
                str: schema.Or(
                    str,
                    StrDictValidator({
                        'path' : str,
                        schema.Optional('libs') : [str],
                        schema.Optional('netAccess') : bool,
                        schema.Optional('environment') : VarDefineValidator("provideTools::environment"),
                        schema.Optional('fingerprintScript', default="") : str,
                        schema.Optional('fingerprintScriptBash') : str,
                        schema.Optional('fingerprintScriptPwsh') : str,
                        schema.Optional('fingerprintScriptPython') : str,
                        schema.Optional('fingerprintIf') : schema.Or(None, str, bool, IfExpression),
                        schema.Optional('fingerprintVars') : [ varNameUseSchema ],
                        schema.Optional('dependTools') : [ ToolValidator(varNameUseSchema) ],
                        schema.Optional('dependToolsWeak') : [ ToolValidator(varNameUseSchema)  ],
                    })
                )
            }),
            schema.Optional('provideVars') : VarDefineValidator("provideVars"),
            schema.Optional('provideInterpreters') : schema.And(
                schema.Schema({ schema.Optional(l.value) : str for l in ScriptLanguage }),
                schema.Use(lambda d: { ScriptLanguage(l) : v for l, v in d.items() })
            ),
            schema.Optional('provideSandbox') : StrDictValidator({
                'paths' : [str],
                schema.Optional('mount') : schema.Schema([ MountValidator() ],
                    error="provideSandbox: invalid 'mount' property"),
                schema.Optional('environment') : VarDefineValidator("provideSandbox::environment"),
                schema.Optional('user') : schema.Or("nobody", "root", "$USER"),
            }),
            schema.Optional('root') : schema.Or(bool, str, IfExpression),
            schema.Optional('shared') : schema.Or(bool, str, IfExpression),
            schema.Optional('relocatable') : bool,
            schema.Optional('buildNetAccess') : bool,
            schema.Optional('packageNetAccess') : bool,
            schema.Optional('fingerprintScript', default="") : str,
            schema.Optional('fingerprintScriptBash') : str,
            schema.Optional('fingerprintScriptPwsh') : str,
            schema.Optional('fingerprintScriptPython') : str,
            schema.Optional('fingerprintIf') : schema.Or(None, str, bool, IfExpression),
            schema.Optional('fingerprintVars') : [ varNameUseSchema ],
            schema.Optional('scriptLanguage') : schema.And(schema.Or("bash", "PowerShell", "python"),
                                                           schema.Use(ScriptLanguage)),
            schema.Optional('jobServer') : schema.Or(bool, "pipe", "fifo", "fifo-or-pipe"),
            schema.Optional('packageDepends') : bool,
            schema.Optional('checkoutAuditFiles') : AuditFilesValidator("checkoutAuditFiles"),
            schema.Optional('buildAuditFiles') : AuditFilesValidator("buildAuditFiles"),
            schema.Optional('packageAuditFiles') : AuditFilesValidator("packageAuditFiles"),
        }
        for (name, prop) in self.__properties.items():
            classSchemaSpec[schema.Optional(name)] = schema.Schema(
                wrapValidator(prop.validate,
                              "property '"+name+"' has an invalid type"))

        self.__classSchema = (StrDictValidator(classSchemaSpec), self.__pluginPropDeps)

        recipeSchemaSpec = classSchemaSpec.copy()
        recipeSchema = StrDictValidator(recipeSchemaSpec)
        recipeSchemaSpec[schema.Optional('multiPackage')] = schema.Schema({
            MULTIPACKAGE_NAME_SCHEMA : recipeSchema
        })
        self.__recipeSchema = (recipeSchema, self.__pluginPropDeps)

        userConfigSchemaSpec = {
            schema.Optional('include') : schema.Schema([str]),
            schema.Optional('require') : schema.Schema([str]),
        }
        for (name, setting) in self.__settings.items():
            userConfigSchemaSpec[schema.Optional(name)] = schema.Schema(
                wrapValidator(setting.validate,
                              "setting '"+name+"' has an invalid type"))
        self.__userConfigSchema = (schema.Schema(userConfigSchemaSpec), self.__pluginSettingsDeps)


    def getInheritAppend(self):
        return self.__inheritAppend

    def getInheritPrepend(self):
        return self.__inheritPrepend

    def getRecipes(self):
        return self.__recipes.keys()

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
                    return tmp.refDeref([], {}, None, {}, pathsConfig)
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
                PackagePickler(f).dump(result)
            replacePath(newCacheName, cacheName)
        except OSError as e:
            Warn("Could not save package cache: " + str(e)).show(cacheName)

        return result.refDeref([], {}, None, {}, pathsConfig)

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

    def getWindowsPowerShellExecutable(self):
        return self.__windowsPowerShellExecutable
