# Bob build tool
# Copyright (C) 2017  TechniSat Digital GmbH
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

from .errors import ParseError
from collections.abc import MutableMapping
from types import MappingProxyType
import fnmatch
import re

def checkGlobList(name, allowed):
    if allowed is None: return True
    ok = False
    for pred in allowed: ok = pred(ok, name)
    return ok

def isFalse(val):
    return val.strip().lower() in [ "", "0", "false" ]

def isTrue(val):
    return not isFalse(val)

class StringParser:
    """Utility class for complex string parsing/manipulation"""

    def __init__(self, env, funs, funArgs, nounset):
        self.env = env
        self.funs = funs
        self.funArgs = funArgs
        self.nounset = nounset

    def parse(self, text):
        """Parse the text and make substitutions"""
        if all((c not in text) for c in '\\\"\'$'):
            return text
        else:
            self.text = text
            self.index = 0
            self.end = len(text)
            return self.getString()

    def nextChar(self):
        """Get next character"""
        i = self.index
        if i >= self.end:
            raise ParseError('Unexpected end of string')
        self.index += 1
        return self.text[i:i+1]

    def nextToken(self, extra=None):
        delim=['\"', '\'', '$']
        if extra: delim.extend(extra)

        # EOS?
        i = start = self.index
        if i >= self.end:
            return None

        # directly on delimiter?
        if self.text[i] in delim:
            self.index = i+1
            return self.text[i]

        # scan
        tok = []
        while i < self.end:
            if self.text[i] in delim: break
            if self.text[i] == '\\':
                tok.append(self.text[start:i])
                start = i = i + 1
                if i >= self.end:
                    raise ParseError("Unexpected end after escape")
            i += 1
        tok.append(self.text[start:i])
        self.index = i
        return "".join(tok)

    def getSingleQuoted(self):
        i = self.index
        while i < self.end:
            if self.text[i] == "'":
                i += 1
                break
            i += 1
        if i >= self.end:
            raise ParseError("Missing closing \"'\"")
        ret = self.text[self.index:i-1]
        self.index = i
        return ret

    def getString(self, delim=[None], keep=False):
        s = []
        tok = self.nextToken(delim)
        while tok not in delim:
            if tok == '"':
                s.append(self.getString(['"']))
            elif tok == '\'':
                s.append(self.getSingleQuoted())
            elif tok == '$':
                tok = self.nextChar()
                if tok == '{':
                    s.append(self.getVariable())
                elif tok == '(':
                    s.append(self.getCommand())
                else:
                    raise ParseError("Invalid $-subsitituion")
            elif tok == None:
                if None not in delim:
                    raise ParseError('Unexpected end of string')
                break
            else:
                s.append(tok)
            tok = self.nextToken(delim)
        else:
            if keep: self.index -= 1
        return "".join(s)

    def getVariable(self):
        # get variable name
        varName = self.getString([':', '-', '+', '}'], True)

        # process?
        op = self.nextChar()
        unset = varName not in self.env
        if op == ':':
            # or null...
            if not unset: unset = self.env[varName] == ""
            op = self.nextChar()

        if op == '-':
            default = self.getString(['}'])
            if unset:
                return default
            else:
                return self.env[varName]
        elif op == '+':
            alternate = self.getString(['}'])
            if unset:
                return ""
            else:
                return alternate
        elif op == '}':
            if varName not in self.env:
                if self.nounset:
                    raise ParseError("Unset variable: " + varName)
                else:
                    return ""
            return self.env[varName]
        else:
            raise ParseError("Unterminated variable: " + str(op))

    def getCommand(self):
        words = []
        delim = [",", ")"]
        while True:
            word = self.getString(delim, True)
            words.append(word)
            end = self.nextChar()
            if end == ")": break

        if len(words) < 1:
            raise ParseError("Expected function name")
        cmd = words[0]
        del words[0]

        if cmd not in self.funs:
            raise ParseError("Unknown function: "+cmd)

        return self.funs[cmd](words, env=self.env, **self.funArgs)


