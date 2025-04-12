import datetime
import os
import schema
import shutil
from textwrap import indent
from .errors import BuildError, ParseError
from .invoker import CmdFailedError, InvocationError, Invoker
from .scm import getScm, ScmOverride, ScmStatus, ScmTaint
from .state import BobState
from .stringparser import Env
from .input import RecipeSet, Scm, YamlCache
from .utils import INVALID_CHAR_TRANS, getPlatformString, getPlatformEnvWhiteList, compareVersion, joinLines
from .tty import DEBUG, EXECUTED, INFO, NORMAL, IMPORTANT, SKIPPED, WARNING, log

class LayersConfig:
    def __init__(self):
        self.__platform = getPlatformString()
        self.__whiteList = getPlatformEnvWhiteList(self.__platform)
        self.__scmOverrides = []
        self.__policies = None

    def derive(self, config, rootLayer=False):
        """Create a new LayersConfig by adding the passed config to this one."""
        ret = LayersConfig()
        ret.__whiteList = set(self.__whiteList)
        ret.__scmOverrides = self.__scmOverrides[:]
        ret.__policies = self.__policies

        ret.__whiteList.update([c.upper() if self.__platform == "win32" else c
            for c in config.get("layersWhitelist", []) ])
        ret.__scmOverrides[0:0] = [ ScmOverride(o) for o in config.get("layersScmOverrides", []) ]

        if rootLayer:
            ret.__policies = RecipeSet.calculatePolicies(config)

        return ret

    # The following is required by bob.input.Scm

    SCM_SCHEMA = RecipeSet.LAYERS_SCM_SCHEMA

    def envWhiteList(self):
        return self.__whiteList

    def scmOverrides(self):
        return self.__scmOverrides

    # The following methods are required for bob.input.RecipeSet compatibility.

    def scmDefaults(self):
        return {}

    def getPreMirrors(self):
        return []

    def getFallbackMirrors(self):
        return []

    def getPolicy(self, name, location=None):
        (policy, warning) = self.__policies[name]
        if policy is None:
            warning.show(location)
        return policy


class LayerStepSpec:
    def __init__(self, path, whitelist):
        self.__path = path
        self.__whitelist = whitelist

    @property
    def workspaceWorkspacePath(self):
        return self.__path

    @property
    def env(self):
        return {}

    @property
    def envWhiteList(self):
        return self.__whitelist


