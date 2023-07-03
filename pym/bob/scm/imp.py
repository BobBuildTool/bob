# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BuildError
from ..stringparser import IfExpression
from ..tty import stepAction, INFO, TRACE
from ..utils import asHexStr, hashDirectory, emptyDirectory
from .scm import Scm, ScmAudit
import base64
import io
import os, os.path
import schema
import shutil
import stat
import tarfile

def copyTree(src, dst, invoker):
    """Recursively copy directory tree.

    The src and dst directories must already exist. The items in the source
    directory are copied to the destination only if it does not exist yet or if
    it is newer than the destination. The copy operation is aborted if the
    source and destination file types differ (file vs. directory vs. symlink).
    """

    changed = False
    try:
        todo = []
        for name in os.listdir(src):
            srcName = os.path.join(src, name)
            dstName = os.path.join(dst, name)

            srcStat = os.lstat(srcName)
            try:
                dstStat = os.lstat(dstName)
            except OSError:
                dstStat = None

            if dstStat is not None:
                if stat.S_IFMT(srcStat.st_mode ^ dstStat.st_mode) != 0:
                    invoker.fail("Copy failed: destination has different type:", srcName)

            todo.append((srcName, srcStat, dstName, dstStat))

        # First handle everything *except* symlinks. On MSYS the destination
        # must exist when creating the symlink, probably because the target
        # type must be determined on creation.
        for srcName, srcStat, dstName, dstStat in todo:
            if stat.S_ISLNK(srcStat.st_mode):
                pass
            elif stat.S_ISDIR(srcStat.st_mode):
                if dstStat is None:
                    os.mkdir(dstName)
                    changed = True
                if copyTree(srcName, dstName, invoker):
                    shutil.copystat(srcName, dstName)
            else:
                if (dstStat is not None) and (srcStat.st_mtime_ns <= dstStat.st_mtime_ns):
                    continue
                shutil.copy2(srcName, dstName)
                changed = True

        # Now handle all symlinks.
        for srcName, srcStat, dstName, dstStat in todo:
            if stat.S_ISLNK(srcStat.st_mode):
                linkTo = os.readlink(srcName)
                if dstStat is not None:
                    oldLink = os.readlink(dstName)
                    if linkTo == oldLink:
                        continue
                    os.unlink(dstName)
                os.symlink(linkTo, dstName)
                changed = True
    except OSError as e:
        invoker.fail("Copy failed", str(e))

    return changed

def packTree(src):
    if not os.path.isdir(src):
        raise BuildError("Cannot import '{}': not a directory!".format(src))

    try:
        f = io.BytesIO()
        with tarfile.open(fileobj=f, mode="w:xz") as tar:
            # Special handling for MSYS. Symlinks fail if the target does not
            # exist and Python will fall back to create a copy on extraction.
            # To prevent this first everything *except* symlinks is archived
            # and then the symlinks are added.
            symlinks = []
            def filterSymlinks(ti):
                if ti.issym():
                    symlinks.append(ti)
                    return None
                else:
                    return ti

            tar.add(src, arcname=".", filter=filterSymlinks)
            for ti in symlinks:
                tar.addfile(ti)
    except OSError as e:
        raise BuildError("Error gathering files: {}".format(str(e)))
    return base64.b85encode(f.getvalue()).decode('ascii')

def unpackTree(data, dest):
    try:
        f = io.BytesIO(base64.b85decode(data))
        with tarfile.open(fileobj=f, mode="r:xz") as tar:
            tar.extractall(dest)
    except OSError as e:
        raise BuildError("Error unpacking files: {}".format(str(e)))

class ImportScm(Scm):

    DEFAULTS = {
        schema.Optional('dir') : str,
        schema.Optional('prune') : bool,
    }

    __SCHEMA = {
        'scm' : 'import',
        'url' : str,
        schema.Optional('if') : schema.Or(str, IfExpression),
    }

    SCHEMA = schema.Schema({**__SCHEMA, **DEFAULTS})

    def __init__(self, spec, overrides=[], pruneDefault=None, fixDigestBug=False, projectRoot=""):
        super().__init__(spec, overrides)
        self.__url = spec["url"]
        self.__dir = spec.get("dir", ".")
        self.__prune = spec.get("prune", pruneDefault or False)
        self.__data = spec.get("__data")
        self.__projectRoot = spec.get("__projectRoot", projectRoot)
        self.__fixDigestBug = fixDigestBug

    def getProperties(self, isJenkins):
        ret = super().getProperties(isJenkins)
        ret.update({
            'scm' : 'import',
            'url' : self.__url,
            'dir' : self.__dir,
            'prune' : self.__prune,
        })
        if isJenkins:
            ret['__data'] = packTree(self.__url)
        else:
            ret['__projectRoot'] = self.__projectRoot
        return ret

    async def invoke(self, invoker):
        dest = invoker.joinPath(self.__dir)
        os.makedirs(dest, exist_ok=True)
        if self.__prune: emptyDirectory(dest)
        if self.__data is None:
            src = os.path.join(self.__projectRoot, self.__url)
            if not os.path.isdir(src):
                invoker.fail("Cannot import '{}': not a directory!".format(src))
            copyTree(src, dest, invoker)
        else:
            unpackTree(self.__data, dest)

    def asDigestScript(self):
        if self.__fixDigestBug:
            return self.__url + " " + self.__dir
        else:
            return self.__url

    def getDirectory(self):
        return self.__dir

    def isDeterministic(self):
        return False

    def isLocal(self):
        return True

    def hasLiveBuildId(self):
        return True

    async def predictLiveBuildId(self, step):
        with stepAction(step, "HASH", self.__url, (INFO, TRACE)) as a:
            return hashDirectory(self.__url)

    def calcLiveBuildId(self, workspacePath):
        return hashDirectory(os.path.join(workspacePath, self.__dir))

    def getAuditSpec(self):
        return ("import", self.__dir, {"url" : self.__url})


class ImportAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'import',
        'dir' : str,
        'digest' : {
            'algorithm' : 'sha1',
            'value' : str
        },
        'url' : str,
    })

    async def _scanDir(self, workspace, dir, extra):
        self.__dir = dir
        self.__hash = asHexStr(hashDirectory(os.path.join(workspace, dir)))
        self.__url = extra.get("url")

    def _load(self, data):
        self.__dir = data["dir"]
        self.__hash = data["digest"]["value"]
        self.__url = data["url"]

    def dump(self):
        return {
            "type" : "import",
            "dir" : self.__dir,
            "digest" : {
                "algorithm" : "sha1",
                "value" : self.__hash
            },
            "url" : self.__url,
        }
