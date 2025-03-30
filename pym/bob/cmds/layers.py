# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import os

from ..layers import Layers, updateLayers
from ..tty import NORMAL, setVerbosity
from ..utils import EventLoopWrapper
from .build.status import PackagePrinter
from .helpers import processDefines, dumpYaml, dumpJson, dumpFlat

def addDefaultArgs(parser):
    parser.add_argument('-lc', dest="layerConfig", default=[], action='append',
        help="Additional layer config")
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")

def doLayersStatus(argv):
    parser = argparse.ArgumentParser(prog="bob layers status", description='Query layers SCM status')
    addDefaultArgs(parser)
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
    addDefaultArgs(parser)
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


def lsFlat(layers):
    ret = []
    for name, layer in sorted(layers.items()):
        ret.append("[" + name + "]")
        ret.extend(dumpFlat(layer))
        ret.append("")

    return "\n".join(ret)

def doLayersLs(argv):
    parser = argparse.ArgumentParser(prog="bob layers update",
                                     description='List layers and their SCMs')
    addDefaultArgs(parser)

    ex = parser.add_mutually_exclusive_group()
    ex.add_argument('--indent', type=int, default=4,
        help="Number of spaces to indent (default: 4)")
    ex.add_argument('--no-indent', action='store_const', const=None,
        dest='indent', help="No indent. Compact format.")
    parser.add_argument('--format', choices=['yaml', 'json', 'flat'],
        default="yaml", help="Output format")

    args = parser.parse_args(argv)
    defines = processDefines(args.defines)

    layers = Layers(defines, False)
    layers.setLayerConfig(args.layerConfig)
    layers.collect(None, False)

    ls = {}
    for layer in layers:
        workspace = layer.getWorkspace()
        # Unmanaged layers have absolute paths to the project root directory.
        # Try to convert them to something readable.
        if os.path.isabs(workspace):
            cwd = os.getcwd()
            if workspace.startswith(cwd):
                workspace = workspace[len(cwd)+1:]

        l = { "disposition" : "managed" if layer.isManaged() else "unmanaged",
               "path" : workspace }
        if layer.isManaged():
            l["scm"] = layer.getScm().getProperties(False, True)

        ls[layer.getName()] = l

    if args.format == 'yaml':
        print(dumpYaml(ls, args.indent))
    elif args.format == 'json':
        print(dumpJson(ls, args.indent))
    else:
        assert args.format == 'flat'
        print(lsFlat(ls))


def doLayers(argv, bobRoot):
    parser = argparse.ArgumentParser(prog="bob layers", description='Handle layers')
    parser.add_argument('action', type=str, choices=['ls', 'update', 'status'],
                        help="Command action")
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help="Arguments for action")

    args = parser.parse_args(argv)
    if args.action == "status":
        doLayersStatus(args.args)
    elif args.action == "update":
        doLayersUpdate(args.args)
    elif args.action == "ls":
        doLayersLs(args.args)
