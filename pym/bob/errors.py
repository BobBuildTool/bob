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

from .utils import colorize

class BobError(Exception):
    def __init__(self, slogan):
        self.slogan = colorize(slogan, "31")
        self.stack = []

    def __str__(self):
        ret = self.slogan
        if self.stack:
            ret = ret + "\nProcessing stack: " + "/".join(self.stack)
        return ret

    def pushFrame(self, frame):
        if not self.stack or (self.stack[0] != frame):
            self.stack.insert(0, frame)

class ParseError(BobError):
    pass

class BuildError(BobError):
    pass

