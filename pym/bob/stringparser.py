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
pyparsing.ParserElement.enable_packrat()

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

class _Literal:
    """Constant string chunk. Never needs substitution."""
    __slots__ = ('text',)

    def __init__(self, text):
        self.text = text

    def eval(self, env, funs, funArgs, nounset):
        return self.text

class _Concat:
    """Sequence of parts that are evaluated and joined."""
    __slots__ = ('parts',)

    def __init__(self, parts):
        self.parts = parts

    def eval(self, env, funs, funArgs, nounset):
        return "".join(p.eval(env, funs, funArgs, nounset) for p in self.parts)

class _VarBare:
    """Bare '$name' variable reference."""
    __slots__ = ('name',)

    def __init__(self, name):
        self.name = name

    def eval(self, env, funs, funArgs, nounset):
        varValue = env.get(self.name)
        if varValue is None:
            if nounset:
                raise ParseError("Unset variable: " + self.name)
            return ""
        else:
            return varValue

class _VarBraced:
    """'${name}', '${name:-x}', '${name-x}', '${name:+x}' or '${name+x}'."""
    __slots__ = ('nameNode', 'hasColon', 'sign', 'argNode')

    def __init__(self, nameNode, hasColon, sign, argNode):
        self.nameNode = nameNode
        self.hasColon = hasColon
        self.sign = sign
        self.argNode = argNode

    def eval(self, env, funs, funArgs, nounset):
        varName = self.nameNode.eval(env, funs, funArgs, nounset)
        unset = varName not in env
        if self.hasColon:
            # or null...
            if not unset: unset = env[varName] == ""

        if self.sign == '-':
            if unset:
                return self.argNode.eval(env, funs, funArgs, nounset)
            else:
                return env[varName]
        elif self.sign == '+':
            if unset:
                return ""
            else:
                return self.argNode.eval(env, funs, funArgs, nounset)
        else:
            if varName not in env:
                if nounset:
                    raise ParseError("Unset variable: " + varName)
                else:
                    return ""
            return env[varName]

class _Command:
    """'$(func,arg1,arg2)' string function call."""
    __slots__ = ('wordNodes',)

    def __init__(self, wordNodes):
        self.wordNodes = wordNodes

    def eval(self, env, funs, funArgs, nounset):
        words = [ w.eval(env, funs, funArgs, nounset) for w in self.wordNodes ]

        if len(words) < 1:
            raise ParseError("Expected function name")
        cmd = words[0]
        del words[0]

        if cmd not in funs:
            raise ParseError("Unknown function: "+cmd)

        return funs[cmd](words, env=env, **funArgs)


class _Tokenizer:
    """Parses text into a tree of substitution nodes.

    This only depends on the text itself, never on the environment, the
    available string functions or the 'subst'/'nounset' evaluation mode.
    That makes the resulting tree reusable (see '_parseTemplate' below) no
    matter with which environment the text is eventually evaluated -- the
    same recipe template is typically substituted many times with different
    environments (once per package variant), so caching the (potentially
    expensive) tokenization step and only repeating the (cheap) evaluation
    gives a substantial speedup.
    """

    __slots__ = ('text', 'index', 'end')

    def __init__(self, text):
        self.text = text
        self.index = 0
        self.end = len(text)

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

    def parseString(self, delim=[None], keep=False):
        """Parse string from current parsing position into a node tree.

        Parses until either the string ends or hits one of the additional
        delimiters.

        :param delim: Additional delimiter characters where parsing should stop
        :param keep: Keep the additional delimiter if hit. By default the
                     delimier is swallowed.
        """
        s = []
        tok = self.nextToken(delim)
        while tok not in delim:
            if tok == '"':
                s.append(self.parseString(['"'], False))
            elif tok == '\'':
                s.append(_Literal(self.getSingleQuoted()))
            elif tok == '$':
                tok = self.nextChar()
                if tok == '{':
                    s.append(self.parseVariable())
                elif tok == '(':
                    s.append(self.parseCommand())
                elif tok in NAME_START:
                    s.append(self.parseBareVariable(tok))
                else:
                    raise ParseError("Invalid $-subsitituion")
            elif tok == None:
                if None not in delim:
                    raise ParseError('Unexpected end of string')
                break
            else:
                s.append(_Literal(tok))
            tok = self.nextToken(delim)
        else:
            if keep: self.index -= 1
        if len(s) == 1:
            return s[0]
        return _Concat(s)

    def parseVariable(self):
        """Parse variable reference at current position."""
        # get variable name
        nameNode = self.parseString([':', '-', '+', '}'], True)

        # process?
        op = self.nextChar()
        hasColon = False
        if op == ':':
            hasColon = True
            op = self.nextChar()

        if op == '-':
            argNode = self.parseString(['}'], False)
            return _VarBraced(nameNode, hasColon, '-', argNode)
        elif op == '+':
            argNode = self.parseString(['}'], False)
            return _VarBraced(nameNode, hasColon, '+', argNode)
        elif op == '}':
            return _VarBraced(nameNode, hasColon, None, None)
        else:
            raise ParseError("Unterminated variable: " + str(op))

    def parseBareVariable(self, varName):
        """Parse bare variable at current position.

        :param varName: Initial character of variable name
        """
        varName += self.getRestOfName()
        return _VarBare(varName)

    def parseCommand(self):
        """Parse string function call at current position."""
        wordNodes = []
        delim = [",", ")"]
        while True:
            wordNodes.append(self.parseString(delim, True))
            end = self.nextChar()
            if end == ")": break

        return _Command(wordNodes)


