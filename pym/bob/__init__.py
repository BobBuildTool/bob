# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

def getVersion():
    import os, re

    version = ""
    root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..')

    # try to read extra version if installed by the Makefile
    vsnFile = os.path.join(root, "version")
    if os.path.isfile(vsnFile):
        try:
            with open(vsnFile) as f:
                version = f.read()
        except OSError:
            pass
    elif os.path.isdir(os.path.join(root, ".git")):
        import subprocess
        try:
            version = subprocess.check_output("git describe --tags --dirty".split(" "),
                cwd=root, universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
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
        version = "0.11-dev"

    return version

BOB_VERSION = getVersion()

def getBobInputHash():
    from .utils import hashDirectory
    import os
    # we need the source hash to invalidate the cache in case of source code changes.
    # therefore it's enough to hash the pym directory without the entries of cmds-Dir
    root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
    return hashDirectory(root, ignoreDirs=['__pycache__', 'cmds'])

BOB_INPUT_HASH = getBobInputHash()

# global debug switch
DEBUG = False

def _enableDebug():
    global DEBUG
    DEBUG = True
