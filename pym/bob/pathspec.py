# Bob build tool
# Copyright (C) 2017  Jan Klötzke
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

from .errors import BobError
from .stringparser import isFalse, isTrue, Env
from collections import OrderedDict
from itertools import chain
from fnmatch import fnmatchcase
from functools import lru_cache
import dbm
import pickle
import pyparsing

# need to enable this for nested expression parsing performance
pyparsing.ParserElement.enablePackrat()

# See "Efficient algorithms for processing XPath queries" [1] for the core
# algorithms that are applied here.
#
# [1] Gottlob, G., Koch, C., and Pichler, R. 2002. Efficient algorithms
#     for processing XPath queries. In Proceedings of the 28th
#     International Conference on Very Large Data Bases (VLDB'02).
#     HongKong, China, 95-106


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
    def __init__(self, s, loc):
        self.__s = s
        self.__loc = loc

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

    def __repr__(self):
        return "LocationPath({})".format(self.__path)

    def __findIntermediateNodes(self, old, new):
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
            todo.update(node.parents())

        return ret

    def evalForward(self, root):
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
        for i in self.__path:
            oldNodes = nodes
            nodes, search = i.evalForward(nodes, valid)
            if search:
                valid.update(self.__findIntermediateNodes(oldNodes, nodes))
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

    def __repr__(self):
        return "LocationStep({}@{}[{}])".format(self.__axis, self.__test, self.__pred)

    def __evalAxisChild(self, nodes):
        """Find nodes in the 'child' axis."""
        ret = set()
        for i in nodes:
            ret.update(c.node for c in i.values())
        return ret

    def __evalAxisDescendant(self, nodes):
        """Find nodes in the 'descendant' axis."""
        ret = set()
        todo = nodes
        while todo:
            childs = set()
            for i in todo:
                childs.update(c.node for c in i.values())
            todo = childs - ret
            ret.update(childs)
        return ret

    def __evalAxisParent(self, nodes):
        """Find nodes in the 'parent' axis."""
        ret = set()
        for i in nodes:
            ret.update(i.parents())
        return ret

    def __evalAxisAncestor(self, nodes):
        """Find nodes in the 'ancestor' axis."""
        ret = set()
        todo = nodes
        while todo:
            parents = set()
            for i in todo: parents.update(i.parents())
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
        search = False
        if self.__axis == "child":
            nodes = self.__evalAxisChild(nodes)
        elif self.__axis == "descendant":
            nodes = self.__evalAxisDescendant(nodes)
            search = True
        elif self.__axis == "descendant-or-self":
            nodes = self.__evalAxisDescendant(nodes) | nodes
            search = True
        elif self.__axis == "self":
            pass
        else:
            assert False, "Invalid axis: " + self.__axis

        if self.__test == "*":
            pass
        elif '*' in self.__test:
            nodes = set(i for i in nodes if fnmatchcase(i.getName(), self.__test))
        else:
            nodes = set(i for i in nodes if i.getName() == self.__test)

        if self.__pred:
            nodes = nodes & self.__pred.evalBackward()

        return (nodes, search)

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
            nodes = self.__evalAxisParent(nodes)
        elif self.__axis == "descendant":
            nodes = self.__evalAxisAncestor(nodes)
        elif self.__axis == "descendant-or-self":
            nodes = self.__evalAxisAncestor(nodes) | nodes
        elif self.__axis == "self":
            pass
        else:
            assert False, "Invalid axis: " + self.__axis

        return nodes

class NotOperator(BaseASTNode):
    def __init__(self, s, loc, toks, rootNodeGetter):
        super().__init__(s, loc)
        self.rootNodeGetter = rootNodeGetter
        assert len(toks) == 1, toks
        toks = toks[0]
        assert len(toks) == 2, toks
        assert toks[0] == '!'
        self.op = toks[1]

    def __repr__(self):
        return "NotOperator({})".format(self.op)

    def evalBackward(self):
        return set(self.rootNodeGetter().allNodes()) - self.op.evalBackward()

    def evalString(self, pkg):
        self.barf("operator in string context")

class BinaryBoolOperator(BaseASTNode):
    def __init__(self, s, loc, toks):
        super().__init__(s, loc)
        assert len(toks) == 1, toks
        toks = toks[0]
        assert len(toks) == 3
        self.left = toks[0]
        self.right = toks[2]
        self.opStr = op = toks[1]
        if op == '&&':
            self.op = lambda l, r: l & r
        elif op == '||':
            self.op = lambda l, r: l | r
        else:
            assert False, op

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
                "sandbox" : pkg._getSandboxRaw(),
                "tools" : pkgStep.getTools()
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
            "sandbox" : pkg._getSandboxRaw(),
            "tools" : pkgStep.getTools()
        }
        return self.fun(args, env=env, **extra)

