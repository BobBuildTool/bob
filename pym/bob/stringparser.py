# Bob build tool
# Copyright (C) 2017  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import ParseError
from .tty import WarnOnce
from .utils import infixBinaryOp
from collections.abc import MutableMapping
from types import MappingProxyType
import fnmatch
import pyparsing
import re

# need to enable this for nested expression parsing performance
pyparsing.ParserElement.enablePackrat()

NAME_START = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz'
NAME_CHARS = NAME_START + '0123456789'

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

    __slots__ = ('env', 'funs', 'funArgs', 'nounset', 'text', 'index', 'end')

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

    def getRestOfName(self):
        """Get remainder of bare variable name"""
        ret = ''
        i = self.index
        while i < self.end:
            c = self.text[i]
            if c not in NAME_CHARS: break
            ret += c
            i += 1

        self.index = i
        return ret

    def getSingleQuoted(self):
        """Get remainder of single quoted string."""
        i = self.index
        while i < self.end:
            if self.text[i] == "'":
                break
            i += 1
        if i >= self.end:
            raise ParseError("Missing closing \"'\"")
        ret = self.text[self.index:i]
        self.index = i+1
        return ret

    def getString(self, delim=[None], keep=False, subst=True):
        """Interpret as string from current parsing position.

        Do any necessary substitutions until either the string ends or hits one
        of the additional delimiters.

        :param delim: Additional delimiter characters where parsing should stop
        :param keep: Keep the additional delimiter if hit. By default the
                     delimier is swallowed.
        :param subst: Do variable or command substitution. If false, skip over
                      such substitutions.
        """
        s = []
        tok = self.nextToken(delim)
        while tok not in delim:
            if tok == '"':
                s.append(self.getString(['"'], False, subst))
            elif tok == '\'':
                s.append(self.getSingleQuoted())
            elif tok == '$':
                tok = self.nextChar()
                if tok == '{':
                    s.append(self.getVariable(subst))
                elif tok == '(':
                    s.append(self.getCommand(subst))
                elif tok in NAME_START:
                    s.append(self.getBareVariable(tok, subst))
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

    def getVariable(self, subst):
        """Substitute variable at current position.

        :param subst: Bail out if substitution fails?
        """
        # get variable name
        varName = self.getString([':', '-', '+', '}'], True, subst)

        # process?
        op = self.nextChar()
        unset = varName not in self.env
        if op == ':':
            # or null...
            if not unset: unset = self.env[varName] == ""
            op = self.nextChar()

        if op == '-':
            default = self.getString(['}'], False, subst and unset)
            if unset:
                return default
            else:
                return self.env[varName]
        elif op == '+':
            alternate = self.getString(['}'], False, subst and not unset)
            if unset:
                return ""
            else:
                return alternate
        elif op == '}':
            if varName not in self.env:
                if subst and self.nounset:
                    raise ParseError("Unset variable: " + varName)
                else:
                    return ""
            return self.env[varName]
        else:
            raise ParseError("Unterminated variable: " + str(op))

    def getBareVariable(self, varName, subst):
        """Substitute base variable at current position.

        :param varName: Initial character of variable name
        :param subst: Bail out if substitution fails?
        """
        varName += self.getRestOfName()
        varValue = self.env.get(varName)
        if varValue is None:
            if subst and self.nounset:
                raise ParseError("Unset variable: " + varName)
            return ""
        else:
            return varValue

    def getCommand(self, subst):
        """Substitute string function at current position.

        :param subst: Actually call function or just skip?
        """
        words = []
        delim = [",", ")"]
        while True:
            word = self.getString(delim, True, subst)
            words.append(word)
            end = self.nextChar()
            if end == ")": break

        if not subst:
            return ""

        if len(words) < 1:
            raise ParseError("Expected function name")
        cmd = words[0]
        del words[0]

        if cmd not in self.funs:
            raise ParseError("Unknown function: "+cmd)

        return self.funs[cmd](words, env=self.env, **self.funArgs)

