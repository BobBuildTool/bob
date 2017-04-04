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

from .errors import BuildError, ParseError
from .scm import GitAudit, SvnAudit, UrlAudit, auditFromData
from .tty import colorize
from .utils import asHexStr, hashFile
from datetime import datetime
import gzip
import hashlib
import json
import os
import schema
import struct

def digestMap(m, h):
    h.update(struct.pack("<BI", 1, len(m)))
    for (k,v) in sorted(m.items()):
        digestString(k, h)
        digestData(v, h)

def digestString(s, h):
    h.update(struct.pack("<BI", 2, len(s)))
    h.update(s.encode('utf8'))

def digestData(d, h):
    if isinstance(d, str):
        digestString(d, h)
    elif isinstance(d, dict):
        digestMap(d, h)
    elif isinstance(d, list):
        h.update(struct.pack("<BI", 3, len(d)))
        for i in d: digestData(i, h)
    elif isinstance(d, int):
        h.update(struct.pack("<Bq", 4, d))
    elif isinstance(d, bool):
        h.update(struct.pack("<B?", 5, d))
    elif isinstance(d, bytes):
        h.update(struct.pack("<BI", 6, len(d)))
        h.update(d)
    else:
        assert False, "Cannot digest " + str(type(d))

class HexValidator:
    def validate(self, data):
        try:
            return bytes.fromhex(data)
        except ValueError:
            raise schema.SchemaUnexpectedTypeError("not valid hex str", None)

class Artifact:

    SCMS = {
        'git' : GitAudit,
        'svn' : SvnAudit,
        'url' : UrlAudit,
    }

    SCHEMA = schema.Schema({
        "variant-id" : HexValidator(),
        "build-id" : HexValidator(),
        "artifact-id" : HexValidator(),
        "result-hash" : HexValidator(),
        "meta" : { schema.Optional(str) : str },
        "build" : {
            'sysname'  : str,
            'nodename' : str,
            'release'  : str,
            'version'  : str,
            'machine'  : str,
            'date'     : str
        },
        "env" : str,
        "metaEnv" : { schema.Optional(str) : str },
        "scms" : [ dict ],
        schema.Optional("recipes") : dict,
        "dependencies" : {
            schema.Optional('args') : [ HexValidator() ],
            schema.Optional('tools') : { str : HexValidator() },
            schema.Optional('sandbox') : HexValidator()
        }
    })

    def __init__(self):
        self.reset(b'\x00' * 20, b'\x00' * 20, b'\x00' * 20)

    @classmethod
    def fromData(cls, data):
        artifact = cls()
        artifact.load(data)
        return artifact

    def __calculate(self):
        if self.__id is not None: return
        d = self.__dump()
        h = hashlib.sha1()
        digestData(d, h)
        self.__id = h.digest()

    def reset(self, variantId, buildId, resultHash):
        self.__variantId = variantId
        self.__buildId = buildId
        self.__resultHash = resultHash
        self.__recipes = None
        self.__defines = {}
        u = os.uname()
        self.__build = {
            'sysname'  : u.sysname,
            'nodename' : u.nodename,
            'release'  : u.release,
            'version'  : u.version,
            'machine'  : u.machine,
            'date'     : str(datetime.utcnow()),
        }
        self.__env = ""
        self.__metaEnv = {}
        self.__scms = []
        self.__deps = []
        self.__tools = {}
        self.__sandbox = None
        self.__id = None

    def load(self, data):
        self.__id = None
        self.__variantId = data["variant-id"]
        self.__buildId = data["build-id"]
        self.__resultHash = data["result-hash"]

        recipes = data.get("recipes")
        if recipes is not None:
            self.__recipes = auditFromData(recipes)
        else:
            self.__recipes = None

        self.__defines = data["meta"]
        self.__build = data["build"]
        self.__env = data["env"]
        self.__metaEnv = data["metaEnv"]

        self.__scms = []
        scms = data["scms"]
        for i in scms:
            self.__scms.append(auditFromData(i))

        deps = data["dependencies"]
        self.__deps = deps.get("args", [])
        self.__tools = deps.get("tools", {})
        self.__sandbox = deps.get("sandbox")

        # validate id
        self.__calculate()
        if self.__id != data["artifact-id"]:
            raise ParseError("Corrupt Audit! Artifact-Id does not match!")

    def dump(self):
        d = self.__dump()
        d['artifact-id'] = asHexStr(self.getId())
        return d

    def __dump(self):
        dependencies = {}
        if self.__deps:
            dependencies["args"] = [ asHexStr(i) for i in self.__deps ]
        if self.__tools:
            dependencies["tools"] = { n : asHexStr(t) for (n,t) in self.__tools.items() }
        if self.__sandbox:
            dependencies["sandbox"] = asHexStr(self.__sandbox)

        ret = {
            "variant-id" : asHexStr(self.__variantId),
            "build-id" : asHexStr(self.__buildId),
            "result-hash" : asHexStr(self.__resultHash),
            "meta" : self.__defines,
            "build" : self.__build,
            "env" : self.__env,
            "metaEnv" : self.__metaEnv,
            "scms" : [ s.dump() for s in self.__scms ],
            "dependencies" : dependencies
        }

        if self.__recipes is not None:
            ret["recipes"] = self.__recipes.dump()

        return ret

    def setRecipes(self, recipes):
        self.__recipes = recipes

    def setEnv(self, env):
        try:
            with open(env) as f:
                self.__env = f.read()
        except OSError as e:
            raise ParseError("Error reading environment: " + str(e))
        self.__id = None

    def addDefine(self, name, value):
        self.__defines[name] = value
        self.__id = None

    def addScm(self, name, workspace, dir):
        scm = Artifact.SCMS.get(name)
        if scm is None:
            raise BuildError("Cannot handle SCM: " + name)
        self.__scms.append(scm.fromDir(workspace, dir))
        self.__id = None

    def addTool(self, name, tool):
        self.__tools[name] = tool
        self.__id = None

    def addMetaEnv(self, var, value):
        self.__metaEnv[var] = value

    def setSandbox(self, sandbox):
        self.__sandbox = sandbox
        self.__id = None

    def addArg(self, arg):
        self.__deps.append(arg)
        self.__id = None

    def getId(self):
        self.__calculate()
        return self.__id

    def getReferences(self):
        ret = set()
        for i in self.__deps: ret.add(i)
        if self.__sandbox: ret.add(self.__sandbox)
        for i in self.__tools.values(): ret.add(i)
        return ret

