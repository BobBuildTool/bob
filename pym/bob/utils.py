# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
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

from binascii import hexlify
from tempfile import NamedTemporaryFile
import hashlib
import os
import shutil
import stat
import struct
import sys

def asHexStr(binary):
    return hexlify(binary).decode("ascii")

def colorize(string, color):
    if __onTTY:
        return "\x1b[" + color + "m" + string + "\x1b[0m"
    else:
        return string

def joinScripts(scripts):
    return "\ncd \"${BOB_CWD}\"\n".join(scripts)

def removePath(path):
    if os.path.exists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

def emptyDirectory(path):
    if os.path.exists(path):
        for f in os.listdir(path): removePath(os.path.join(path, f))

class Unbuffered(object):
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def __getattr__(self, attr):
        return getattr(self.stream, attr)

def compareVersion(left, right):
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

     return cmp(left.split("."), right.split("."))

### directory hashing ###

def hashFile(path):
    m = hashlib.sha1()
    try:
        with open(path, 'rb', buffering=0) as f:
            buf = f.read(16384)
            while len(buf) > 0:
                m.update(buf)
                buf = f.read(16384)
    except IOError as e:
        print("      Error hashing file:", str(e))
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
        CACHE_ENTRY_FMT  = '=QQLLLQ20sH'
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

        def __init__(self, cachePath):
            self.__cachePath = cachePath
            self.__cacheDir = os.path.dirname(cachePath)

        def open(self):
            self.__inPos = 0
            self.__inPosOld = 0
            self.__outFile = None
            if os.path.exists(self.__cachePath):
                self.__inFile = open(self.__cachePath, "rb")
                self.__mismatch = False
            else:
                self.__inFile = None
                self.__mismatch = True
            self.__current = DirHasher.FileIndex.Stat()

        def close(self):
            if self.__inFile:
                self.__inFile.close()
            if self.__outFile:
                self.__outFile.close()
                os.rename(self.__outFile.name, self.__cachePath)

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
            #if not res: print("Mismatch", e.name, name)
            return res

        def check(self, prefix, name, st, process):
            if self.__match(name, st):
                digest = self.__current.digest
            else:
                digest = process(os.path.join(prefix, name))
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
            return process(os.path.join(prefix, name))

    def __init__(self, basePath=None):
        if basePath:
            self.__index = DirHasher.FileIndex(basePath)
        else:
            self.__index = DirHasher.NullIndex()

    def __hashEntry(self, prefix, entry, file, s):
        if stat.S_ISREG(s.st_mode):
            digest = self.__index.check(prefix, entry, s, hashFile)
        elif stat.S_ISDIR(s.st_mode):
            digest = self.__hashDir(prefix, entry)
        elif stat.S_ISLNK(s.st_mode):
            digest = self.__index.check(prefix, entry, s, DirHasher.__hashLink)
        else:
            raise Exception("Unsopported file: "+repr(s))

        return struct.pack("=L", s.st_mode) + digest + file

    @staticmethod
    def __hashLink(path):
        m = hashlib.sha1()
        m.update(os.readlink(path))
        return m.digest()

    def __hashDir(self, prefix, path=b''):
        entries = []
        for f in os.listdir(os.path.join(prefix, path if path else b'.')):
            e = os.path.join(path, f)
            s = os.lstat(os.path.join(prefix, e))
            if stat.S_ISDIR(s.st_mode):
                # skip useless directories
                if f in DirHasher.IGNORE_DIRS: continue
                # add training '/' for directores for correct sorting
                f = f + os.fsencode(os.path.sep)
            else:
                # skip useless files
                if f in DirHasher.IGNORE_FILES: continue
            entries.append((e, f, s))
        entries = sorted(entries, key=lambda x: x[1])
        dirList = [ self.__hashEntry(prefix, e, f, s) for (e, f, s) in entries ]
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

def hashDirectory(path, index=None):
    return DirHasher(index).hashDirectory(path)

# module initialization

__onTTY = False
if sys.stdout.isatty() and sys.stderr.isatty():
    __onTTY = True