class Layer:
    def __init__(self, name, upperConfig, defines, projectRoot, scm=None):
        self.__name = name
        self.__upperConfig = upperConfig
        self.__defines = defines
        self.__projectRoot = projectRoot
        self.__scm = scm
        self.__created = False

        # SCM backed layers are in build dir, regular layers are in project dir.
        layerDir = projectRoot if scm is None else ""
        if name:
            layerDir = os.path.join(layerDir, "layers", name)
        self.__layerDir = layerDir
        self.__subLayers = []

    async def __checkoutTask(self, verbose, attic):
        if self.__scm is None:
            return

        invoker = Invoker(spec=LayerStepSpec(self.__layerDir, self.__upperConfig.envWhiteList()),
                          preserveEnv= False,
                          noLogFiles = True,
                          showStdOut = verbose > INFO,
                          showStdErr = verbose > INFO,
                          trace = verbose >= DEBUG,
                          redirect=False, executor=None)
        newState = {}
        newState["digest"] = self.__scm.asDigestScript()
        newState["prop"] = {k:v for k,v in self.__scm.getProperties(False).items() if v is not None}

        oldState = BobState().getLayerState(self.__layerDir)

        if os.path.exists(self.__layerDir) and oldState is None:
            raise BuildError(f"New layer checkout '{self.getName()}' collides with existing layer '{self.__layerDir}'!")

        if not os.path.isdir(self.__layerDir):
            os.makedirs(self.__layerDir)
            self.__created = True

        if not self.__created \
           and self.__scm.isDeterministic() \
           and oldState is not None \
           and oldState["digest"] == newState["digest"]:
            log("CHECKOUT: Layer " +
                "'{}' skipped (up to date)".format(self.getName()), SKIPPED, INFO)
            return

        if not self.__created and oldState is not None and \
            newState["digest"] != oldState["digest"]:

            canSwitch = self.__scm.canSwitch(getScm(oldState["prop"]))
            if canSwitch:
                log("SWITCH: Layer '{}' .. ok".format(self.getName()), EXECUTED, NORMAL)
                ret = await invoker.executeScmSwitch(self.__scm, oldState["prop"])
                if ret == 0:
                    BobState().setLayerState(self.__layerDir, newState)
                    return

            if not attic:
                raise BuildError("Layer '{}' inline switch not possible and move to attic disabled '{}'!"
                    .format(self.__name, self.__layerDir))
            atticName = datetime.datetime.now().isoformat().translate(INVALID_CHAR_TRANS)+"_"+os.path.basename(self.__layerDir)
            log("ATTIC: Layer " +
                "{} (move to ../../layers.attic/{})".format(self.__layerDir, atticName), WARNING)
            atticPath = os.path.join(self.__layerDir, "..", "..", "layers.attic")
            if not os.path.isdir(atticPath):
                os.makedirs(atticPath)
            atticPath = os.path.join(atticPath, atticName)
            os.rename(self.__layerDir, atticPath)
            BobState().delLayerState(self.__layerDir)
            os.makedirs(self.__layerDir)
            self.__created = True

        await self.__scm.invoke(invoker, self.__created)
        log("CHECKOUT: Layer " +
                "'{}' .. ok".format(self.getName()), EXECUTED, NORMAL)
        BobState().setLayerState(self.__layerDir, newState)

    def checkout(self, loop, verbose, attic):
        try:
            j = loop.create_task(self.__checkoutTask(verbose, attic))
            loop.run_until_complete(j)
        except (CmdFailedError, InvocationError) as e:
            if self.__created:
                shutil.rmtree(self.__layerDir)
            raise BuildError(f"Failed to checkout Layer {self.getName()}: {e.what}")

    def getWorkspace(self):
        return self.__layerDir

    def getName(self):
        return self.__name

    def parse(self, yamlCache):
        configSchema = (schema.Schema({**RecipeSet.STATIC_CONFIG_SCHEMA_SPEC,
                                       **RecipeSet.STATIC_CONFIG_LAYER_SPEC}), b'')
        config = RecipeSet.loadConfigYaml(yamlCache.loadYaml, self.__layerDir)
        self.__config = self.__upperConfig.derive(config, self.__name == "")

        configYaml = os.path.join(self.__layerDir, "config.yaml")
        for l in config.get('layers', []):
            scmSpec = l.getScm()
            if scmSpec is None:
                layerScm = None
            else:
                scmSpec.update({'recipe':configYaml})
                layerScm = Scm(scmSpec,
                               Env(self.__defines),
                               overrides=self.__config.scmOverrides(),
                               recipeSet=self.__config)
            self.__subLayers.append(Layer(l.getName(),
                                          self.__config,
                                          self.__defines,
                                          self.__projectRoot,
                                          layerScm))

    def getSubLayers(self):
        return self.__subLayers

    def getScm(self):
        return self.__scm

    def getPolicy(self, name, location=None):
        return self.__config.getPolicy(name, location)

    def isManaged(self):
        return self.__scm is not None

