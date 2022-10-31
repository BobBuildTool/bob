# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BobError
from .stringparser import isFalse, isTrue, Env
from .utils import infixBinaryOp
from collections import OrderedDict
from itertools import chain
from fnmatch import fnmatchcase
from functools import lru_cache
import pickle
import pyparsing
import sqlite3

# need to enable this for nested expression parsing performance
pyparsing.ParserElement.enablePackrat()

# See "Efficient algorithms for processing XPath queries" [1] for the core
# algorithms that are applied here.
#
# [1] Gottlob, G., Koch, C., and Pichler, R. 2002. Efficient algorithms
#     for processing XPath queries. In Proceedings of the 28th
#     International Conference on Very Large Data Bases (VLDB'02).
#     HongKong, China, 95-106


class SandboxSentinel:
    def isEnabled(self):
        return False
sandboxSentinel = SandboxSentinel()

def markLocation(line, loc):
    if len(line) <= 60:
        # line is short enough
        pass
    elif loc <= 30:
        # error at beginning
        line = line[:60] + '[...]'
    elif loc >= (len(line) - 30):
        # error at end
        loc = 60 - (len(line) - loc) + 5
        line = '[...]' + line[-60:]
    else:
        # error in middle
        line = '[...]' + line[loc-30:loc+30] + '[...]'
        loc = 35

    return "Offending query: " + line + "\n" + (" " * (loc + 17)) + \
        "^.-- Error location"

class BaseASTNode:
    def __init__(self, s, loc, precedence=1000):
        self.__s = s
        self.__loc = loc
        self.precedence = precedence

    def barf(self, msg):
        raise BobError("Bad syntax: " + msg + " (at char {})".format(self.__loc),
                       help=markLocation(self.__s, self.__loc))

class LocationPath(BaseASTNode):
    """AST class that represents a 'location path'"""

    def __init__(self, s, loc, toks, rootNodeGetter):
        super().__init__(s, loc)
        self.__path = [
            (LocationStep(s, loc, ['descendant-or-self', '@', '*']) if t == "//" else t)
            for t in toks
            if t != "/"
        ]
        self.__absolute = (toks[0] == '/') or (toks[0] == '//')
        self.__rootNodeGetter = rootNodeGetter

        # remove trivial 'self' steps
        self.__path = [ s for s in self.__path
            if (s.axis != 'self' or s.test != '*' or s.pred is not None) ]

        # combine '//foo' to 'descendant@foo'
        # FIXME: this looks ugly as hell. there's certainly a better way
        path = []
        i = 0
        while i < len(self.__path):
            step = self.__path[i]
            if i < len(self.__path)-1:
                nextStep = self.__path[i+1]
                if (step.axis == 'descendant-or-self' and step.test == '*' and
                        step.pred is None and nextStep.axis == 'child'):
                    nextStep.axis = 'descendant'
                    step = nextStep
                    i += 1
            path.append(step)
            i += 1
        self.__path = path

    def __str__(self):
        ret = "/" if self.__absolute else ""
        ret += "/".join(str(p) for p in self.__path)
        return ret

    def __repr__(self):
        return "LocationPath({})".format(self.__path)

    def __findIntermediateNodes(self, old, new, queryIndirect):
        """Find nodes that are on on any path between 'old' and 'new'"""

        visited = set()
        intermediate = set()
        if old.issuperset(new): return intermediate

        def traverse(node, stack):
            if node in visited: return

            if node in new:
                intermediate.update(stack)
            else:
                stack = stack + [node]
                for i in node.values():
                    if queryIndirect or i.direct:
                        traverse(i.node, stack)
                visited.add(node)

        for n in old: traverse(n, [])

        return intermediate

    def __findReachableSubset(self, valid, nodes):
        """Find all nodes and their parents that are in the valid set."""

        ret = set()
        todo = set(nodes)
        while todo:
            node = todo.pop()
            if (node not in valid) or (node in ret): continue
            ret.add(node)
            todo.update(node.parents(True))

        return ret

    def evalForward(self, root, emptyMode):
        """Evaluate path step by step.

        Each step is performed with the set of context nodes that represent the
        (intermediate) result.  While each step is evaluated the trail of
        visited nodes is recorded in 'valid'. After each step these nodes are
        trimmed by the parent-reachable nodes of the current context nodes.

        The 'valid' set is returned to limit the possible paths of the result
        nodes to what was visited during the path traversal. Even though any
        path would technically be correct the user intuitively expects the
        resulting paths to reflect the intermediate steps of the query.
        """
        nodes = set([root])
        valid = set([root])
        wasComplex = False
        stack = []
        for i in self.__path:
            oldNodes = nodes
            nodes, search, complexQuery = i.evalForward(nodes, valid)
            wasComplex = wasComplex or complexQuery
            stack.append(i)
            if not nodes and emptyMode != "nullset":
                if not wasComplex:
                    raise BobError("Package '/{}' not found".format("/".join(str(s) for s in stack)))
                elif emptyMode == "nullfail":
                    raise BobError("Query '/{}' matched no packages".format("/".join(str(s) for s in stack)))
            if search is not None:
                valid.update(self.__findIntermediateNodes(oldNodes, nodes, search))
            else:
                valid.update(nodes)
            valid.update(nodes)
            valid.intersection_update(self.__findReachableSubset(valid, nodes))
        return (nodes, valid)

    def evalBackward(self):
        """Evaluate a path backwards.

        Used in predicate expressions to find all nodes that possibly match the
        path. IOW we backwards find all nodes that satisfy the path.
        """
        root = self.__rootNodeGetter()
        allNodes = nodes = set(root.allNodes())
        for i in reversed(self.__path):
            nodes = i.evalBackward(nodes)
        if self.__absolute:
            if root in nodes:
                nodes = allNodes
            else:
                nodes = set()
        return nodes

    def evalString(self, pkg):
        """A path cannot be treated as a string"""
        self.barf("location path in string context")

