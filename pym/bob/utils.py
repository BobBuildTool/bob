# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BuildError, ParseError
from binascii import hexlify
from tempfile import NamedTemporaryFile
import collections
import hashlib
import logging
import os
import shutil
import stat
import struct
import sys

def hashString(string):
    h = hashlib.md5()
    h.update(string.encode("utf8"))
    return h.digest()

def asHexStr(binary):
    return hexlify(binary).decode("ascii")

def joinLines(*lines):
    return "\n".join(l for l in lines if l)

def joinScripts(scripts, glue="\ncd \"${BOB_CWD}\"\n"):
    scripts = [ s for s in scripts if ((s is not None) and (s != "")) ]
    if scripts != []:
        return glue.join(scripts)
    else:
        return None

def sliceString(data, chunk):
    """Return iterator that slices string in "chunk" size strings."""
    def genSlice(i = 0):
        r = data[i:i+chunk]
        while len(r) > 0:
            yield r
            i += chunk
            r = data[i:i+chunk]
    return iter(genSlice())

def removePath(path):
    try:
        if os.path.lexists(path):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
    except OSError as e:
        raise BuildError("Error removing '"+path+"': " + str(e))

def emptyDirectory(path):
    try:
        if os.path.exists(path):
            for f in os.listdir(path): removePath(os.path.join(path, f))
    except OSError as e:
        raise BuildError("Error cleaning '"+path+"': " + str(e))

# Recursively merge entries of two dictonaries.
#
# Expect that both arguments have the same schema. Dictionaries are merged
# key-by-key. Lists are appended. Returns merged result.
#
# See: http://stackoverflow.com/questions/3232943
def updateDicRecursive(d, u):
    for k, v in u.items():
        if isinstance(v, collections.Mapping):
            d[k] = updateDicRecursive(d.get(k, {}), v)
        elif isinstance(v, list):
            d[k] = d.get(k, []) + v
        else:
            d[k] = u[k]
    return d

# Compare versions. Not strictly according to semver but enough for us.
def compareVersion(origLeft, origRight):
    # Strip any suffix
    left = origLeft.partition("-")[0]
    right = origRight.partition("-")[0]

    def cmp(l, r):
        if (len(l) == 0) and (len(r) == 0): return 0
        if len(l) == 0: l = ["0"]
        if len(r) == 0: r = ["0"]
        if int(l[0]) < int(r[0]):
            return -1
        elif int(l[0]) > int(r[0]):
            return 1
        else:
            return cmp(l[1:], r[1:])

    try:
        return cmp(left.split("."), right.split("."))
    except ValueError:
        raise ParseError("Cannot compare version numbers ('{}' vs. '{}'): bad format!"
                            .format(origLeft, origRight))

def isWindows():
    """Check if we run on a windows platform.

    We have to rule out MSYS(2) and Cygwin as they are advertised a POSIX but
    in fact cannot truly hide the underlying system.
    """
    if os.name == 'posix':
        p = sys.platform
        if p.startswith('msys'): return True
        if p.startswith('cygwin'): return True
        return False
    return True

### directory hashing ###

def hashFile(path):
    m = hashlib.sha1()
    try:
        with open(path, 'rb', buffering=0) as f:
            buf = f.read(16384)
            while len(buf) > 0:
                m.update(buf)
                buf = f.read(16384)
    except OSError as e:
        logging.getLogger(__name__).warning("Cannot hash file: %s", str(e))
    return m.digest()

def float2ns(v):
    return int(v * 1000000000)

