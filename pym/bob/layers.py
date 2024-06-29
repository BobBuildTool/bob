import argparse
import datetime
import os
from .cmds.build.status import PackagePrinter
from .errors import BuildError
from .invoker import CmdFailedError, InvocationError, Invoker
from .state import BobState
from .input import RecipeSet, Scm, YamlCache
from .utils import EventLoopWrapper, processDefines, INVALID_CHAR_TRANS
from .tty import DEBUG, EXECUTED, INFO, NORMAL, SKIPPED, WARNING, colorize, log, setVerbosity

class LayerStepSpec:
    def __init__(self, path):
        self.__path = path

    @property
    def workspaceWorkspacePath(self):
        return self.__path

    @property
    def env(self):
        return {}

    @property
    def envWhiteList(self):
        return []

class Layer:
    def __init__(self, name, root, recipes, yamlCache, scm=None):
        self.__name = name
        self.__root = root
        self.__recipes = recipes
        self.__yamlCache = yamlCache
        self.__subLayers = []
        self.__scm = scm
        self.__layerDir = os.path.join(self.__root, "layers", self.__name) if len(self.__name) else self.__root

    async def __checkoutTask(self, verbose):
        if self.__scm is None:
            return
        dir = self.__scm.getProperties(False).get("dir")
        layerSrcPath = os.path.join(self.__root, "layers",
                                    self.__name)
        if dir != '.':
            layerSrcPath = os.path.join(layerSrcPath, dir)
        invoker = Invoker(spec=LayerStepSpec(layerSrcPath),
                          preserveEnv=True,
                          noLogFiles = True,
                          showStdOut = verbose >= INFO,
                          showStdErr = verbose >= INFO,
                          trace = verbose >= DEBUG,
                          redirect=False, executor=None)
        newState = {}
        newState["digest"] = self.__scm.asDigestScript(),
        newState["prop"] = {k:v for k,v in self.__scm.getProperties(False).items() if v is not None}

        oldState = BobState().getLayerState(layerSrcPath)

        created = False
        if not os.path.isdir(layerSrcPath):
            os.makedirs(layerSrcPath)
            created = True

        if not created \
           and self.__scm.isDeterministic() \
           and oldState is not None \
           and oldState["digest"] == newState["digest"]:
            log("CHECKOUT: Layer " +
                "{} skipped (up to date)".format(layerSrcPath), SKIPPED, NORMAL)
            return

        if not created and oldState is not None and \
            newState["digest"] != oldState["digest"] and \
            self.__scm.canSwitch(Scm(oldState["prop"],
                                 self.__recipes.getRootEnv(),
                                 overrides=self.__recipes.scmOverrides(),
                                 recipeSet=self.__recipes)):
                ret = await invoker.executeScmSwitch(self.__scm, oldState["prop"])
                log("SWITCH: Layer {} .. ok".format(self.getName()), EXECUTED, INFO)

                if ret == 0:
                    BobState().setLayerState(layerSrcPath, newState)
                    return
        ret = os.path.exists(layerSrcPath)

        if os.path.exists(layerSrcPath) and not created:
            atticName = datetime.datetime.now().isoformat().translate(INVALID_CHAR_TRANS) + "_" + \
                            self.__name
            log("ATTIC: Layer " +
                "{} (move to layers.attic/{})".format(layerSrcPath, atticName), WARNING, WARNING)
            atticPath = os.path.join(self.__root, "layers.attic")
            if not os.path.isdir(atticPath):
                os.makedirs(atticPath)
            atticPath = os.path.join(atticPath, atticName)
            os.rename(layerSrcPath, atticPath)

        if not os.path.isdir(layerSrcPath):
            os.makedirs(layerSrcPath)
        await self.__scm.invoke(invoker)
        log("CHECKOUT: Layer " +
                "{} .. ok".format(self.getName()), EXECUTED, INFO)
        BobState().setLayerState(layerSrcPath, newState)

    def checkout(self, loop, verbose):
        try:
            j = loop.create_task(self.__checkoutTask(verbose))
            loop.run_until_complete(j)
        except CmdFailedError as e:
            raise BuildError(f"Failed to checkout Layer {self.getName()}: {e.what}")
        except InvocationError as e:
            raise BuildError(f"Failed to checkout Layer {self.getName()}: {e.what}")

    def getName(self):
        return self.__name

    def loadYaml(self, path, schema):
        if os.path.exists(path):
            return self.__yamlCache.loadYaml(path, schema, {}, preValidate=lambda x: None)
        return {}

    def parse(self):
        configYaml = os.path.join(self.__layerDir, "config.yaml")
        config = self.loadYaml(configYaml, (RecipeSet.STATIC_CONFIG_SCHEMA, b''))
        for l in config.get('layers', []):
            if not isinstance(l, dict): continue
            scmSpec = l.get("scmSpec", {})
            scmSpec.update({'recipe':configYaml})
            layerScms = Scm(scmSpec,
                            self.__recipes.getRootEnv(),
                            overrides=self.__recipes.scmOverrides(),
                            recipeSet=self.__recipes)
            self.__subLayers.append(Layer(l.get("name"),
                                          self.__root,
                                          self.__recipes,
                                          self.__yamlCache,
                                          layerScms))

    def getSubLayers(self):
        return self.__subLayers

    def status(self, verbose):
        if self.__scm is None:
            return
        pp = PackagePrinter(verbose, False, False)
        status = self.__scm.status(self.__layerDir)
        pp.show(status, self.__layerDir)

class Layers:
    def __init__(self, recipes, loop, yamlCache=None):
        self.__layers = {}
        self.__loop = loop
        self.__scmUpdate = False
        self.__recipes = recipes
        self.__yamlCache = yamlCache
        if yamlCache is None:
            self.__yamlCache = YamlCache()

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

    def collect(self, update, verbose=0):
        if self.__yamlCache is not None:
            self.__yamlCache.open()
        try:
            rootLayers = Layer("", os.getcwd(), self.__recipes, self.__yamlCache)
            rootLayers.parse()
            self.__layers[0] = rootLayers.getSubLayers();
            self.__collect(0, update, verbose)
        finally:
            if self.__yamlCache is not None:
                self.__yamlCache.close()

    def status(self, verbose):
        for level in self.__layers:
            for layer in self.__layers[level]:
                layer.status(verbose)

def fetchLayers(recipes, yamlCache, loop, verbose):
    layers = Layers(recipes, loop, yamlCache)
    layers.collect(True, verbose)

def doLayers(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob layers", description='Handle layers')
    parser.add_argument('action', type=str, choices=['update', 'status'], default="status",
                        help="Action: [update, status]")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    parser.add_argument('-v', '--verbose', default=NORMAL, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    args = parser.parse_args(argv)

    setVerbosity(args.verbose)

    defines = processDefines(args.defines)

    with EventLoopWrapper() as (loop, executor):
        recipes = RecipeSet()
        recipes.setConfigFiles(args.configFile)
        if args.action == "update":
            recipes.updateLayers(loop, defines, args.verbose)

        recipes.parse(defines)

        layers = Layers(recipes, loop)
        layers.collect(False, args.verbose)
        if args.action == "status":
            layers.status(args.verbose)