# Cache of parsed templates, keyed by the literal text. Parsing only depends
# on the text (see '_Tokenizer'), so the same tree can be reused regardless
# of the environment it is evaluated with. Only successfully parsed texts are
# cached; a malformed text simply fails to parse again the same way on the
# next attempt.
_parseCache = {}

def _parseTemplate(text):
    node = _parseCache.get(text)
    if node is None:
        node = _Tokenizer(text).parseString()
        _parseCache[text] = node
    return node

class StringParser:
    """Utility class for complex string parsing/manipulation"""

    __slots__ = ('env', 'funs', 'funArgs', 'nounset')

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
            node = _parseTemplate(text)
            return node.eval(self.env, self.funs, self.funArgs, self.nounset)

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
        sQStringLiteral.set_parse_action(
            lambda s, loc, toks: StringLiteral(s, loc, toks, False))

        dQStringLiteral = pyparsing.QuotedString('"', '\\')
        dQStringLiteral.set_parse_action(
            lambda s, loc, toks: StringLiteral(s, loc, toks, True))

        stringLiteral = sQStringLiteral | dQStringLiteral

        functionCall = pyparsing.Forward()
        functionArg = stringLiteral | functionCall
        functionCall << pyparsing.Word(pyparsing.alphas, pyparsing.alphanums+'-') + \
            pyparsing.Suppress('(') + \
            pyparsing.Optional(functionArg +
                pyparsing.ZeroOrMore(pyparsing.Suppress(',') + functionArg)) + \
            pyparsing.Suppress(')')
        functionCall.set_parse_action(
            lambda s, loc, toks: FunctionCall(s, loc, toks))

        predExpr = pyparsing.infix_notation(
            stringLiteral ^ functionCall ,
            [
                ('!',  1, pyparsing.opAssoc.RIGHT, lambda s, loc, toks: NotOperator(s, loc, toks)),
                (pyparsing.one_of('< <= > >='), 2, pyparsing.opAssoc.LEFT, infixBinaryOp(BinaryStrOperator)),
                (pyparsing.one_of('== !='), 2, pyparsing.opAssoc.LEFT, infixBinaryOp(BinaryStrOperator)),
                ('&&', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator)),
                ('||', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator))
            ])

        self.__ifgrammer = predExpr

    def parseExpression(self, expression):
        try:
            ret = self.__ifgrammer.parse_string(expression, True)
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

    # The touched sets form a stack where each set is a superset of all sets
    # that were pushed after it. Hence we can stop as soon as a set already
    # contains the key.
    def __touch(self, key):
        for i in reversed(self.touched):
            if key in i: break
            i.add(key)

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
        for i in reversed(self.touched):
            keys = keys - i
            if not keys: break
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

    try:
        if re.search(args[1],args[0],flags):
            return "true"
        else:
            return "false"
    except re.error as e:
        raise ParseError("Invalid $(match) regex '{}': {}".format(e.pattern, e))

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

def funResubst(args, **options):
    try:
        [3, 4].index(len(args))
    except ValueError:
        raise ParseError("$(resubst) expects either three or four arguments")

    flags = 0
    if len(args) == 4:
        if args[3] == 'i':
            flags = re.IGNORECASE
        else:
            raise ParseError('$(resubst) only supports the ignore case flag "i"')

    try:
        return re.sub(args[0], args[1], args[2], flags=flags)
    except re.error as e:
        raise ParseError("Invalid $(resubst) regex '{}': {}".format(e.pattern, e))

# Attention: do *not* add any new functions here. That will break existing
# plugins that define a function with the same name. Use EXTRA_STRING_FUNS for
# new functions instead.
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

EXTRA_STRING_FUNS = {
    "resubst" : funResubst,
}
