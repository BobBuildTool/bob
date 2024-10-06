import datetime
import os
import schema
import shutil
from .errors import BuildError
from .invoker import CmdFailedError, InvocationError, Invoker
from .scm import getScm, ScmOverride
from .state import BobState
from .stringparser import Env
from .input import RecipeSet, Scm, YamlCache
from .utils import INVALID_CHAR_TRANS, getPlatformString, getPlatformEnvWhiteList, compareVersion
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
            for c in config.get("layersWhiteList", []) ])
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
    def __init__(self, name, upperConfig, defines, attic, scm=None):
        self.__name = name
        self.__upperConfig = upperConfig
        self.__defines = defines
        self.__attic = attic
        self.__scm = scm
        self.__created = False
        self.__layerDir = os.path.join("layers", name) if len(name) else "."
        self.__subLayers = []

    async def __checkoutTask(self, verbose):
        if self.__scm is None:
            return
        dir = self.__scm.getProperties(False).get("dir")

        invoker = Invoker(spec=LayerStepSpec(self.__layerDir, self.__upperConfig.envWhiteList()),
                          preserveEnv= False,
                          noLogFiles = True,
                          showStdOut = verbose > INFO,
                          showStdErr = verbose > INFO,
                          trace = verbose >= DEBUG,
                          redirect=False, executor=None)
        newState = {}
        newState["digest"] = self.__scm.asDigestScript(),
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
                log("SWITCH: Layer '{}' .. ok".format(self.getName()), EXECUTED, INFO)
                ret = await invoker.executeScmSwitch(self.__scm, oldState["prop"])
                if ret == 0:
                    BobState().setLayerState(self.__layerDir, newState)
                    return

            if not self.__attic:
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

        await self.__scm.invoke(invoker)
        log("CHECKOUT: Layer " +
                "'{}' .. ok".format(self.getName()), EXECUTED, NORMAL)
        BobState().setLayerState(self.__layerDir, newState)

    def checkout(self, loop, verbose):
        try:
            j = loop.create_task(self.__checkoutTask(verbose))
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
            if scmSpec is None: continue
            scmSpec.update({'recipe':configYaml})
            layerScm = Scm(scmSpec,
                           Env(self.__defines),
                           overrides=self.__config.scmOverrides(),
                           recipeSet=self.__config)
            self.__subLayers.append(Layer(l.getName(),
                                          self.__config,
                                          self.__defines,
                                          self.__attic,
                                          layerScm))

    def getSubLayers(self):
        return self.__subLayers

    def status(self, printer):
        if self.__scm is None:
            return
        status = self.__scm.status(self.__layerDir)
        printer(status, self.__layerDir)

class Layers:
    def __init__(self, defines, attic):
        self.__layers = {}
        self.__attic = attic
        self.__defines = defines
        self.__layerConfigFiles = []

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
                l.checkout(loop, verbose)
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

    def collect(self, loop, update, verbose=0):
        configSchema = (schema.Schema(RecipeSet.STATIC_CONFIG_LAYER_SPEC), b'')
        config = LayersConfig()
        with YamlCache() as yamlCache:
            for c in reversed(self.__layerConfigFiles):
                c += ".yaml"
                if not os.path.exists(c):
                    raise BuildError(f"Layer config file {c} not found" )

                config = config.derive(yamlCache.loadYaml(c, configSchema))

            rootLayers = Layer("", config, self.__defines, self.__attic)
            rootLayers.parse(yamlCache)
            self.__layers[0] = rootLayers.getSubLayers();
            self.__collect(loop, 0, yamlCache, update, verbose)

    def setLayerConfig(self, configFiles):
        self.__layerConfigFiles = configFiles

    def status(self, printer):
        for level in sorted(self.__layers.keys()):
            for layer in self.__layers[level]:
                layer.status(printer)

def updateLayers(loop, defines, verbose, attic, layerConfigs):
    layers = Layers(defines, attic)
    layers.setLayerConfig(layerConfigs)
    layers.collect(loop, True, verbose)
    layers.cleanupUnused()