class IfExpression():
    __slots__ = ('__expr')

    def __init__(self, expr):
        self.__expr = IfExpressionParser.getInstance().parseExpression(expr)

    def __eq__(self, other):
        return isinstance(other, IfExpression) and self.__expr == other.__expr

    def __lt__(self, other): return NotImplemented
    def __le__(self, other): return NotImplemented
    def __gt__(self, other): return NotImplemented
    def __ge__(self, other): return NotImplemented

    def __str__(self):
        return str(self.__expr)

    def evalExpression(self, env):
        return self.__expr.evalExpression(env)

OPS = {
    '&&' : lambda l, r: l & r,
    '||' : lambda l, r: l | r,
    '<'  : lambda l, r: l < r,
    '>'  : lambda l, r: l > r,
    '<=' : lambda l, r: l <= r,
    '>=' : lambda l, r: l >= r,
    '==' : lambda l, r: l == r,
    '!=' : lambda l, r: l != r,
}

class NotOperator():
    __slots__ = ('op')

    def __init__(self, s, loc, toks):
        assert len(toks) == 1, toks
        toks = toks[0]
        assert len(toks) == 2, toks
        assert toks[0] == '!'
        self.op = toks[1]

    def __eq__(self, other):
        return isinstance(other, NotOperator) and self.op == other.op

    def __str__(self):
        return "!({})".format(self.op)

    def evalExpression(self, env):
        return not self.op.evalExpression(env)

class BinaryBoolOperator():
    __slots__ = ('op', 'left', 'right')

    def __init__(self, s, loc, toks):
        self.left = toks[0]
        self.right = toks[2]
        self.op = toks[1]

    def __eq__(self, other):
        return isinstance(other, BinaryBoolOperator) and \
            self.op == other.op and \
            self.left == other.left and self.right == other.right

    def __str__(self):
        return "({}) {} ({})".format(self.left, self.op, self.right)

    def evalExpression(self, env):
        return OPS[self.op](self.left.evalExpression(env),
                            self.right.evalExpression(env))

class StringLiteral():
    __slots__ = ('literal', 'subst')

    def __init__(self, s, loc, toks, doSubst):
        assert len(toks) == 1, toks
        self.literal = toks[0]
        self.subst = doSubst and any((c in self.literal) for c in '\\\"\'$')

    def __eq__(self, other):
        return isinstance(other, StringLiteral) and self.literal == other.literal

    def __str__(self):
        return '"' + self.literal + '"'

    def evalExpressionToString(self, env):
        if self.subst:
            return env.substitute(self.literal, self.literal, False)
        else:
            return self.literal

    def evalExpression(self, env):
        return isTrue(self.evalExpressionToString(env))

class FunctionCall():
    __slots__ = ('name', 'args')

    def __init__(self, s, loc, toks):
        self.name = toks[0]
        self.args = toks[1:]

    def __eq__(self, other):
        return isinstance(other, FunctionCall) and \
            self.name == other.name and self.args == other.args

    def __str__(self):
        return "{}({})".format(self.name,
            ", ".join(str(a) for a in self.args))

    def evalExpressionToString(self, env):
        extra = env.funArgs
        args = [ a.evalExpressionToString(env) for a in self.args ]
        if self.name not in env.funs:
            raise ParseError("Bad syntax: " + "Unknown string function: "\
                    + self.name)
        fun = env.funs[self.name]
        return fun(args, env=env, **extra)

    def evalExpression(self, env):
        return isTrue(self.evalExpressionToString(env))

class BinaryStrOperator():
    __slots__ = ('op', 'opStr', 'left', 'right')

    def __init__(self, s, loc, toks):
        self.left = toks[0]
        self.right = toks[2]
        self.op = toks[1]

    def __eq__(self, other):
        return isinstance(other, BinaryStrOperator) and \
            self.op == other.op and \
            self.left == other.left and self.right == other.right

    def __str__(self):
        return "({}) {} ({})".format(self.left, self.op, self.right)

    def evalExpression(self, env):
        return OPS[self.op](self.left.evalExpressionToString(env),
                            self.right.evalExpressionToString(env))