class LocationStep(BaseASTNode):
    """AST class that represents a single step in a location path."""

    def __init__(self, s, loc, step):
        """Constructor.

        Handles the various abbreviations of steps like the optional axis, the
        optional predicate and the '.' short-cut.
        """
        super().__init__(s, loc)
        self.__pred = None
        if len(step) == 1:
            if step[0] == '.':
                self.__axis = 'self'
                self.__test = '*'
            else:
                self.__axis = 'child'
                self.__test = step[0]
        else:
            if step[1] == '@':
                self.__axis = step[0]
                self.__test = step[2]
                remain = step[3:]
            else:
                self.__axis = 'child'
                self.__test = step[0]
                remain = step[1:]

            if remain:
                assert remain[0] == '['
                assert remain[2] == ']'
                self.__pred = remain[1]

    def __str__(self):
        if self.__pred is None:
            if self.__axis == 'self' and self.__test == '*':
                return "."
            elif self.__axis == 'child':
                return self.__test

        if self.__axis == 'child':
            ret = self.__test
        else:
            ret = self.__axis + "@" + self.__test

        if self.__pred is not None:
            ret += "[" + str(self.__pred) + "]"

        return ret

    def __repr__(self):
        return "LocationStep({}@{}[{}])".format(self.__axis, self.__test, self.__pred)

    def __evalAxisChild(self, nodes, queryIndirect):
        """Find nodes in the 'child' axis."""
        ret = set()
        for i in nodes:
            ret.update(c.node for c in i.values() if (queryIndirect or c.direct))
        return ret

    def __evalAxisDescendant(self, nodes, queryIndirect):
        """Find nodes in the 'descendant' axis."""
        ret = set()
        todo = nodes
        while todo:
            childs = set()
            for i in todo:
                childs.update(c.node for c in i.values()
                              if (queryIndirect or c.direct))
            todo = childs - ret
            ret.update(childs)
        return ret

    def __evalAxisParent(self, nodes, queryIndirect):
        """Find nodes in the 'parent' axis."""
        ret = set()
        for i in nodes:
            ret.update(i.parents(queryIndirect))
        return ret

    def __evalAxisAncestor(self, nodes, queryIndirect):
        """Find nodes in the 'ancestor' axis."""
        ret = set()
        todo = nodes
        while todo:
            parents = set()
            for i in todo: parents.update(i.parents(queryIndirect))
            todo = parents - ret
            ret.update(parents)
        return ret

    @property
    def axis(self):
        return self.__axis

    @axis.setter
    def axis(self, value):
        assert value in ['self', 'child', 'descendant', 'descendant-or-self'], value
        self.__axis = value

    @property
    def test(self):
        return self.__test

    @property
    def pred(self):
        return self.__pred

    def evalForward(self, nodes, valid):
        """Evaluate the axis, name test and predicate

        Despite the result set returns whether we possibly made multiple hops
        in the dendency graph, i.e.  evaluated a 'descendant' axis. In this
        caste it is the responsibility of the caller to calculate all possible
        paths that lead to the result set.
        """
        search = None
        if self.__axis == "child":
            nodes = self.__evalAxisChild(nodes, True)
        elif self.__axis == "descendant":
            nodes = self.__evalAxisDescendant(nodes, True)
            search = True
        elif self.__axis == "descendant-or-self":
            nodes = self.__evalAxisDescendant(nodes, True) | nodes
            search = True
        elif self.__axis == "direct-child":
            nodes = self.__evalAxisChild(nodes, False)
        elif self.__axis == "direct-descendant":
            nodes = self.__evalAxisDescendant(nodes, False)
            search = False
        elif self.__axis == "direct-descendant-or-self":
            nodes = self.__evalAxisDescendant(nodes, False) | nodes
            search = False
        elif self.__axis == "self":
            pass
        else:
            raise AssertionError("Invalid axis: " + str(self.__axis))

        if self.__test == "*":
            complexQuery = True
        elif '*' in self.__test:
            complexQuery = True
            nodes = set(i for i in nodes if fnmatchcase(i.getName(), self.__test))
        else:
            complexQuery = search is not None
            nodes = set(i for i in nodes if i.getName() == self.__test)

        if self.__pred:
            complexQuery = True
            nodes = nodes & self.__pred.evalBackward()

        return (nodes, search, complexQuery)

    def evalBackward(self, nodes):
        """Inverse evaluation of location path step."""
        if self.__test == "*":
            pass
        elif '*' in self.__test:
            nodes = set(i for i in nodes if fnmatchcase(i.getName(), self.__test))
        else:
            nodes = set(i for i in nodes if i.getName() == self.__test)

        if self.__pred:
            nodes = nodes & self.__pred.evalBackward()

        if self.__axis == "child":
            nodes = self.__evalAxisParent(nodes, True)
        elif self.__axis == "descendant":
            nodes = self.__evalAxisAncestor(nodes, True)
        elif self.__axis == "descendant-or-self":
            nodes = self.__evalAxisAncestor(nodes, True) | nodes
        elif self.__axis == "direct-child":
            nodes = self.__evalAxisParent(nodes, False)
        elif self.__axis == "direct-descendant":
            nodes = self.__evalAxisAncestor(nodes, False)
        elif self.__axis == "direct-descendant-or-self":
            nodes = self.__evalAxisAncestor(nodes, False) | nodes
        elif self.__axis == "self":
            pass
        else:
            raise AssertionError("Invalid axis: " + str(self.__axis))

        return nodes

