# SPDX-License-Identifier: GPL-3.0-or-later

from ..utils import dumpFlat

def processDefines(defs):
    """Convert a list of 'KEY=VALUE' strings into a dict"""
    defines = {}
    for define in defs:
        key, _sep, value = define.partition('=')
        defines[key] = value
    return defines

def addStandardArgs(parser, sandboxDefault=False):
    """Add the -D/-c/sandbox arguments common to most Bob sub-commands"""
    parser.add_argument('-D', default=[], action='append', dest="defines",
        help="Override default environment variable")
    parser.add_argument('-c', dest="configFile", default=[], action='append',
        help="Use config File")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sandbox', action='store_true', default=sandboxDefault,
        help="Enable sandboxing")
    group.add_argument('--slim-sandbox', action='store_false', dest='sandbox',
        help="Enable slim sandboxing")
    group.add_argument('--dev-sandbox', action='store_true', dest='sandbox',
        help="Enable development sandboxing")
    group.add_argument('--strict-sandbox', action='store_true', dest='sandbox',
        help="Enable strict sandboxing")
    group.add_argument('--no-sandbox', action='store_false', dest='sandbox',
        help="Disable sandboxing")


def dumpYaml(doc, indent):
    if indent is None:
        style = None
    else:
        style = False

    import yaml
    return yaml.dump(doc, default_flow_style=style, indent=indent)

def dumpJson(doc, indent):
    import json
    return json.dumps(doc, indent=indent, sort_keys=True)