class IfExpressionParser:
    __instance = None

    def __init__(self):
        # create parsing grammer
        sQStringLiteral = pyparsing.QuotedString("'")
        sQStringLiteral.setParseAction(
            lambda s, loc, toks: StringLiteral(s, loc, toks, False))

        dQStringLiteral = pyparsing.QuotedString('"', '\\')
        dQStringLiteral.setParseAction(
            lambda s, loc, toks: StringLiteral(s, loc, toks, True))

        stringLiteral = sQStringLiteral | dQStringLiteral

        functionCall = pyparsing.Forward()
        functionArg = stringLiteral | functionCall
        functionCall << pyparsing.Word(pyparsing.alphas, pyparsing.alphanums+'-') + \
            pyparsing.Suppress('(') + \
            pyparsing.Optional(functionArg +
                pyparsing.ZeroOrMore(pyparsing.Suppress(',') + functionArg)) + \
            pyparsing.Suppress(')')
        functionCall.setParseAction(
            lambda s, loc, toks: FunctionCall(s, loc, toks))

        predExpr = pyparsing.infixNotation(
            stringLiteral ^ functionCall ,
            [
                ('!',  1, pyparsing.opAssoc.RIGHT, lambda s, loc, toks: NotOperator(s, loc, toks)),
                ('<',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('<=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('>',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('>=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('==', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('!=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator)),
                ('&&', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator)),
                ('||', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator))
            ])

        self.__ifgrammer = predExpr

    def parseExpression(self, expression):
        try:
            ret = self.__ifgrammer.parseString(expression, True)
        except pyparsing.ParseBaseException as e:
            raise ParseError("Invalid syntax: " + str(e))
        return ret[0]

    @classmethod
    def getInstance(cls):
        if cls.__instance is None:
            cls.__instance = IfExpressionParser()
        return cls.__instance

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

        if isinstance(condition, IfExpression):
            return condition.evalExpression(self)

        s = self.substitute(condition, "condition on "+prop)
        return not isFalse(s)

    def substituteCondDict(self, values, prop, nounset=True):
        try:
            return { key : self.substitute(value, key, nounset)
                     for key, (value, condition) in values.items()
                     if self.evaluate(condition, key) }
        except ParseError as e:
            raise ParseError(f"{prop}: {e.slogan}")

    def touchReset(self):
        self.touched = self.touched + [ set() ]

    def touch(self, keys):
        for i in self.touched:
            i.update(keys)

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
    return "true" if sandbox else "false"

def funToolDefined(args, __tools, **options):
    if len(args) != 1: raise ParseError("is-tool-defined expects one argument")
    return "true" if (args[0] in __tools) else "false"

def funToolEnv(args, __tools, **options):
    l = len(args)
    if l == 2:
        tool, var = args
        default = None
    elif l == 3:
        tool, var, default = args
    else:
        raise ParseError("get-tool-env expects two or three arguments")

    try:
        env = __tools[tool].environment
    except KeyError:
        raise ParseError("get-tool-env: tool '{}' undefined".format(tool))

    ret = env.get(var, default)
    if ret is None:
        raise ParseError("get-tool-env: undefined variable '{}' in tool '{}'".format(var, tool))

    return ret

def funMatchScm(args, **options):
    if len(args) != 2: raise ParseError("matchScm expects two arguments")
    name = args[0]
    val = args[1]
    try:
        pkg = options['package']
    except KeyError:
        raise ParseError('matchScm can only be used for queries')

    for scm in pkg.getCheckoutStep().getScmList():
        prop = scm.getProperties(False).get(name)
        if isinstance(prop, str):
            if fnmatch.fnmatchcase(str(prop), val): return "true"
        elif isinstance(prop, bool):
            # Need to compare bool before int because bool is a subclass of int
            if isTrue(val) == prop: return "true"
        elif isinstance(prop, int):
            if prop == int(val, 0): return "true"

    return "false"

DEFAULT_STRING_FUNS = {
    "eq" : funEqual,
    "or" : funOr,
    "and" : funAnd,
    "if-then-else" : funIfThenElse,
    "is-sandbox-enabled" : funSandboxEnabled,
    "is-tool-defined" : funToolDefined,
    "get-tool-env" : funToolEnv,
    "ne" : funNotEqual,
    "not" : funNot,
    "strip" : funStrip,
    "subst" : funSubst,
    "match" : funMatch,
    "matchScm" : funMatchScm,
}