class NotOperator(BaseASTNode):
    def __init__(self, s, loc, toks, rootNodeGetter, precedence):
        super().__init__(s, loc, precedence)
        self.rootNodeGetter = rootNodeGetter
        assert len(toks) == 1, toks
        toks = toks[0]
        assert len(toks) == 2, toks
        assert toks[0] == '!'
        self.op = toks[1]

    def __str__(self):
        if self.op.precedence < self.precedence:
            return "!({})".format(str(self.op))
        else:
            return "!" + str(self.op)

    def __repr__(self):
        return "NotOperator({})".format(self.op)

    def evalBackward(self):
        return set(self.rootNodeGetter().allNodes()) - self.op.evalBackward()

    def evalString(self, pkg):
        self.barf("operator in string context")

class BinaryBoolOperator(BaseASTNode):
    def __init__(self, s, loc, toks, precedence):
        super().__init__(s, loc, precedence)
        self.left = toks[0]
        self.right = toks[2]
        self.opStr = op = toks[1]
        if op == '&&':
            self.op = lambda l, r: l & r
        elif op == '||':
            self.op = lambda l, r: l | r
        else:
            raise AssertionError("Invalid op: " + str(op))

    def __str__(self):
        l = "({})".format(str(self.left)) if self.left.precedence < self.precedence else str(self.left)
        r = "({})".format(str(self.right)) if self.right.precedence < self.precedence else str(self.right)
        return "{} {} {}".format(l, self.opStr, r)

    def __repr__(self):
        return "BinaryBoolOperator({}, {}, {})".format(self.left, self.opStr, self.right)

    def evalBackward(self):
        return self.op(self.left.evalBackward(), self.right.evalBackward())

    def evalString(self, pkg):
        self.barf("operator in string context")