class Audit:
    SCHEMA = schema.Schema({
        'artifact' : Artifact.SCHEMA,
        'references' : [ Artifact.SCHEMA ]
    })

    def __init__(self):
        self.__artifact = Artifact()
        self.__references = {}

    @classmethod
    def fromFile(cls, file):
        audit = cls()
        audit.load(file)
        return audit

    @classmethod
    def create(cls, variantId, buildId, resultHash):
        audit = cls()
        audit.reset(variantId, buildId, resultHash)
        return audit

    def __merge(self, other):
        self.__references.update(other.__references)
        self.__references[other.getId()] = other.__artifact

    def __validate(self):
        done = set()
        refs = self.__artifact.getReferences()
        while refs:
            curId = refs.pop()
            cur = self.__references.get(curId)
            if cur is None:
                raise ParseError("Incomplete audit: missing " + asHexStr(curId))
            for dep in cur.getReferences():
                if dep not in done: refs.add(dep)
            done.add(curId)

    def load(self, file):
        try:
            with gzip.open(file, 'rt') as gzf:
                tree = json.load(gzf)
            tree = Audit.SCHEMA.validate(tree)
            self.__artifact = Artifact.fromData(tree["artifact"])
            self.__references = {
                r["artifact-id"] : Artifact.fromData(r) for r in tree["references"]
            }
        except OSError as e:
            print(colorize("Error loading audit: " + str(e), "33"))
        except schema.SchemaError as e:
            raise ParseError("Invalid audit record: " + str(e))
        except ValueError as e:
            raise ParseError("Invalid json: " + str(e))
        self.__validate()

    def save(self, file):
        tree = {
            "artifact" : self.__artifact.dump(),
            "references" : [ a.dump() for a in self.__references.values() ]
        }
        try:
            with gzip.open(file, 'wt', 6) as gzf:
                json.dump(tree, gzf)
        except OSError as e:
            raise BuildError("Cannot write audit: " + str(e))

    def reset(self, variantId, buildId, resultHash):
        self.__artifact.reset(variantId, buildId, resultHash)
        self.__references = {}

    def getId(self):
        return self.__artifact.getId()

    def setRecipesAudit(self, recipes):
        self.__artifact.setRecipes(recipes)

    def setRecipesData(self, xml):
        self.__artifact.setRecipes(auditFromData(xml))

    def setEnv(self, env):
        self.__artifact.setEnv(env)

    def addDefine(self, name, value):
        self.__artifact.addDefine(name, value)

    def addScm(self, name, workspace, dir):
        self.__artifact.addScm(name, workspace, dir)

    def addTool(self, name, tool):
        audit = Audit.fromFile(tool)
        self.__merge(audit)
        self.__artifact.addTool(name, audit.getId())

    def addMetaEnv(self, var, value):
        self.__artifact.addMetaEnv(var, value)

    def setSandbox(self, sandbox):
        audit = Audit.fromFile(sandbox)
        self.__merge(audit)
        self.__artifact.setSandbox(audit.getId())

    def addArg(self, arg):
        audit = Audit.fromFile(arg)
        self.__merge(audit)
        self.__artifact.addArg(audit.getId())

