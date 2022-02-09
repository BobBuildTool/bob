# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

def filterDirs(dirs, exclude):
    i = 0
    while i < len(dirs):
        if dirs[i] in exclude:
            del dirs[i]
        else:
            i += 1

def getBobInputHash():
    import hashlib
    import os, os.path
    # we need the source hash to invalidate the cache in case of source code changes.
    # therefore it's enough to hash the pym directory without the entries of cmds-Dir
    root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
    exclude = frozenset(['__pycache__', 'cmds'])
    h = hashlib.sha1()
    for root, dirs, files in os.walk(root):
        filterDirs(dirs, exclude)
        dirs.sort()
        for fn in sorted(files):
            with open(os.path.join(root, fn), "rb") as f:
                h.update(f.read())
    return h.digest()

# First try to see if we're running a development version. If we do we take the
# version from git and make sure everything is up-to-date. Otherwise Bob was
# installed via pip and we can import the installed version.
try:
    from .develop.version import BOB_VERSION
    try:
        BOB_INPUT_HASH = getBobInputHash()
    except KeyboardInterrupt:
        sys.exit(1)
except ImportError:
    from .version import version as BOB_VERSION
    BOB_INPUT_HASH = BOB_VERSION.encode("utf-8")

# global debug switches
DEBUG = {
    'ngd' :  False,     # no-global-defaults
    'pkgck' : False,    # package-calculation-checks
    'prof' : False,     # profiling
}

# interactive debug shell
def __debugTap(sig, frame):
    import code, traceback

    """Interrupt running process, and provide a python prompt for
    interactive debugging."""
    d={'_frame':frame}         # Allow access to frame object.
    d.update(frame.f_globals)  # Unless shadowed by global
    d.update(frame.f_locals)

    i = code.InteractiveConsole(d)
    message  = "Signal received : entering python shell.\nTraceback:\n"
    message += ''.join(traceback.format_stack(frame))
    i.interact(message)

def _enableDebug(enabled):
    global DEBUG

    for e in enabled.split(','):
        e = e.strip()
        if e in DEBUG:
            DEBUG[e] = True
        else:
            print("Invalid debug flag:", e, file=sys.stderr)
            sys.exit(2)

if sys.platform != "win32":
    import signal
    signal.signal(signal.SIGUSR1, __debugTap)