class StringLiteral(BaseASTNode):
    def __init__(self, s, loc, toks, doSubst, stringFunctions, graphIterator):
        super().__init__(s, loc)
        assert len(toks) == 1, toks
        self.literal = toks[0]
        self.subst = doSubst and any((c in self.literal) for c in '\\\"\'$')
        self.stringFunctions = stringFunctions
        self.graphIterator = graphIterator

    def __str__(self):
        return ("\"{}\"" if self.subst else "'{}'").format(self.literal)

    def __repr__(self):
        if self.subst:
            return "StringLiteral(\"{}\")".format(self.literal)
        else:
            return "StringLiteral('{}')".format(self.literal)

    def evalBackward(self):
        return set( n for (n, p) in self.graphIterator()
                    if isTrue(self.evalString(p)) )

    def evalString(self, pkg):
        if self.subst:
            pkgStep = pkg.getPackageStep()
            env = Env(pkgStep.getEnv())
            env.update(pkg.getMetaEnv())
            env.setFunArgs({
                "package" : pkg,
                "recipe" : pkg.getRecipe(),
                "sandbox" : (pkg._getSandboxRaw() or sandboxSentinel).isEnabled(),
                "__tools" : pkgStep.getTools()
            })
            env.setFuns(self.stringFunctions)
            return env.substitute(self.literal, self.literal, False)
        else:
            return self.literal

class FunctionCall(BaseASTNode):
    def __init__(self, s, loc, toks, stringFunctions, graphIterator):
        super().__init__(s, loc)
        if toks[0] not in stringFunctions:
            self.barf("Unknown string function: " + toks[0])
        self.fun = stringFunctions[toks[0]]
        self.name = toks[0]
        self.args = toks[1:]
        self.graphIterator = graphIterator

    def __str__(self):
        return "{}({})".format(self.name, ", ".join(str(a) for a in self.args))

    def __repr__(self):
        return "FunctionCall({}, {})".format(self.name,
            ", ".join(repr(a) for a in self.args))

    def evalBackward(self):
        return set( n for (n, p) in self.graphIterator()
                    if isTrue(self.evalString(p)) )

    def evalString(self, pkg):
        pkgStep = pkg.getPackageStep()
        env = Env(pkgStep.getEnv())
        env.update(pkg.getMetaEnv())
        args = [ a.evalString(pkg) for a in self.args ]
        extra = {
            "package" : pkg,
            "recipe" : pkg.getRecipe(),
            "sandbox" : (pkg._getSandboxRaw() or sandboxSentinel).isEnabled(),
            "__tools" : pkgStep.getTools()
        }
        return self.fun(args, env=env, **extra)

