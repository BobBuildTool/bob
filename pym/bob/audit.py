# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from . import BOB_INPUT_HASH, DEBUG
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
    elif d is None:
        h.update(struct.pack("<B", 7))
    else:
        assert False, "Cannot digest " + str(type(d))

class HexValidator:
    def validate(self, data):
        try:
            return bytes.fromhex(data)
        except ValueError:
            raise schema.SchemaUnexpectedTypeError("not valid hex str", None)

class Artifact:
    __slots__ = ['__data']

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
        schema.Optional("layers") : dict,
        "dependencies" : {
            schema.Optional('args') : [ HexValidator() ],
            schema.Optional('tools') : { str : HexValidator() },
            schema.Optional('sandbox') : HexValidator()
        }
    })

    REQUIRED_KEYS = frozenset(("variant-id", "build-id", "artifact-id",
                               "result-hash", "meta", "build", "dependencies"))

    def __init__(self, date=None):
        self.reset(b'\x00' * 20, b'\x00' * 20, b'\x00' * 20, date)

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

    def __invalidateId(self):
        if 'artifact-id' in self.__data:
            del self.__data['artifact-id']

    def __calculateArtifactId(self):
        if 'artifact-id' in self.__data: return
        h = hashlib.sha1()
        digestData(self.__data, h)
        self.__data['artifact-id'] = asHexStr(h.digest())

    def reset(self, variantId, buildId, resultHash, date=None):
        u = platform.uname()
        # Versions of Bob before 0.25 required some fields to be always
        # present. Keep them for compatibility reasons.
        self.__data = {
            "variant-id" : asHexStr(variantId),
            "build-id" : asHexStr(buildId),
            "result-hash" : asHexStr(resultHash),
            "meta" : {},
            "build" : {
                'sysname'  : u.system,
                'nodename' : u.node,
                'release'  : u.release,
                'version'  : u.version,
                'machine'  : u.machine,
                'date'     : (datetime.now(timezone.utc) if date is None else date).isoformat(),
            },
            "env" : "",
            "scms" : [],
            "dependencies" : {}
        }

        osRelease = self.__getOsRelease()
        if osRelease is not None:
            self.__data['build']['os-release'] = osRelease

    def load(self, data):
        if not isinstance(data, dict) or any(k not in data for k in self.REQUIRED_KEYS):
            raise ParseError("Invalid audit trail")
        self.__data = data

    def dump(self):
        self.__calculateArtifactId()
        return self.__data

    def setRecipes(self, recipes):
        if recipes is None:
            if "recipes" in self.__data:
                del self.__data["recipes"]
        else:
            self.__data["recipes"] = recipes.dump()
        self.__invalidateId()

    def setLayers(self, layers):
        if layers:
            self.__data['layers'] = { name : (audit and audit.dump())
                                      for name, audit in layers.items() }
        elif 'layers' in self.__data:
            del self.__data['layers']
        self.__invalidateId()

    def setEnv(self, env):
        try:
            with open(env) as f:
                self.__data["env"] = f.read()
        except OSError as e:
            raise ParseError("Error reading environment: " + str(e))
        self.__invalidateId()

    def addDefine(self, name, value):
        self.__data["meta"][name] = value
        self.__invalidateId()

    async def addScm(self, name, workspace, dir, extra):
        scm = Artifact.SCMS.get(name)
        if scm is None:
            raise BuildError("Cannot handle SCM: " + name)
        self.__data['scms'].append((await scm.fromDir(workspace, dir, extra)).dump())
        self.__invalidateId()

    def addTool(self, name, toolId):
        self.__data["dependencies"].setdefault("tools", {})[name] = asHexStr(toolId)
        self.__invalidateId()

    def addMetaEnv(self, var, value):
        self.__data.setdefault("metaEnv", {})[var] = value
        self.__invalidateId()

    def setSandbox(self, sandboxId):
        self.__data["dependencies"]["sandbox"] = asHexStr(sandboxId)
        self.__invalidateId()

    def addArg(self, argId):
        self.__data["dependencies"].setdefault("args", []).append(asHexStr(argId))
        self.__invalidateId()

    def getId(self):
        self.__calculateArtifactId()
        return bytes.fromhex(self.__data['artifact-id'])

    def getBuildId(self):
        return bytes.fromhex(self.__data['build-id'])

    def getReferences(self):
        deps = self.__data["dependencies"]
        ret = set()
        for i in deps.get("args", []): ret.add(bytes.fromhex(i))
        if "sandbox" in deps: ret.add(bytes.fromhex(deps["sandbox"]))
        for i in deps.get("tools", {}).values(): ret.add(bytes.fromhex(i))
        return ret

    def getMetaData(self):
        return self.__data["meta"]

    def getBuildInfo(self):
        return self.__data["build"]

    def getMetaEnv(self):
        return self.__data.get("metaEnv", {})

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
            if DEBUG['audit']:
                Audit.SCHEMA.validate(tree)
                self.__validate()
            self.__artifact = Artifact.fromData(tree["artifact"])
            self.__references = {
                bytes.fromhex(r["artifact-id"]) : Artifact.fromData(r)
                for r in tree["references"]
            }
        except (ValueError, KeyError, TypeError) as e:
            raise ParseError(name + ": Invalid audit trail: " + str(e))

    def save(self, file):
        tree = {
            "artifact" : self.__artifact.dump(),
            "references" : [ a.dump() for a in self.__references.values() ]
        }
        if DEBUG['audit']:
            self.__validate()
            Audit.SCHEMA.validate(tree)
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

    def setRecipesAudit(self, recipesAudit):
        self.__artifact.setRecipes(recipesAudit.get(""))
        self.__artifact.setLayers({
            layer : audit for layer, audit in recipesAudit.items() if layer != ""
        })

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

