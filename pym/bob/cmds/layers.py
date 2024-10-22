import argparse

from ..layers import Layers, updateLayers
from ..tty import NORMAL, setVerbosity
from ..utils import EventLoopWrapper, processDefines
from .build.status import PackagePrinter

def doLayersStatus(argv):
    parser = argparse.ArgumentParser(prog="bob layers status", description='Query layers SCM status')
    parser.add_argument('-lc', dest="layerConfig", default=[], action='append',
        help="Additional layer config")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('--show-clean', action='store_true',
        help="Show SCM status even if layer is unmodified")
    parser.add_argument('--show-overrides', action='store_true',
        help="Show SCM status if affected by an scmOverrides")
    parser.add_argument('-v', '--verbose', default=NORMAL, action='count',
        help="Increase verbosity (may be specified multiple times)")

    args = parser.parse_args(argv)
    setVerbosity(args.verbose)
    defines = processDefines(args.defines)

    layers = Layers(defines, False)
    layers.setLayerConfig(args.layerConfig)
    layers.collect(None, False, args.verbose)
    pp = PackagePrinter(args.verbose, args.show_clean, args.show_overrides)
    layers.status(pp.show)


def doLayersUpdate(argv):
    parser = argparse.ArgumentParser(prog="bob layers update", description='Update layers SCMs')
    parser.add_argument('-lc', dest="layerConfig", default=[], action='append',
        help="Additional layer config")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--attic', action='store_true', default=None,
        help="Move scm to attic if inline switch is not possible (default).")
    group.add_argument('--no-attic', action='store_false', default=None, dest='attic',
        help="Do not move to attic, instead fail the build.")
    parser.add_argument('-v', '--verbose', default=NORMAL, action='count',
        help="Increase verbosity (may be specified multiple times)")

    args = parser.parse_args(argv)
    setVerbosity(args.verbose)
    defines = processDefines(args.defines)

    with EventLoopWrapper() as (loop, executor):
        updateLayers(loop, defines, args.verbose, args.attic, args.layerConfig)


def doLayers(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob layers", description='Handle layers')
    parser.add_argument('action', type=str, choices=['update', 'status'],
                        help="Action: [update, status]")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for action")

    args = parser.parse_args(argv)
    if args.action == "status":
        doLayersStatus(args.args)
    elif args.action == "update":
        doLayersUpdate(args.args)