class BinaryStrOperator(BaseASTNode):
    def __init__(self, s, loc, toks, graphIterator, precedence):
        super().__init__(s, loc, precedence)
        self.left = toks[0]
        self.right = toks[2]
        self.opStr = op = toks[1]
        if op == '<':
            self.op = lambda l, r: l < r
        elif op == '>':
            self.op = lambda l, r: l > r
        elif op == '<=':
            self.op = lambda l, r: l <= r
        elif op == '>=':
            self.op = lambda l, r: l >= r
        elif op == '==':
            self.op = lambda l, r: l == r
        elif op == '!=':
            self.op = lambda l, r: l != r
        else:
            raise AssertionError("Invalid op: " + str(op))
        self.graphIterator = graphIterator

    def __str__(self):
        l = "({})".format(str(self.left)) if self.left.precedence < self.precedence else str(self.left)
        r = "({})".format(str(self.right)) if self.right.precedence < self.precedence else str(self.right)
        return "{} {} {}".format(l, self.opStr, r)

    def __repr__(self):
        return "BinaryStrOperator({}, {}, {})".format(self.left,
            self.opStr, self.right)

    def evalBackward(self):
        return set( n for (n, p) in self.graphIterator()
                    if self.op(self.left.evalString(p), self.right.evalString(p)) )

    def evalString(self, pkg):
        self.barf("operator in string context")


class PkgGraphEdge:
    __slots__ = ['__node', '__isDirect', '__origin']

    def __init__(self, db, data):
        self.__node = PkgGraphNode(db, data[0])
        self.__isDirect = data[1]
        self.__origin = data[2]

    @property
    def node(self):
        return self.__node

    @property
    def direct(self):
        return self.__isDirect

    @property
    def origin(self):
        return self.__origin

class PkgGraphNode:
    __slots__ = ['__db', '__name', '__key', '__parents', '__childs']

    def __init__(self, db, key, node=None):
        self.__db = db
        self.__key = key
        try:
            if node is None:
                db.execute("SELECT node FROM graph WHERE key=?", (key,))
                node = db.fetchone()[0]
            (self.__name, self.__parents, self.__childs) = pickle.loads(node)
        except sqlite3.Error as e:
            raise BobError("Cannot load internal state: " + str(e))

    @classmethod
    def init(cls, cacheName, cacheKey, rootGenerator):
        try:
            db = sqlite3.connect(cacheName, isolation_level=None).cursor()
            db.execute("CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY, value)")
            db.execute("CREATE TABLE IF NOT EXISTS graph(key PRIMARY KEY, node)")

            # check if Bob was changed
            db.execute("BEGIN")
            db.execute("SELECT value FROM meta WHERE key='vsn'")
            vsn = db.fetchone()
            if (vsn is None) or (vsn[0] != cacheKey):
                # Database was changed or created
                db.execute("DELETE FROM graph")
                root = PkgGraphNode.__convertPackageToGraph(db, rootGenerator())
                db.execute("INSERT OR REPLACE INTO meta VALUES ('vsn', ?), ('root', ?)",
                    (cacheKey, root))
                # Commit and start new read-only transaction
                db.execute("END")
                db.execute("BEGIN")
            else:
                # Database is valid. Use it...
                db.execute("SELECT value FROM meta WHERE key='root'")
                root = db.fetchone()[0]

            return cls(db, root)

        except sqlite3.Error as e:
            raise BobError("Cannot save internal state: " + str(e))

    def close(self):
        self.__db.close()
        self.__db.connection.close()
        del self.__db

    def __repr__(self):
        return "Node({})".format(self.__key)

    def __hash__(self):
        return hash(self.__key)

    def __eq__(self, other):
        return isinstance(other, PkgGraphNode) and (self.__key == other.__key)

    def __len__(self):
        return len(self.__childs)

    def __contains__(self, name):
        return name in self.__childs

    def __getitem__(self, name):
        return PkgGraphEdge(self.__db, self.__childs[name])

    def __iter__(self):
        return iter(self.__childs)

    def key(self):
        return self.__key

    def getByKey(self, key):
        return PkgGraphEdge(self.__db, key)

    def keys(self):
        return self.__childs.keys()

    def values(self):
        return iter( PkgGraphEdge(self.__db, i) for i in self.__childs.values() )

    def items(self):
        return iter( (name, PkgGraphEdge(self.__db, child))
                     for name, child in self.__childs.items() )

    def parents(self, queryIndirect):
        return iter( PkgGraphNode(self.__db, p) for (p, d) in self.__parents.items()
                     if (queryIndirect or d) )

    def allNodes(self):
        self.__db.execute("SELECT key FROM graph")
        keys = self.__db.fetchall()
        return iter( PkgGraphNode(self.__db, i) for (i,) in keys )

    def getName(self):
        return self.__name

    def __addParent(self, parent, direct):
        # Direct dependencies are traversed first. We don't need to worry that
        # a parent is flipping between direct and indirect.
        if parent not in self.__parents:
            self.__parents[parent] = direct
            node = (self.__name, self.__parents, self.__childs)
            self.__db.execute("INSERT OR REPLACE INTO graph VALUES (?, ?)",
                (self.__key, pickle.dumps(node, -1)))

    @staticmethod
    def __convertPackageToGraph(db, pkg, parent=None, directParent=True):
        name = pkg.getName()
        key = pkg._getId()
        db.execute("SELECT node FROM graph WHERE key=?", (key,))
        node = db.fetchone()
        if node is None:
            # recurse
            childs = OrderedDict()
            for d in pkg.getDirectDepSteps():
                subPkg = d.getPackage()
                subPkgId = PkgGraphNode.__convertPackageToGraph(db, subPkg, key, True)
                childs[subPkg.getName()] = (subPkgId, True, "")
            prefixLen = len("/".join(pkg.getStack()))
            for d in pkg.getIndirectDepSteps():
                subPkg = d.getPackage()
                subPkgName = subPkg.getName()
                if subPkgName in childs: continue
                subPkgId = PkgGraphNode.__convertPackageToGraph(db, subPkg, key, False)
                childs[subPkgName] = ( subPkgId, False,
                    ".." + "/".join(subPkg.getStack())[prefixLen:] )
            # create node
            node = ( name, ({parent:directParent} if parent is not None else {}), childs )
            db.execute("INSERT INTO graph VALUES (?, ?)",
                (key, pickle.dumps(node, -1)))
        elif parent is not None:
            # add as parent
            PkgGraphNode(db, key, node[0]).__addParent(parent, directParent)

        return key


