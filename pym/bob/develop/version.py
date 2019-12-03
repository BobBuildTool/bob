# Bob build tool
# Copyright (C) 2019  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

import sys

# validate Python version
if sys.version_info.major != 3:
    print("Bob requires Python 3")
    sys.exit(1)
elif sys.version_info.minor < 5:
    print("Bob requires at least Python 3.5")
    sys.exit(1)

def getVersion():
    import os, re

    version = ""
    root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..', '..')

    # try to query git
    if os.path.exists(os.path.join(root, ".git")):
        import subprocess
        try:
            version = subprocess.check_output("git describe --tags --dirty".split(" "),
                cwd=root, universal_newlines=True, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError):
            pass

    if version:
        m = re.match(r"^v(?P<version>[0-9]+(?:\.[0-9]+){2})(?P<rc>-rc[0-9]+)?(?P<dist>-[0-9]+-g[a-f0-9]+)?(?P<dirty>-dirty)?$", version)
        if m is not None:
            # Convert to PEP 440 conforming version number
            local = []
            version = m.group("version")
            if m.group("rc"): version += m.group("rc")[1:]
            if m.group("dist"):
                dist,commit = m.group("dist")[1:].split("-")
                version += ".dev" + dist
                local.append(commit)
            if m.group("dirty"): local.append("dirty")
            if local: version += "+" + ".".join(local)
        else:
            import sys
            print("Warning: inferred version of Bob does not match schema:",
                version, file=sys.stderr)
            version = ""

    if not version:
        # Last fallback. See PEP 440 and adjust accordingly.
        version = "0.16+unknown"

    return version

try:
    BOB_VERSION = getVersion()
except KeyboardInterrupt:
    sys.exit(1)

