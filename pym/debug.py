# Bob build tool
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
#
#
# This is the entry point for Phyton IDEs like PhyDev.
# It needs to be in a separate package to get relative path working,
# wich are used in bob.
#
# Phydev debug configuration setup:
#  - Main Module: debug.py
#  - Workspace:   recipe repo, e.g. sandbox
#  - Arguments:   bob args, e.g. dev vexpress

from bob.scripts import bob
import sys,os

if __name__ == '__main__':
    rootDir = os.getcwd()
    bob(rootDir)