class Layers:
    def __init__(self, defines, attic):
        self.__layers = {}
        self.__attic = attic if attic is not None else True
        self.__defines = defines
        self.__layerConfigFiles = []

        self.__projectRoot = os.getcwd()
        if os.path.isfile(".bob-project"):
            try:
                with open(".bob-project") as f:
                    self.__projectRoot = f.read()
            except OSError as e:
                raise ParseError("Broken project link: " + str(e))

    def __haveLayer(self, layer):
        for depth,layers in self.__layers.items():
            for l in layers:
                if l.getName() == layer.getName():
                    return True
        return False

    def __collect(self, loop, depth, yamlCache, update, verbose):
        self.__layers[depth+1] = []
        newLevel = False
        for l in self.__layers[depth]:
            if update:
                l.checkout(loop, verbose, self.__attic)
            l.parse(yamlCache)
            for subLayer in l.getSubLayers():
                if not self.__haveLayer(subLayer):
                    self.__layers[depth+1].append(subLayer)
                    newLevel = True
        if newLevel:
            self.__collect(loop, depth + 1, yamlCache, update, verbose)

    def cleanupUnused(self):
        old_layers = BobState().getLayers()
        layers = []
        for level in sorted(self.__layers.keys()):
            for layer in self.__layers[level]:
                layers.append(layer.getWorkspace())

        for d in [x for x in old_layers if x not in layers]:
            if os.path.exists(d):
                atticName = datetime.datetime.now().isoformat().translate(INVALID_CHAR_TRANS)+"_"+os.path.basename(d)
                log("ATTIC: Layer " +
                    "{} (move to ../../layers.attic/{})".format(d, atticName), WARNING)
                atticPath = os.path.join(d, "..", "..", "layers.attic")
                if not os.path.isdir(atticPath):
                    os.makedirs(atticPath)
                atticPath = os.path.join(atticPath, atticName)
                os.rename(d, atticPath)
                BobState().delLayerState(d)

    def collect(self, loop, update, verbose=0, requireManagedLayers=True):
        configSchema = (schema.Schema(RecipeSet.STATIC_CONFIG_LAYER_SPEC), b'')
        config = LayersConfig()
        with YamlCache() as yamlCache:
            for c in reversed(self.__layerConfigFiles):
                c += ".yaml"
                if not os.path.exists(c):
                    raise BuildError(f"Layer config file {c} not found" )

                config = config.derive(yamlCache.loadYaml(c, configSchema))

            rootLayers = Layer("", config, self.__defines, self.__projectRoot)
            rootLayers.parse(yamlCache)
            if not rootLayers.getPolicy("managedLayers"):
                if requireManagedLayers:
                    raise ParseError("Managed layers aren't enabled! See the managedLayers policy for details.")
                else:
                    return False
            self.__layers[0] = rootLayers.getSubLayers();
            self.__collect(loop, 0, yamlCache, update, verbose)

        return True

    def setLayerConfig(self, configFiles):
        self.__layerConfigFiles = configFiles

    def status(self, printer):
        result = {}
        currentLayers = {}
        for level in self.__layers.keys():
            for layer in self.__layers[level]:
                scm = layer.getScm()
                if scm is not None:
                    currentLayers[layer.getWorkspace()] = (scm.asDigestScript(), scm)

        # Scan previously known layer SCMs first. They may not be referenced
        # any more but we want to know their status nonetheless.
        for layerDir in BobState().getLayers():
            if not os.path.exists(layerDir): continue

            oldLayer = BobState().getLayerState(layerDir)
            if oldLayer["digest"] == currentLayers.get(layerDir, (None, None))[0]:
                # The digest still matches -> use current SCM properties
                status = currentLayers[layerDir][1].status(layerDir)
            else:
                # Changed or removed. Compare with that and mark as attic...
                status = getScm(oldLayer["prop"]).status(layerDir)
                status.add(ScmTaint.attic,
                           "> Config.yaml changed. Will be moved to attic on next update.")

            result[layerDir] = status

        # Additionally, scan current layers state to find new checkouts and
        # determine override status.
        for layerDir, (layerDigets, layerScm) in currentLayers.items():
            status = result.setdefault(layerDir, ScmStatus(ScmTaint.new))
            if (ScmTaint.new in status.flags) and os.path.exists(layerDir):
                status.add(ScmTaint.collides,
                    "> Collides with existing directory in project.")
            elif ScmTaint.attic in status.flags:
                status.add(ScmTaint.new)

            # The override status is taken from the layer SCM. This is
            # independent of any actual checkout.
            overrides = layerScm.getActiveOverrides()
            for o in overrides:
                status.add(ScmTaint.overridden, joinLines("> Overridden by:",
                    indent(str(o), '   ')))

        # Show results
        for (layerDir, status) in sorted(result.items()):
            printer(status, layerDir)

    def __iter__(self):
        for level in self.__layers.keys():
            for layer in self.__layers[level]:
                yield layer

def updateLayers(loop, defines, verbose, attic, layerConfigs, requireManagedLayers=True):
    layers = Layers(defines, attic)
    layers.setLayerConfig(layerConfigs)
    if layers.collect(loop, True, verbose, requireManagedLayers):
        layers.cleanupUnused()
