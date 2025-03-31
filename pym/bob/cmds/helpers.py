# SPDX-License-Identifier: GPL-3.0-or-later

def processDefines(defs):
    """Convert a list of 'KEY=VALUE' strings into a dict"""
    defines = {}
    for define in defs:
        key, _sep, value = define.partition('=')
        defines[key] = value
    return defines


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

def dumpFlat(doc, prefix=""):
    ret = []
    if isinstance(doc, dict):
        for k,v in sorted(doc.items()):
            ret.extend(dumpFlat(v, prefix+"."+k if prefix else k))
    elif isinstance(doc, list):
        i = 0
        for v in doc:
            ret.extend(dumpFlat(v, "{}[{}]".format(prefix, i)))
            i += 1
    else:
        doc = str(doc).replace('\n', '\\n')
        ret = [ "{}={}".format(prefix, doc) ]

    return ret
