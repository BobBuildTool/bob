# SPDX-License-Identifier: GPL-3.0-or-later

def processDefines(defs):
    """Convert a list of 'KEY=VALUE' strings into a dict"""
    defines = {}
    for define in defs:
        key, _sep, value = define.partition('=')
        defines[key] = value
    return defines
