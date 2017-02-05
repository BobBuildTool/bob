# Bob build tool
# Copyright (C) 2016  Jan Klötzke
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

import fnmatch
import re

class ScmOverride:
    def __init__(self, override):
        self.__match = override.get("match", {})
        self.__del = override.get("del", [])
        self.__set = override.get("set", {})
        self.__replaceRaw = override.get("replace", {})
        self.__init()

    def __init(self):
        self.__replace = { key : (re.compile(subst["pattern"]), subst["replacement"])
            for (key, subst) in self.__replaceRaw.items() }

    def __getstate__(self):
        return (self.__match, self.__del, self.__set, self.__replaceRaw)

    def __setstate__(self, s):
        (self.__match, self.__del, self.__set, self.__replaceRaw) = s
        self.__init()

    def __doesMatch(self, scm):
        for (key, value) in self.__match.items():
            if key not in scm: return False
            if not fnmatch.fnmatchcase(scm[key], value): return False
        return True

    def mangle(self, scm):
        ret = False
        if self.__doesMatch(scm):
            ret = True
            scm = scm.copy()
            for d in self.__del:
                if d in scm: del scm[d]
            scm.update(self.__set)
            for (key, (pat, repl)) in self.__replace.items():
                if key in scm:
                    scm[key] = re.sub(pat, repl, scm[key])
        return ret, scm

    def __str__(self):
        return str("match: " + str(self.__match)  + "\n"
                + (("del: " + str(self.__del) + "\n") if self.__del else "")
                + (("set: " + str(self.__set)+ "\n") if self.__set else "")
                + (("replace: " + str(self.__replaceRaw)) if self.__replaceRaw else "")).rstrip()

class Scm(object):
    def __init__(self, overrides=[]):
        self.__overrides = overrides

    def getActiveOverrides(self):
        return self.__overrides

    def statusOverrides(self, workspacePath, dir):
        overrides = self.getActiveOverrides()
        if len(overrides):
            status = "O"
            longStatus = ""
            for o in overrides:
                overrideText = str(o).rstrip().replace('\n', '\n       ')
                longStatus += "    > Overridden by:\n       {}\n".format(overrideText)
            return True, status, longStatus
        return False, '', ''