class GraphPackageIterator:
    """Special iterator that yields the 'PkgGraphNode' node _and_ the 'Package'
    together.

    The traversal is done in lock step for maximum efficiency. The culprit is
    that the 'bob.input.Package' and 'bob.input.Step' objects are re-created on
    the fly. We must make sure that this is done only once per package.
    """
    def __init__(self, graphRoot, packageRoot):
        self.__graphRoot = graphRoot
        self.__pkgRoot = packageRoot

    def __iter__(self):
        stack = [ (self.__graphRoot, chain(self.__pkgRoot.getDirectDepSteps(),
                                           self.__pkgRoot.getIndirectDepSteps())) ]
        yield (self.__graphRoot, self.__pkgRoot)
        done = set([self.__graphRoot.key()])

        while stack:
            try:
                childPkg = next(stack[-1][1]).getPackage()
                childNode = stack[-1][0][childPkg.getName()].node
                if childNode.key() not in done:
                    done.add(childNode.key())
                    yield (childNode, childPkg)
                    stack.append( (childNode, chain(childPkg.getDirectDepSteps(),
                                                    childPkg.getIndirectDepSteps())) )
            except StopIteration:
                stack.pop()


class PackageSet:
    """Accessor object to the calculated package set.

    Takes care of the transparent caching of package adjacency matrix that is
    used for the path query evaluation.
    """

    def __init__(self, cacheKey, aliases, stringFunctions, packageGenerator, emptyMode="nullglob"):
        self.__cacheKey = cacheKey
        self.__aliases = aliases
        self.__stringFunctions = stringFunctions
        self.__generator = packageGenerator
        self.__emptyMode = emptyMode
        self.__root = None
        self.__graph = None

        # create parsing grammer
        locationPath = pyparsing.Forward()
        relativeLocationPath = pyparsing.Forward()

        axisName = \
              pyparsing.Keyword("descendant-or-self") \
            | pyparsing.Keyword("child") \
            | pyparsing.Keyword("descendant") \
            | pyparsing.Keyword("direct-descendant-or-self") \
            | pyparsing.Keyword("direct-child") \
            | pyparsing.Keyword("direct-descendant") \
            | pyparsing.Keyword("self")

        nodeTest = pyparsing.Word(pyparsing.alphanums + "_.:+-*")
        axisSpecifier = axisName + '@'
        abbreviatedStep = pyparsing.Keyword('.')

        sQStringLiteral = pyparsing.QuotedString("'")
        sQStringLiteral.setParseAction(
            lambda s, loc, toks: StringLiteral(s, loc, toks, False,
                                               self.__stringFunctions,
                                               self.__getGraphIter))
        dQStringLiteral = pyparsing.QuotedString('"', '\\')
        dQStringLiteral.setParseAction(
            lambda s, loc, toks: StringLiteral(s, loc, toks, True,
                                               self.__stringFunctions,
                                               self.__getGraphIter))
        stringLiteral = sQStringLiteral | dQStringLiteral

        functionCall = pyparsing.Forward()
        functionArg = stringLiteral | functionCall
        functionCall << pyparsing.Word(pyparsing.alphas, pyparsing.alphanums+'-') + \
            pyparsing.Suppress('(') + \
            pyparsing.Optional(functionArg +
                pyparsing.ZeroOrMore(pyparsing.Suppress(',') + functionArg)) + \
            pyparsing.Suppress(')')
        functionCall.setParseAction(
            lambda s, loc, toks: FunctionCall(s, loc, toks, self.__stringFunctions,
                                              self.__getGraphIter))

        predExpr = pyparsing.infixNotation(
            locationPath ^ stringLiteral ^ functionCall,
            [
                ('!',  1, pyparsing.opAssoc.RIGHT, lambda s, loc, toks: NotOperator(s, loc, toks, self.__getGraphRoot, 9)),
                ('<',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 8)),
                ('<=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 7)),
                ('>',  2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 6)),
                ('>=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 5)),
                ('==', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 4)),
                ('!=', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryStrOperator, self.__getGraphIter, 3)),
                ('&&', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator, precedence=2)),
                ('||', 2, pyparsing.opAssoc.LEFT,  infixBinaryOp(BinaryBoolOperator, precedence=1))
            ])
        predicate = '[' + predExpr + ']'
        step = abbreviatedStep | (pyparsing.Optional(axisSpecifier) +
                                  nodeTest + pyparsing.Optional(predicate))
        step.setParseAction(lambda s, loc, toks: LocationStep(s, loc, toks))
        abbreviatedRelativeLocationPath = step + '//' + relativeLocationPath
        relativeLocationPath << (
            abbreviatedRelativeLocationPath |
            (step + '/' + relativeLocationPath) |
            step)
        abbreviatedAbsoluteLocationPath = '//' + relativeLocationPath
        absoluteLocationPath = abbreviatedAbsoluteLocationPath | \
                               ('/' + relativeLocationPath)
        locationPath << (absoluteLocationPath | relativeLocationPath)
        locationPath.setParseAction(
            lambda s, loc, toks: LocationPath(s, loc, toks, self.__getGraphRoot))

        self.__pathGrammer = locationPath

    def __substAlias(self, path):
        """Substitute aliases.

        Aliases are substituted at the first step of a relative location path.
        Further steps are not touched. An absolute location path is never
        substituted.
        """
        first, sep, tail = path.partition('/')
        if first:
            path = self.__aliases.get(first, first) + sep + tail
        return path

    def __getGraphRoot(self):
        """Get root node of package graph"""
        if self.__graph is None:
            # Try to load persisted graph. If the graph does not exist or does
            # not match it will be generated and saved.
            self.__graph = PkgGraphNode.init(".bob-tree.sqlite3", self.__cacheKey,
                self.getRootPackage)

        return self.__graph

    def __getGraphIter(self):
        """Get iterator that yields graph node and package together."""
        return GraphPackageIterator(self.__getGraphRoot(), self.getRootPackage())

    def __findResultNodes(self, node, result, valid, queryAll, stack=[]):
        if not queryAll: valid.discard(node)
        if node in result:
            if not queryAll: result.remove(node)
            yield (stack, node)
        for (name, child) in sorted((n, c.node) for (n, c) in node.items()
                                    if ((c.node in valid))):
            yield from self.__findResultNodes(child, result, valid,
                                              queryAll, stack + [name])

    def __findResultPackages(self, node, pkg, result, valid, queryAll):
        if not queryAll: valid.discard(node)
        nextPackages = { s.getPackage().getName() : s.getPackage()
            for s in pkg.getDirectDepSteps() }
        for s in pkg.getIndirectDepSteps():
            p = s.getPackage()
            nextPackages.setdefault(p.getName(), p)

        for (name, child) in sorted((n, c.node) for (n, c) in node.items() if c.node in valid):
            if child in result:
                if not queryAll: result.remove(child)
                yield nextPackages[name]
            yield from self.__findResultPackages(child, nextPackages[name], result, valid, queryAll)

    def __query(self, path):
        # replace aliases
        path = self.__substAlias(path)

        while path.endswith('/'): path = path[:-1]
        if path:
            try:
                path = self.__pathGrammer.parseString(path, True)
            except pyparsing.ParseBaseException as e:
                raise BobError("Invalid syntax: " + str(e),
                               help=markLocation(e.line, e.col))
            assert len(path) == 1
            assert isinstance(path[0], LocationPath)
            #print(path[0])
            return path[0].evalForward(self.__getGraphRoot(), self.__emptyMode)
        else:
            root = self.__getGraphRoot()
            return (set([root]), set([root]))

    def close(self):
        if self.__graph is not None:
            self.__graph.close()
            self.__graph = None

    def getAliases(self):
        return list(self.__aliases.keys())

    def getRootPackage(self):
        """Get virtual root package."""
        if self.__root is None:
            self.__root = self.__generator()
        return self.__root

    def queryTreePath(self, path, queryAll=False):
        """Execute query and return (stack, PkgGraphNode) tuples.

        Setting 'queryAll' to True will return all alternate paths to a result
        element instead of only the first one.
        """
        (nodes, valid) = self.__query(path)
        return self.__findResultNodes(self.__getGraphRoot(), nodes, valid, queryAll)

    def queryPackagePath(self, path, queryAll=False):
        """Execute query and return bob.input.Package objects.

        Setting 'queryAll' to True will return all alternate paths to a result
        package instead of only the first one.
        """
        (nodes, valid) = self.__query(path)
        return self.__findResultPackages(self.__getGraphRoot(), self.getRootPackage(), nodes, valid, queryAll)

    def walkPackagePath(self, path):
        """Legacy path walking.

        Does not support any advanced query features. Guarenteed to return only
        a single package. If any path element is not found a error is thrown.
        """
        # replace aliases
        path = self.__substAlias(path)

        # walk packages
        thisPackage = self.getRootPackage()
        steps = [ s for s in path.split("/") if s != "" ]
        if steps == []:
            raise BobError("'{}' is not a valid package path".format(path))
        for step in steps:
            nextPackages = { s.getPackage().getName() : s.getPackage()
                for s in thisPackage.getDirectDepSteps() }
            for s in thisPackage.getIndirectDepSteps():
                p = s.getPackage()
                nextPackages.setdefault(p.getName(), p)
            if step not in nextPackages:
                stack = thisPackage.getStack()
                raise BobError("Package '{}' not found under '{}'"
                                    .format(step, "/".join(stack) if stack != [''] else "/"))
            thisPackage = nextPackages[step]

        return thisPackage

    def getCacheKey(self):
        return self.__cacheKey
