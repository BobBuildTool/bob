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
from collections import OrderedDict
import dbm
import pickle


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


class PackageSet:
    def __init__(self, cacheKey, aliases, packageGenerator):
        self.__cacheKey = cacheKey
        self.__aliases = aliases
        self.__generator = packageGenerator
        self.__root = None
        self.__graph = None

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

    def getAliases(self):
        return list(self.__aliases.keys())

    def getRootPackage(self):
        """Get virtual root package."""
        if self.__root is None:
            self.__root = self.__generator()
        return self.__root

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
