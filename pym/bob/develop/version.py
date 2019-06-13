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
        if re.match(r"^v[0-9]+(\.[0-9]+){2}(-.*)?$", version):
            # strip white spaces and leading 'v' from tag name
            version = version.strip().lstrip("v")
        else:
            import sys
            print("Warning: inferred version of Bob does not match schema:",
                version, file=sys.stderr)
            version = ""

    if not version:
        # Last fallback. See http://semver.org/ and adjust accordingly.
        version = "0.15.0-unknown"

    return version

try:
    BOB_VERSION = getVersion()
except KeyboardInterrupt:
    sys.exit(1)

