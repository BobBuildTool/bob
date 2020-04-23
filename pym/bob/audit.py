# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_INPUT_HASH
from .errors import BuildError, ParseError
from .scm import GitAudit, SvnAudit, UrlAudit, ImportAudit, auditFromData
from .utils import asHexStr, hashFile, binStat
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import platform
import pickle
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
        'import' : ImportAudit,
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
            'date'     : str,
            schema.Optional('os-release') : str,
        },
        "env" : str,
        schema.Optional('metaEnv') : { schema.Optional(str) : str },
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

    @classmethod
    def __getOsRelease(cls):
        try:
            ret = cls.__osRelease
        except AttributeError:
            try:
                with open("/etc/os-release") as f:
                    ret = f.read()
            except OSError:
                ret = None
            cls.__osRelease = ret

        return ret

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
        u = platform.uname()
        self.__build = {
            'sysname'  : u.system,
            'nodename' : u.node,
            'release'  : u.release,
            'version'  : u.version,
            'machine'  : u.machine,
            'date'     : datetime.now(timezone.utc).isoformat(),
        }
        osRelease = self.__getOsRelease()
        if osRelease is not None:
            self.__build['os-release'] = osRelease
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
        self.__metaEnv = data.get("metaEnv", {})

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
            "scms" : [ s.dump() for s in self.__scms ],
            "dependencies" : dependencies
        }

        if self.__metaEnv:
            ret[ "metaEnv"] = self.__metaEnv

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

    async def addScm(self, name, workspace, dir, extra):
        scm = Artifact.SCMS.get(name)
        if scm is None:
            raise BuildError("Cannot handle SCM: " + name)
        self.__scms.append(await scm.fromDir(workspace, dir, extra))
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

    def getBuildId(self):
        return self.__buildId

    def getReferences(self):
        ret = set()
        for i in self.__deps: ret.add(i)
        if self.__sandbox: ret.add(self.__sandbox)
        for i in self.__tools.values(): ret.add(i)
        return ret

    def getMetaData(self):
        return self.__defines

    def getBuildInfo(self):
        return self.__build

    def getMetaEnv(self):
        return self.__metaEnv

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
        try:
            cacheName = file + ".pickle"
            cacheKey = binStat(file) + BOB_INPUT_HASH
            with open(cacheName, "rb") as f:
                persistedCacheKey = f.read(len(cacheKey))
                if cacheKey == persistedCacheKey:
                    return pickle.load(f)
        except (EOFError, OSError, pickle.UnpicklingError) as e:
            pass

        audit = cls()
        try:
            with gzip.open(file, 'rb') as gzf:
                audit.load(gzf, file)
            with open(cacheName, "wb") as f:
                f.write(cacheKey)
                pickle.dump(audit, f, -1)
        except OSError as e:
            raise ParseError("Error loading audit: " + str(e))
        return audit

    @classmethod
    def fromByteStream(cls, stream, name):
        audit = cls()
        audit.load(stream, name)
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

    def load(self, file, name):
        try:
            tree = json.load(io.TextIOWrapper(file, encoding='utf8'))
            tree = Audit.SCHEMA.validate(tree)
            self.__artifact = Artifact.fromData(tree["artifact"])
            self.__references = {
                r["artifact-id"] : Artifact.fromData(r) for r in tree["references"]
            }
        except schema.SchemaError as e:
            raise ParseError(name + ": Invalid audit record: " + str(e))
        except ValueError as e:
            raise ParseError(name + ": Invalid json: " + str(e))
        self.__validate()

    def save(self, file):
        tree = {
            "artifact" : self.__artifact.dump(),
            "references" : [ a.dump() for a in self.__references.values() ]
        }
        try:
            with gzip.open(file, 'wb', 6) as gzf:
                json.dump(tree, io.TextIOWrapper(gzf, encoding='utf8'))

            cacheName = file + ".pickle"
            cacheKey = binStat(file) + BOB_INPUT_HASH
            with open(cacheName, "wb") as f:
                f.write(cacheKey)
                pickle.dump(self, f, -1)

        except OSError as e:
            raise BuildError("Cannot write audit: " + str(e))

    def reset(self, variantId, buildId, resultHash):
        self.__artifact.reset(variantId, buildId, resultHash)
        self.__references = {}

    def getId(self):
        return self.__artifact.getId()

    def getArtifact(self, aid=None):
        if aid:
            return self.__references[aid]
        else:
            return self.__artifact

    def getReferencedBuildIds(self):
        ret = set()
        refs = self.__artifact.getReferences()
        while refs:
            artifact = self.__references[refs.pop()]
            if artifact.getMetaData()["step"] == "dist":
                ret.add(artifact.getBuildId())
            else:
                refs.update(artifact.getReferences())
        return sorted(ret)

    def setRecipesAudit(self, recipes):
        self.__artifact.setRecipes(recipes)

    def setRecipesData(self, xml):
        self.__artifact.setRecipes(auditFromData(xml))

    def setEnv(self, env):
        self.__artifact.setEnv(env)

    def addDefine(self, name, value):
        self.__artifact.addDefine(name, value)

    async def addScm(self, name, workspace, dir, extra):
        await self.__artifact.addScm(name, workspace, dir, extra)

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

