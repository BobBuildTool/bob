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

from .tty import colorize

class BobError(Exception):
    def __init__(self, kind, stackSlogan, slogan, help=""):
        self.kind = kind
        self.slogan = slogan
        self.stackSlogan = stackSlogan
        self.stack = []
        self.help = help

    def __str__(self):
        ret = colorize(self.kind+" error: ", "31;1") + colorize(self.slogan, "31")
        if self.stack:
            ret = ret + "\n" + self.stackSlogan + ": " + "/".join(self.stack)
        if self.help:
            ret = ret + "\n" + self.help
        return ret

    def pushFrame(self, frame):
        if not self.stack or (self.stack[0] != frame):
            self.stack.insert(0, frame)

    def setStack(self, stack):
        if not self.stack: self.stack = stack[:]

class ParseError(BobError):
    def __init__(self, slogan, help=""):
        BobError.__init__(self, "Parse", "Processing stack", slogan, help)

class BuildError(BobError):
    def __init__(self, slogan, help=""):
        BobError.__init__(self, "Build", "Failed package", slogan, help)