class BinaryStrOperator(BaseASTNode):
    def __init__(self, s, loc, toks, graphIterator):
        super().__init__(s, loc)
        assert len(toks) == 1, toks
        toks = toks[0]
        assert len(toks) == 3
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
            assert False, op
        self.graphIterator = graphIterator

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

    def __init__(self, db, key):
        self.__db = db
        self.__key = key
        (self.__name, self.__parents, self.__childs) = pickle.loads(db[key])

    @classmethod
    def load(cls, cacheName, cacheKey):
        try:
            db = dbm.open(cacheName, "r")
            persistedCacheKey = db.get(b'vsn')
            if cacheKey == persistedCacheKey:
                return cls(db, db[b'root'])
            else:
                db.close()
        except OSError:
            pass
        except dbm.error:
            pass
        return None

    @classmethod
    def create(cls, cacheName, cacheKey, root):
        try:
            db = dbm.open(cacheName, 'n')
            try:
                db[b'root'] = rootKey = PkgGraphNode.__convertPackageToGraph(db, root)
                db[b'vsn'] = cacheKey
            finally:
                db.close()
            return cls(dbm.open(cacheName, 'r'), rootKey)
        except dbm.error as e:
            raise BobError("Cannot save internal state: " + str(e))

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

    def parents(self):
        return iter( PkgGraphNode(self.__db, p) for p in self.__parents )

    def allNodes(self):
        return iter( PkgGraphNode(self.__db, i) for i in self.__db.keys()
                     if ((i != b'root') and (i != b'vsn')) )

    def getName(self):
        return self.__name

    def __addParent(self, parent):
        if parent not in self.__parents:
            self.__parents.add(parent)
            self.__db[self.__key] = pickle.dumps(
                (self.__name, self.__parents, self.__childs),
                -1)

    @staticmethod
    def __convertPackageToGraph(db, pkg, parent=None):
        name = pkg.getName()
        key = pkg._getId().to_bytes(4, 'little')
        if key not in db:
            # recurse
            childs = OrderedDict()
            for d in pkg.getDirectDepSteps():
                subPkg = d.getPackage()
                subPkgId = PkgGraphNode.__convertPackageToGraph(db, subPkg, key)
                childs[subPkg.getName()] = (subPkgId, True, "")
            prefixLen = len("/".join(pkg.getStack()))
            for d in pkg.getIndirectDepSteps():
                subPkg = d.getPackage()
                subPkgName = subPkg.getName()
                if subPkgName in childs: continue
                subPkgId = PkgGraphNode.__convertPackageToGraph(db, subPkg, key)
                childs[subPkgName] = ( subPkgId, False,
                    ".." + "/".join(subPkg.getStack())[prefixLen:] )
            # create node
            node = ( name, (set([parent]) if parent is not None else set()), childs )
            db[key] = pickle.dumps(node, -1)
        elif parent is not None:
            # add as parent
            PkgGraphNode(db, key).__addParent(parent)

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

    def __init__(self, cacheKey, aliases, stringFunctions, packageGenerator):
        self.__cacheKey = cacheKey
        self.__aliases = aliases
        self.__stringFunctions = stringFunctions
        self.__generator = packageGenerator
        self.__root = None
        self.__graph = None

        # create parsing grammer
        locationPath = pyparsing.Forward()
        relativeLocationPath = pyparsing.Forward()

        axisName = \
              pyparsing.Keyword("descendant-or-self") \
            | pyparsing.Keyword("child") \
            | pyparsing.Keyword("descendant") \
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
                ('!',  1, pyparsing.opAssoc.RIGHT, lambda s, loc, toks: NotOperator(s, loc, toks, self.__getTree)),
                ('<',  2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('<=', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('>',  2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('>=', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('==', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('!=', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryStrOperator(s, loc, toks, self.__getGraphIter)),
                ('&&', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryBoolOperator(s, loc, toks)),
                ('||', 2, pyparsing.opAssoc.LEFT,  lambda s, loc, toks: BinaryBoolOperator(s, loc, toks))
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
            # try to load persisted graph
            cacheName = ".bob-tree.dbm"
            self.__graph = PkgGraphNode.load(cacheName, self.__cacheKey)
            if self.__graph is None:
                # generate, convert and save
                root = self.getRootPackage()
                self.__graph = PkgGraphNode.create(cacheName, self.__cacheKey, root)

        return self.__graph

    def __getGraphIter(self):
        """Get iterator that yields graph node and package together."""
        return GraphPackageIterator(self.__getGraphRoot(), self.getRootPackage())

    def __findResultNodes(self, node, result, valid, queryIndirect, queryAll, stack=[]):
        if not queryAll: valid.discard(node)
        if node in result:
            if not queryAll: result.remove(node)
            yield (stack, node)
        for (name, child) in sorted((n, c.node) for (n, c) in node.items()
                                    if ((c.node in valid) and (c.direct or queryIndirect))):
            yield from self.__findResultNodes(child, result, valid, queryIndirect,
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
            return path[0].evalForward(self.__getGraphRoot())
        else:
            root = self.__getGraphRoot()
            return (set([root]), set([root]))

    def getAliases(self):
        return list(self.__aliases.keys())

    def getRootPackage(self):
        """Get virtual root package."""
        if self.__root is None:
            self.__root = self.__generator()
        return self.__root

    def queryTreePath(self, path, queryIndirect, queryAll=False):
        """Execute query and return (stack, PkgGraphNode) tuples.

        If 'queryIndirect' is True then indirect dependencies are consideret
        too for path traversals. Setting it to false will only return paths
        that consist of direct dependencies. Setting 'queryAll' to True will
        return all alternate paths to a result element instead of only the
        first one.
        """
        (nodes, valid) = self.__query(path)
        return self.__findResultNodes(self.__getGraphRoot(), nodes, valid, queryIndirect, queryAll)

    def queryPackagePath(self, path, queryAll=False):
        """Execute query and return bob.input.Package objects.

        Setting 'queryAll' to True will return all alternate paths to a result
        package instead of only the first one.
        """
        (nodes, valid) = self.__query(path)
        return self.__findResultPackages(self.__getGraphRoot(), self.getRootPackage(), nodes, valid, queryAll)

    def walkTreePath(self, path):
        # replace aliases
        path = self.__substAlias(path)

        # descend tree
        root = self.__getGraphRoot()
        stack = [ s for s in path.split("/") if s != "" ]
        trail = []
        for step in stack:
            if step not in root:
                raise BobError("Package '{}' not found under '{}'".format(step, "/".join(trail)))
            trail.append(step)
            root = root[step].node

        yield (stack, root)

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