class DirHasher:
    IGNORE_DIRS = frozenset([
        os.fsencode(".git"),
        os.fsencode(".portage-cache"),
        os.fsencode(".svn"),
    ])
    IGNORE_FILES = frozenset([
        os.fsencode("BaseDirList.txt"),
    ])

    class FileIndex:
        SIGNATURE        = b'BOB1'
        CACHE_ENTRY_FMT  = '=QQLqLQ20sH'
        CACHE_ENTRY_SIZE = struct.calcsize(CACHE_ENTRY_FMT)

        class Stat:
            def __init__(self):
                self.name = b""
                self.ctime = 0
                self.mtime = 0
                self.dev = 0
                self.ino = 0
                self.mode = 0
                self.size = 0
                self.digest = b''

            def __repr__(self):
                return "Stat(name={}, ctime={}, mtime={}, dev={}, ino={}, mode={}, size={}, digest={})".format(
                    self.name, self.ctime, self.mtime, self.dev, self.ino, self.mode, self.size, self.digest)

        def __init__(self, cachePath):
            self.__cachePath = cachePath
            self.__cacheDir = os.path.dirname(cachePath)

        def open(self):
            self.__inPos = 0
            self.__inPosOld = 0
            self.__outFile = None
            self.__current = DirHasher.FileIndex.Stat()
            try:
                if os.path.exists(self.__cachePath):
                    self.__inFile = open(self.__cachePath, "rb")
                    sig = self.__inFile.read(4)
                    if sig == DirHasher.FileIndex.SIGNATURE:
                        self.__mismatch = False
                        self.__inPos = self.__inPosOld = 4
                        self.__readEntry() # prefetch first entry
                    else:
                        logging.getLogger(__name__).info(
                            "Wrong signature at '%s': %s", self.__cachePath, sig)
                        self.__inFile.close()
                        self.__inFile = None
                        self.__mismatch = True
                else:
                    self.__inFile = None
                    self.__mismatch = True
            except OSError as e:
                raise BuildError("Error opening hash cache: " + str(e))

        def close(self):
            try:
                if self.__inFile:
                    self.__inFile.close()
                if self.__outFile:
                    self.__outFile.close()
                    os.replace(self.__outFile.name, self.__cachePath)
            except OSError as e:
                raise BuildError("Error closing hash cache: " + str(e))

        def __readEntry(self):
            if not self.__inFile: return False
            raw = self.__inFile.read(DirHasher.FileIndex.CACHE_ENTRY_SIZE)
            if len(raw) < DirHasher.FileIndex.CACHE_ENTRY_SIZE: return False
            e = self.__current
            (e.ctime, e.mtime, e.dev, e.ino, e.mode, e.size, e.digest,
                nameLen) = struct.unpack(DirHasher.FileIndex.CACHE_ENTRY_FMT, raw)
            e.name = self.__inFile.read(nameLen)
            self.__inPosOld = self.__inPos
            self.__inPos += DirHasher.FileIndex.CACHE_ENTRY_SIZE + nameLen
            return True

        def __writeEntry(self, name, st, digest):
            if not self.__outFile:
                self.__outFile = NamedTemporaryFile(mode="wb", dir=self.__cacheDir, delete=False)
                if self.__inFile:
                    pos = self.__inFile.tell()
                    self.__inFile.seek(0)
                    self.__outFile.write(self.__inFile.read(self.__inPosOld))
                    self.__inFile.seek(pos)
                else:
                    self.__outFile.write(DirHasher.FileIndex.SIGNATURE)
            self.__outFile.write(struct.pack(DirHasher.FileIndex.CACHE_ENTRY_FMT, float2ns(st.st_ctime),
                float2ns(st.st_mtime), st.st_dev, st.st_ino, st.st_mode, st.st_size,
                digest, len(name)))
            self.__outFile.write(name)

        def __match(self, name, st):
            while self.__current.name < name:
                if not self.__readEntry(): break
            e = self.__current
            res = ((e.name == name) and (e.ctime == float2ns(st.st_ctime)) and
                (e.mtime == float2ns(st.st_mtime)) and (e.dev == st.st_dev) and
                (e.ino == st.st_ino) and (e.mode == st.st_mode) and
                (e.size == st.st_size))
            #if not res: print("Mismatch", e.name, name, e, st)
            return res

        def check(self, prefix, name, st, process):
            if self.__match(name, st):
                digest = self.__current.digest
            else:
                digest = process(os.path.join(prefix, name) if name else prefix)
                self.__mismatch = True
            if self.__mismatch:
                self.__writeEntry(name, st, digest)
            return digest

    class NullIndex:
        def __init__(self):
            pass

        def open(self):
            pass

        def close(self):
            pass

        def check(self, prefix, name, st, process):
            return process(os.path.join(prefix, name) if name else prefix)

    def __init__(self, basePath=None, ignoreDirs=None):
        if basePath:
            self.__index = DirHasher.FileIndex(basePath)
        else:
            self.__index = DirHasher.NullIndex()
        if ignoreDirs:
            self.__ignoreDirs = DirHasher.IGNORE_DIRS | frozenset(os.fsencode(i) for i in ignoreDirs)
        else:
            self.__ignoreDirs = DirHasher.IGNORE_DIRS

    def __hashEntry(self, prefix, entry, s):
        if stat.S_ISREG(s.st_mode):
            digest = self.__index.check(prefix, entry, s, hashFile)
        elif stat.S_ISDIR(s.st_mode):
            digest = self.__hashDir(prefix, entry)
        elif stat.S_ISLNK(s.st_mode):
            digest = self.__index.check(prefix, entry, s, DirHasher.__hashLink)
        elif stat.S_ISBLK(s.st_mode) or stat.S_ISCHR(s.st_mode):
            digest = struct.pack("<L", s.st_rdev)
        elif stat.S_ISFIFO(s.st_mode):
            digest = b''
        else:
            digest = b''
            logging.getLogger(__name__).warning("Unknown file: %s", entry)

        return digest

    @staticmethod
    def __hashLink(path):
        m = hashlib.sha1()
        try:
            m.update(os.readlink(path))
        except OSError as e:
            logging.getLogger(__name__).warning("Cannot hash link: %s", str(e))
        return m.digest()

    def __hashDir(self, prefix, path=b''):
        entries = []
        try:
            dirEntries = os.listdir(os.path.join(prefix, path if path else b'.'))
        except OSError as e:
            logging.getLogger(__name__).warning("Cannot list directory: %s", str(e))
            dirEntries = []

        for f in dirEntries:
            e = os.path.join(path, f)
            try:
                s = os.lstat(os.path.join(prefix, e))
                if stat.S_ISDIR(s.st_mode):
                    # skip useless directories
                    if f in self.__ignoreDirs: continue
                    # add training '/' for directores for correct sorting
                    f = f + os.fsencode(os.path.sep)
                else:
                    # skip useless files
                    if f in DirHasher.IGNORE_FILES: continue
                entries.append((e, f, s))
            except OSError as err:
                logging.getLogger(__name__).warning("Cannot stat '%s': %s", e, str(err))
        entries = sorted(entries, key=lambda x: x[1])
        dirList = [
            (struct.pack("=L", s.st_mode) + self.__hashEntry(prefix, e, s) + f)
            for (e, f, s) in entries
        ]
        dirBlob = b"".join(dirList)
        m = hashlib.sha1()
        m.update(dirBlob)
        return m.digest()

    def hashDirectory(self, path):
        self.__index.open()
        try:
            return self.__hashDir(os.fsencode(path))
        finally:
            self.__index.close()

    def hashPath(self, path):
        path = os.fsencode(path)
        try:
            s = os.lstat(path)
        except OSError as err:
            logging.getLogger(__name__).warning("Cannot stat '%s': %s", path, str(err))
            return b''

        self.__index.open()
        try:
            return self.__hashEntry(path, b'', s)
        finally:
            self.__index.close()


