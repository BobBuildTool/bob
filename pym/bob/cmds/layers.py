import argparse

from ..input import RecipeSet
from ..layers import Layers, updateLayers
from ..utils import EventLoopWrapper, processDefines
from ..tty import NORMAL, setVerbosity

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
            updateLayers(recipes, loop, defines, args.verbose)

        recipes.parse(defines)

        layers = Layers(recipes, loop)
        layers.collect(False, args.verbose)
        if args.action == "status":
            layers.status(args.verbose)

