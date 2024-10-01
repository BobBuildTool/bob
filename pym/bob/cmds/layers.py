import argparse

from ..input import RecipeSet
from ..layers import Layers, updateLayers
from ..tty import NORMAL, setVerbosity
from ..utils import EventLoopWrapper, processDefines
from .build.status import PackagePrinter

def doLayers(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob layers", description='Handle layers')
    parser.add_argument('action', type=str, choices=['update', 'status'], default="status",
                        help="Action: [update, status]")
    parser.add_argument('-lc', dest="layerConfig", default=[], action='append',
        help="Additional layer config")
    parser.add_argument('-v', '--verbose', default=NORMAL, action='count',
        help="Increase verbosity (may be specified multiple times)")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--attic', action='store_true', default=True,
        help="Move scm to attic if inline switch is not possible (default).")
    group.add_argument('--no-attic', action='store_false', default=None, dest='attic',
        help="Do not move to attic, instead fail the build.")

    args = parser.parse_args(argv)

    setVerbosity(args.verbose)

    defines = processDefines(args.defines)

    with EventLoopWrapper() as (loop, executor):
        recipes = RecipeSet()
        if args.action == "update":
            updateLayers(recipes, loop, defines, args.verbose,
                         args.attic, args.layerConfig)
        elif args.action == "status":
            recipes.parse(defines, noLayers=True)
            layers = Layers(recipes, loop, defines, args.attic)
            layers.setLayerConfig(args.layerConfig)
            layers.collect(False, args.verbose)
            pp = PackagePrinter(args.verbose, False, False)
            layers.status(pp.show)