def hashDirectory(path, index=None, ignoreDirs=None):
    return DirHasher(index, ignoreDirs).hashDirectory(path)

def hashPath(path, index=None, ignoreDirs=None):
    return DirHasher(index, ignoreDirs).hashPath(path)

def binStat(path):
    st = os.stat(path)
    return struct.pack('=QQLqLQ', float2ns(st.st_ctime), float2ns(st.st_mtime),
                       st.st_dev, st.st_ino, st.st_mode, st.st_size)


# There are two "magic" modules with similar functionality. Find out which one we got and adapt.
def summonMagic():
    import magic
    if hasattr(magic, 'from_file'):
        # https://pypi.python.org/pypi/python-magic
        return magic
    elif hasattr(magic, 'open'):
        # http://www.darwinsys.com/file/, in Debian as python3-magic
        class WrapMagic:
            def __init__(self):
                self.magic = magic.open(magic.NONE)
                self.magic.load()

            def from_file(self, name):
                return self.magic.file(name)
        return WrapMagic()
    else:
        raise NotImplementedError("I do not understand your magic")

### directory copy ###

def copyTree(src, dst):
    try:
        names = os.listdir(src)
        os.makedirs(dst, exist_ok=True)
    except OSError as e:
        logging.getLogger(__name__).error("Copy '%s' to '%s' failed: %s", src,
                                          dst, str(e))
        return False

    ret = True
    for name in names:
        srcname = os.path.join(src, name)
        dstname = os.path.join(dst, name)
        try:
            if os.path.isdir(srcname):
                if os.path.exists(dstname) and not os.path.isdir(dstname):
                    logging.getLogger(__name__).error(
                        "Cannon overwrite non-directory '%s' with directory '%s'",
                        dstname, srcname)
                    ret = False
                else:
                    ret = copyTree(srcname, dstname) and ret
            else:
                if os.path.lexists(dstname):
                    os.unlink(dstname)
                if os.path.islink(srcname):
                    linkto = os.readlink(srcname)
                    os.symlink(linkto, dstname)
                else:
                    shutil.copy(srcname, dstname)
        except OSError as e:
            logging.getLogger(__name__).error("Copy failed: %s", str(e))
            ret = False

    return ret


def processDefines(defs):
    defines = {}
    for define in defs:
        key, _sep, value = define.partition('=')
        defines[key] = value
    return defines


def infixBinaryOp(handler, *args, **kwargs):
    """Handy wrapper to make sure binary operator handlers are called with only
    two arguments.

    Consecutive terms with the same operator are given as batch to the handler
    by pyparsing. E.g. 'a || b || c' will be seen as: [[a, '||', b, '||', c)]].
    This wrapper will recursively chop it up so that the given handler is
    always called with only two operands as expected for a binary operator.
    """

    def wrap(s, loc, toks):
        assert len(toks) == 1, toks
        toks = toks[0]
        while len(toks) > 3:
            toks = [ wrap(s, loc, [toks[0:3]]) ] + toks[3:]
        assert len(toks) == 3
        return handler(s, loc, toks, *args, **kwargs)

    return wrap
