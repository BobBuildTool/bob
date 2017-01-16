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
    import os
    import subprocess

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
        try:
            version = subprocess.check_output("git describe --tags --dirty".split(" "),
                cwd=root, universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            pass

    if version:
        # strip white spaces and leading 'v' from tag name
        version = version.strip().lstrip("v")
    else:
        # See http://semver.org/ and adjust accordingly
        version = "0.10"

    return version

BOB_VERSION = getVersion()
