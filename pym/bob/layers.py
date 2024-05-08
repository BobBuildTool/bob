import datetime
import os
import schema
import shutil
from .errors import BuildError
from .invoker import CmdFailedError, InvocationError, Invoker
from .scm import ScmOverride
from .state import BobState
from .stringparser import Env
from .input import RecipeSet, Scm, YamlCache
from .utils import INVALID_CHAR_TRANS
from .tty import DEBUG, EXECUTED, INFO, NORMAL, IMPORTANT, SKIPPED, WARNING, log

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
    def __init__(self, name, root, recipes, yamlCache, defines, attic, 
                 whitelist, overrides, scm=None):
        self.__attic = attic
        self.__created = False
        self.__defines = defines
        self.__layerDir = os.path.join(root, "layers", name) if len(name) else root
        self.__name = name
        self.__recipes = recipes
        self.__root = root
        self.__subLayers = []
        self.__scm = scm
        self.__yamlCache = yamlCache
        self.__whitelist = whitelist
        self.__overrides = overrides

    async def __checkoutTask(self, verbose):
        if self.__scm is None:
            return
        dir = self.__scm.getProperties(False).get("dir")

        invoker = Invoker(spec=LayerStepSpec(self.__layerDir, self.__whitelist),
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

        created = False
        if not os.path.isdir(self.__layerDir):
            os.makedirs(self.__layerDir)
            self.__created = True

        if not created \
           and self.__scm.isDeterministic() \
           and oldState is not None \
           and oldState["digest"] == newState["digest"]:
            log("CHECKOUT: Layer " +
                "'{}' skipped (up to date)".format(self.getName()), SKIPPED, INFO)
            return

        if not created and oldState is not None and \
            newState["digest"] != oldState["digest"]:

            canSwitch = self.__scm.canSwitch(Scm(oldState["prop"],
                                 Env(self.__defines),
                                 overrides=self.__overrides,
                                 recipeSet=self.__recipes))
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

    def getWhiteList(self):
        return self.__layerWhitelist

    def loadYaml(self, path, schema):
        if os.path.exists(path):
            return self.__yamlCache.loadYaml(path, schema, {}, preValidate=lambda x: None)
        return {}

    def parse(self):
        configYaml = os.path.join(self.__layerDir, "config.yaml")
        config = self.loadYaml(configYaml, (schema.Schema({**RecipeSet.STATIC_CONFIG_SCHEMA_SPEC,
                                                           **RecipeSet.STATIC_CONFIG_LAYER_SPEC}), b''))

        self.__whitelist.append([c.upper() if self.__platform == "win32" else c
            for c in config.get("layersWhiteList", []) ])

        self.__overrides.extend([ ScmOverride(o) for o in config.get("layersScmOverrides", []) ])

        for l in config.get('layers', []):
            scmSpec = l.getScm()
            if scmSpec is None: continue
            scmSpec.update({'recipe':configYaml})
            layerScms = Scm(scmSpec,
                            Env(self.__defines),
                            overrides=self.__overrides,
                            recipeSet=self.__recipes)
            self.__subLayers.append(Layer(l.getName(),
                                          self.__root,
                                          self.__recipes,
                                          self.__yamlCache,
                                          self.__defines,
                                          self.__attic,
                                          self.__whitelist,
                                          self.__overrides,
                                          layerScms))

    def getSubLayers(self):
        return self.__subLayers

    def status(self, printer):
        if self.__scm is None:
            return
        status = self.__scm.status(self.__layerDir)
        printer(status, self.__layerDir)

class Layers:
    def __init__(self, recipes, loop, defines, attic):
        self.__layers = {}
        self.__loop = loop
        self.__recipes = recipes
        self.__attic = attic
        self.__defines = defines
        self.__yamlCache = YamlCache()
        self.__whitelist = []
        self.__overrides = []
        self.__layerConfigFiles = []

    def __haveLayer(self, layer):
        for depth,layers in self.__layers.items():
            for l in layers:
                if l.getName() == layer.getName():
                    return True
        return False

    def __collect(self, depth, update, verbose):
        self.__layers[depth+1] = []
        newLevel = False
        for l in self.__layers[depth]:
            if update:
                l.checkout(self.__loop, verbose)
            l.parse()
            for subLayer in l.getSubLayers():
                if not self.__haveLayer(subLayer):
                    self.__layers[depth+1].append(subLayer)
                    newLevel = True
        if newLevel:
            self.__collect(depth + 1, update, verbose)

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

    def collect(self, update, verbose=0):
        self.__yamlCache.open()
        try:
            for c in self.__layerConfigFiles:
                c += ".yaml"
                if os.path.exists(c):
                    config = self.__yamlCache.loadYaml(c, (schema.Schema(RecipeSet.STATIC_CONFIG_LAYER_SPEC), b''),
                                                       {}, preValidate=lambda x: None)
                    self.__whitelist.append([c.upper() if self.__platform == "win32" else c
                        for c in config.get("layersWhiteList", []) ])
                    self.__overrides.extend([ ScmOverride(o) for o in config.get("layersScmOverrides", []) ])
                else:
                    raise BuildError(f"Layer config file {c} not found" )

            rootLayers = Layer("", os.getcwd(), self.__recipes, self.__yamlCache,
                               self.__defines, self.__attic, self.__whitelist, self.__overrides)
            rootLayers.parse()
            self.__layers[0] = rootLayers.getSubLayers();
            self.__collect(0, update, verbose)
        finally:
            self.__yamlCache.close()

    def setLayerConfig(self, configFiles):
        self.__layerConfigFiles = configFiles

    def status(self, printer):
        for level in sorted(self.__layers.keys()):
            for layer in self.__layers[level]:
                layer.status(printer)

def updateLayers(recipes, loop, defines, verbose, attic, layerConfigs):
    recipes.parse(defines, noLayers=True)
    layers = Layers(recipes, loop, defines, attic)
    layers.setLayerConfig(layerConfigs)
    layers.collect(True, verbose)
    layers.cleanupUnused()
