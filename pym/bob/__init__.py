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
    version=""
    try:
       root = os.path.dirname(os.path.realpath(__file__))
       _cwd = os.path.join(root, '..', '..')
       version = subprocess.check_output("git describe --tags --dirty --always".split(" "), cwd=_cwd,
                universal_newlines=True, stderr=subprocess.STDOUT).rstrip()
    except subprocess.CalledProcessError as e:
        pass

    return version

# See http://semver.org/ and adjust accordingly
BOB_VERSION = "0.9-dev-"+getVersion()