class Env(MutableMapping):
    def __init__(self, other={}):
        self.data = dict(other)
        self.funs = []
        self.funArgs = {}
        self.touched = [ set() ]

    def __touch(self, key):
        for i in self.touched: i.add(key)

    def __contains__(self, key):
        self.__touch(key)
        return key in self.data

    def __delitem__(self, key):
        del self.data[key]

    def __eq__(self, other):
        if isinstance(other, Env):
            return self.data == other.data
        else:
            return self.data == other

    def __getitem__(self, key):
        self.__touch(key)
        return self.data[key]

    def __iter__(self):
        raise NotImplementedError("iter() not supported")

    def __len__(self):
        return len(self.data)

    def __ne__(self, other):
        if isinstance(other, Env):
            return self.data != other.data
        else:
            return self.data != other

    def __setitem__(self, key, value):
        self.data[key] = value

    def clear(self):
        self.data.clear()

    def copy(self):
        ret = Env(self.data)
        ret.funs = self.funs
        ret.funArgs = self.funArgs
        ret.touched = self.touched
        return ret

    def get(self, key, default=None):
        self.__touch(key)
        return self.data.get(key, default)

    def items(self):
        raise NotImplementedError("items() not supported")

    def keys(self):
        raise NotImplementedError("keys() not supported")

    def pop(self, key, default=None):
        raise NotImplementedError("pop() not supported")

    def popitem(self):
        raise NotImplementedError("popitem() not supported")

    def update(self, other):
        self.data.update(other)

    def values(self):
        raise NotImplementedError("values() not supported")

    def derive(self, overrides = {}):
        ret = self.copy()
        ret.data.update(overrides)
        return ret

    def detach(self):
        return self.data.copy()

    def inspect(self):
        return MappingProxyType(self.data)

    def setFuns(self, funs):
        self.funs = funs

    def setFunArgs(self, funArgs):
        self.funArgs = funArgs

    def prune(self, allowed):
        if allowed is None:
            return self.copy()
        else:
            ret = Env()
            ret.data = { key : self.data[key] for key in (set(self.data.keys()) & allowed) }
            ret.funs = self.funs
            ret.funArgs = self.funArgs
            ret.touched = self.touched
            return ret

    def filter(self, allowed):
        if allowed is None:
            return self.copy()
        else:
            ret = Env()
            ret.data = { key : value for (key, value) in self.data.items()
                if checkGlobList(key, allowed) }
            ret.funs = self.funs
            ret.funArgs = self.funArgs
            ret.touched = self.touched
            return ret

    def substitute(self, value, prop, nounset=True):
        try:
            return StringParser(self, self.funs, self.funArgs, nounset).parse(value)
        except ParseError as e:
            raise ParseError("Error substituting {}: {}".format(prop, str(e.slogan)))

    def evaluate(self, condition, prop):
        if condition is None:
            return True

        s = self.substitute(condition, "condition on "+prop)
        return not isFalse(s)

    def touchReset(self):
        self.touched = self.touched + [ set() ]

    def touch(self, keys):
        for k in keys: self.__touch(k)

    def touchedKeys(self):
        return self.touched[-1]


def funEqual(args, **options):
    if len(args) != 2: raise ParseError("eq expects two arguments")
    return "true" if (args[0] == args[1]) else "false"

def funNotEqual(args, **options):
    if len(args) != 2: raise ParseError("ne expects two arguments")
    return "true" if (args[0] != args[1]) else "false"

def funNot(args, **options):
    if len(args) != 1: raise ParseError("not expects one argument")
    return "true" if isFalse(args[0]) else "false"

def funOr(args, **options):
    for arg in args:
        if not isFalse(arg):
            return "true"
    return "false"

def funAnd(args, **options):
    for arg in args:
        if isFalse(arg):
            return "false"
    return "true"

def funMatch(args, **options):
    try:
        [2, 3].index(len(args))
    except ValueError:
        raise ParseError("match expects either two or three arguments")

    flags = 0
    if len(args) == 3:
        if args[2] == 'i':
            flags = re.IGNORECASE
        else:
            raise ParseError('match only supports the ignore case flag "i"')

    if re.search(args[1],args[0],flags):
        return "true"
    else:
        return "false"

def funIfThenElse(args, **options):
    if len(args) != 3: raise ParseError("if-then-else expects three arguments")
    if isFalse(args[0]):
        return args[2]
    else:
        return args[1]

def funSubst(args, **options):
    if len(args) != 3: raise ParseError("subst expects three arguments")
    return args[2].replace(args[0], args[1])

def funStrip(args, **options):
    if len(args) != 1: raise ParseError("strip expects one argument")
    return args[0].strip()

def funSandboxEnabled(args, sandbox, **options):
    if len(args) != 0: raise ParseError("is-sandbox-enabled expects no arguments")
    return "true" if ((sandbox is not None) and sandbox.isEnabled()) else "false"

def funToolDefined(args, tools, **options):
    if len(args) != 1: raise ParseError("is-tool-defined expects one argument")
    return "true" if (args[0] in tools) else "false"

def funMatchScm(args, **options):
    if len(args) != 2: raise ParseError("matchScm expects two arguments")
    name = args[0]
    val = args[1]
    try:
        pkg = options['package']
    except KeyError:
        raise ParseError('matchScm can only be used for queries')

    for scm in pkg.getCheckoutStep().getScmList():
        for props in scm.getProperties():
            if fnmatch.fnmatchcase(props.get(name), val): return "true"

    return "false"

DEFAULT_STRING_FUNS = {
    "eq" : funEqual,
    "or" : funOr,
    "and" : funAnd,
    "if-then-else" : funIfThenElse,
    "is-sandbox-enabled" : funSandboxEnabled,
    "is-tool-defined" : funToolDefined,
    "ne" : funNotEqual,
    "not" : funNot,
    "strip" : funStrip,
    "subst" : funSubst,
    "match" : funMatch,
    "matchScm" : funMatchScm,
}
