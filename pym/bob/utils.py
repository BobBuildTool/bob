# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BuildError, ParseError
from binascii import hexlify
from tempfile import NamedTemporaryFile, TemporaryDirectory
import collections.abc
import hashlib
import logging
import os
import re
import shutil
import stat
import struct
import sys
import sysconfig

# The stat.st_ino field is 128 bit since Python 3.12 on Windows...
def maskIno(ino):
    return (ino ^ (ino >> 64)) & 0xFFFFFFFFFFFFFFFF

def hashString(string):
    h = hashlib.md5()
    h.update(string.encode("utf8"))
    return h.digest()

def asHexStr(binary):
    return hexlify(binary).decode("ascii")

def joinLines(*lines):
    return "\n".join(l for l in lines if l)

def joinScripts(scripts, glue):
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

def quotePwsh(string):
    """Create a PowerShell string literal"""
    return "'" + string.replace("'", "''") + "'"

def escapePwsh(string):
    """Escape a string so that no meta characters are interpreted by PowerShell"""
    return string.replace('"', '`"').replace('$', '`$')

def quoteCmdExe(string):
    """Quote a string for cmd.exe to prevent interpretation of meta characters"""
    if any(c in string for c in " \"()[]{}^=;!'+,`~"):
        return '"' + string.replace('"', '""') + '"'
    else:
        return string

def removeUserFromUrl(url):
    """Remove the user information from an URL.

    recognizes scp-like syntax as used by git too. If the schema was not
    detected the original url is returned.
    """

    global __urlRE
    try:
        urlRE = __urlRE
    except NameError:
        # See rfc3986 for the allowed characters in the components
        #                              ~~~~~~~~~ scheme ~~~~~~~~   ~~~~~~~~~~~~~ user ~~~~~~~~~~~~~ ~ h+p ~ ~~~~
        __urlRE = urlRE = re.compile(r"([a-zA-Z][a-zA-Z0-9+-.]*)://([-._~a-zA-Z0-9:%!$&'()*+,;=]+@)?([^/]*)/(.*)")

    m = urlRE.fullmatch(url)
    if m is not None:
        return "{}://{}/{}".format(m.group(1), m.group(3), m.group(4))

    global __scpRE
    try:
        scpRE = __scpRE
    except NameError:
        #                              ~~~~~~~~~~~~~ user ~~~~~~~~~~~~~ ~ h+p ~~ ~~~~
        __scpRE = scpRE = re.compile(r"([-._~a-zA-Z0-9:%!$&'()*+,;=]+@)?([^/:]+):(.*)")

    m = scpRE.fullmatch(url)
    if m is not None:
        return "{}:{}".format(m.group(2), m.group(3))

    # Nothing matched
    return url

class SandboxMode:
    def __init__(self, mode):
        # normalize legacy boolean argument
        if isinstance(mode, bool):
            mode = "yes" if mode else "no"
        assert mode in ("no", "yes", "slim", "dev", "strict")

        self.mode = mode
        self.sandboxEnabled = mode in ("dev", "yes", "strict")
        self.slimSandbox = mode in ("slim", "dev", "strict")

        if mode == "dev":
            self.stablePaths = False
        elif mode == "strict":
            self.stablePaths = True
        else:
            self.stablePaths = None

    @property
    def compatMode(self):
        # pre 0.25 compatibility
        if self.mode == "no":
            return False
        elif self.mode == "yes":
            return True
        else:
            return self.mode

def removePath(path):
    if sys.platform == "win32":
        def onerror(func, path, exc):
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
    else:
        onerror = None

    try:
        if os.path.lexists(path):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onerror=onerror)
            else:
                os.unlink(path)
    except OSError as e:
        raise BuildError("Error removing '"+path+"': " + str(e))

def removePrefix(string, prefix):
    if sys.version_info >= (3, 9):
        return string.removeprefix(prefix)
    else:
        if string.startswith(prefix):
            string = string[len(prefix):]
        return string

def emptyDirectory(path):
    try:
        if os.path.exists(path):
            for f in os.listdir(path): removePath(os.path.join(path, f))
    except OSError as e:
        raise BuildError("Error cleaning '"+path+"': " + str(e))

# Python 3.13 "fixed" absolute path detection on Windows (os.path.isabs). We
# want to retain the old behaviour and strictly reject paths that are not
# relative. Reject the following:
#   * /path     (Unix, absolute path)
#   * \path     (Windows, absolute to current drive)
#   * \\path    (Windows, UNC name)
#   * C:path    (Windows, relative path to designated drive)
#   * C:\path   (Windws, fully qualified path)
def isAbsPath(path):
    prefix = path[:2].replace('\\', '/')
    return prefix.startswith('/') or prefix.startswith(':', 1)

# Recursively merge entries of two dictonaries.
#
# Expect that both arguments have a compatible schema. Dictionaries are merged
# key-by-key. Lists are appended. Returns merged result.
#
# See: http://stackoverflow.com/questions/3232943
def updateDicRecursive(d, u):
    for k, v in u.items():
        isSameType = isinstance(v, type(d.get(k)))
        if isinstance(v, collections.abc.Mapping) and isSameType:
            d[k] = updateDicRecursive(d.get(k, {}), v)
        elif isinstance(v, list) and isSameType:
            d[k] = d.get(k, []) + v
        else:
            d[k] = v
    return d

# Compare PEP 440 versions. Not strictly according to spec but enough for us.
def compareVersion(origLeft, origRight):

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
        r = re.compile(r"^(?P<version>[0-9]+(?:\.[0-9]+){0,2})(?:rc(?P<rc>[0-9]+))?(?:.dev(?P<dist>[0-9]+))?(?:\+.*)?$")
        left = r.match(origLeft).groupdict()
        right = r.match(origRight).groupdict()

        # Compare version number. If an element is missing it is assumed to be 0.
        ret = cmp(left["version"].split("."), right["version"].split("."))

        # If both versions are equal than the higher release candidate wins. A
        # version without release candidate is considered more higher.
        if ret == 0:
            lrc = 9999 if  left["rc"] is None else int( left["rc"])
            rrc = 9999 if right["rc"] is None else int(right["rc"])
            if lrc < rrc:
                ret = -1
            elif lrc > rrc:
                ret = 1

        # If we still have a tie then the smallest distance wins.
        if ret == 0:
            ldist = 0xFFFF if  left["dist"] is None else int( left["dist"])
            rdist = 0xFFFF if right["dist"] is None else int(right["dist"])
            if ldist < rdist:
                ret = -1
            elif ldist > rdist:
                ret = 1

    except Exception:
        raise ParseError("Cannot compare version numbers ('{}' vs. '{}'): bad format!"
                            .format(origLeft, origRight))
    return ret


def getPlatformString():
    return __platformString

def isMsys():
    return __isMsys

if sys.platform.startswith('msys') or sysconfig.get_platform().startswith('msys'):
    __isMsys = True
    __platformString = 'msys'
else:
    __isMsys = False
    __platformString = sys.platform

def isWindows():
    """Check if we run on a windows platform.

    We have to rule out MSYS(2) and Cygwin as they are advertised a POSIX but
    in fact cannot truly hide the underlying system.
    """
    return __isWindows

if os.name == 'posix':
    if sys.platform.startswith('msys'):
        __isWindows = True
    elif sys.platform.startswith('cygwin'):
        __isWindows = True
    else:
        __isWindows = False
else:
    __isWindows = True

def _replacePathWin32(src, dst):
    # Workaround for spurious PermissionError's on Windows.
    i = 0
    while True:
        try:
            os.replace(src, dst)
            break
        except PermissionError:
            if i >= 10: raise
            import time
            time.sleep(0.1 * i)
            i += 1

if __isWindows:
    INVALID_CHAR_TRANS = str.maketrans(':*?<>"|', '_______')
    replacePath = _replacePathWin32
else:
    INVALID_CHAR_TRANS = str.maketrans('', '')
    replacePath = os.replace


__canSymlink = None

def canSymlink():
    # cached on first call
    global __canSymlink
    if __canSymlink is not None:
        return __canSymlink

    # On Windows it depends on the SeCreateSymbolicLinkPrivilege capability if
    # it is possible to create symlinks. Try to create a symlink to see if we
    # have the privilege. Either the symlink() call fails directly or MSYS
    # silently creates a copy (unless MSYS=winsymlinks:nativestrict is set).
    if sys.platform in ('msys', 'cygwin', 'win32'):
        ret = False
        try:
            with TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "file"), "w") as f:
                    pass
                canary = os.path.join(tmp, "canary")
                os.symlink("file", canary)
                ret = os.path.islink(canary)
        except OSError:
            pass
    else:
        ret = True

    # cache result
    __canSymlink = ret
    return ret

__platformTag = None

def getPlatformTag():
    # cached on first call
    global __platformTag
    if __platformTag is not None:
        return __platformTag

    p = sys.platform
    if p == 'win32':
        ret = b'w'
    elif p in ('msys', 'cygwin'):
        ret = b'm'
    else:
        ret = b''

    # It's not given that you can symlink on Windows. Things will behave
    # differently so threat it as a separate platform.
    if p in ('msys', 'cygwin', 'win32'):
        if canSymlink():
            ret += b'l'

    # cache result
    __platformTag = ret
    return ret

def getPlatformEnvWhiteList(platform):
    """Return default environment whitelist, depending on the platform"""

    ret = set()
    if platform == 'win32':
        ret |= set(["ALLUSERSPROFILE", "APPDATA",
            "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMSPEC",
            "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
            "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE",
            "SYSTEMROOT", "TEMP", "TMP", "WINDIR"])
    else:
        ret |= set(["PATH", "TERM", "SHELL", "USER", "HOME"])

    if platform in ('cygwin', 'msys'):
        ret |= set(["ALLUSERSPROFILE", "APPDATA",
            "COMMONPROGRAMFILES", "CommonProgramFiles(x86)", "COMSPEC",
            "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
            "ProgramData", "PROGRAMFILES", "ProgramFiles(x86)", "SYSTEMDRIVE",
            "SYSTEMROOT", "TEMP", "TMP", "WINDIR"])

    return ret

__bashPath = None

def getBashPath():
    """Get path to bash.

    This is required to work around a weird behaviour on Windows when WSL is
    enabled but no distribution is installed. In this case the subprocess
    module (which internally uses CreateProcess()) cannot execute bash even
    though it's in %PATH%. Interrestingly cmd.com and powershell.exe look at
    %PATH% first and can execute bash successfully.
    """

    global __bashPath
    if __bashPath is not None:
        return __bashPath

    if sys.platform == "win32":
        import shutil
        ret = shutil.which("bash")
        if ret is None:
            raise BuildError("bash: command not found")
    else:
        ret = "bash"

    __bashPath = ret
    return ret

### directory hashing ###

def hashFile(path, hasher=hashlib.sha1):
    m = hasher()
    try:
        with open(path, 'rb', buffering=0) as f:
            buf = f.read(16384)
            while len(buf) > 0:
                m.update(buf)
                buf = f.read(16384)
    except OSError as e:
        logging.getLogger(__name__).warning("Cannot hash file: %s", str(e))
    return m.digest()

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
        SIGNATURE        = b'BOB2'
        CACHE_ENTRY_FMT  = '=qqQQLQ20sH'
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
                    replacePath(self.__outFile.name, self.__cachePath)
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
            self.__outFile.write(struct.pack(DirHasher.FileIndex.CACHE_ENTRY_FMT, st.st_ctime_ns,
                st.st_mtime_ns, st.st_dev, maskIno(st.st_ino), st.st_mode, st.st_size,
                digest, len(name)))
            self.__outFile.write(name)

        def __match(self, name, st):
            while self.__current.name < name:
                if not self.__readEntry(): break
            e = self.__current
            res = ((e.name == name) and (e.ctime == st.st_ctime_ns) and
                (e.mtime == st.st_mtime_ns) and (e.dev == st.st_dev) and
                (e.ino == maskIno(st.st_ino)) and (e.mode == st.st_mode) and
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
        self.size = 0

    def __hashEntry(self, prefix, entry, s):
        self.size += s.st_size
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
            with os.scandir(os.path.join(prefix, path if path else b'.')) as dirEntries:
                for dirEntry in dirEntries:
                    f = dirEntry.name
                    e = os.path.join(path, f)
                    try:
                        if dirEntry.is_dir(follow_symlinks=False):
                            # skip useless directories
                            if f in self.__ignoreDirs: continue
                            # add training '/' for directores for correct sorting
                            f = f + os.fsencode(os.path.sep)
                        else:
                            # skip useless files
                            if f in DirHasher.IGNORE_FILES: continue
                        entries.append((e, f, dirEntry.stat(follow_symlinks=False)))
                    except OSError as err:
                        logging.getLogger(__name__).warning("Cannot stat '%s': %s", e, str(err))
        except OSError as e:
            logging.getLogger(__name__).warning("Cannot list directory: %s", str(e))

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

def hashDirectoryWithSize(path, index=None, ignoreDirs=None):
    h = DirHasher(index, ignoreDirs)
    retHash = h.hashDirectory(path)
    retSize = h.size
    return retHash, retSize

def hashPath(path, index=None, ignoreDirs=None):
    return DirHasher(index, ignoreDirs).hashPath(path)

def binStat(path):
    st = os.stat(path)
    return struct.pack('=qqQQLQ', st.st_ctime_ns, st.st_mtime_ns,
                       st.st_dev, maskIno(st.st_ino), st.st_mode, st.st_size)


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

class __BlackHoleSet:
    def add(self, item):
        pass

def copyTree(src, dst, fileSet = __BlackHoleSet()):
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
                fileSet.add(dstname)
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

### Asyncio event loop setup

def dummy():
    pass

__startMethodSet = False
def __setStartMethod(method):
    global __startMethodSet
    if not __startMethodSet:
        import multiprocessing
        multiprocessing.set_start_method(method)
        __startMethodSet = True

def getProcessPoolExecutor():
    import multiprocessing
    import signal
    import concurrent.futures

    try:
        if sys.platform == 'win32':
            __setStartMethod('spawn')
            executor = concurrent.futures.ProcessPoolExecutor()
        else:
            # The ProcessPoolExecutor is a barely usable for our interactive use
            # case. On SIGINT any busy executor should stop. The only way how this
            # does not explode is that we ignore SIGINT before spawning the process
            # pool and re-enable SIGINT in every executor. In the main process we
            # have to ignore BrokenProcessPool errors as we will likely hit them.
            # To "prime" the process pool a dummy workload must be executed because
            # the processes are spawned lazily.
            origSigInt = signal.getsignal(signal.SIGINT)
            try:
                signal.signal(signal.SIGINT, signal.SIG_IGN)

                method = 'fork' if isWindows() else 'forkserver'
                __setStartMethod(method)
                executor = concurrent.futures.ProcessPoolExecutor()

                # fork early before process gets big
                executor.submit(dummy).result()
            finally:
                signal.signal(signal.SIGINT, origSigInt)
    except EOFError:
        # On Windows WSL1, the 'forkserver' method does not work because UNIX
        # domain sockets are not fully implemented. Fall back to the 'spawn'
        # method. See bug #562.
        multiprocessing.set_start_method('spawn', force=True)
        executor = concurrent.futures.ProcessPoolExecutor()
    except OSError as e:
        raise BuildError("Error spawning process pool: " + str(e))

    return executor

class EventLoopWrapper:
    def __init__(self):
        import asyncio

        if sys.platform == 'win32':
            self.__loop = asyncio.ProactorEventLoop()
        else:
            self.__loop = asyncio.new_event_loop()

        self.__executor = getProcessPoolExecutor()

    def __enter__(self):
        import asyncio
        asyncio.set_event_loop(self.__loop)
        return (self.__loop, self.__executor)

    def __exit__(self, exc_type, exc_value, traceback):
        import asyncio
        self.__executor.shutdown()
        self.__loop.close()
        asyncio.set_event_loop(None)


async def run(args, universal_newlines=False, errors=None, check=False,
        shell=False, retries=0, **kwargs):
    """Provide the subprocess.run() function as asyncio corouting.

    This takes care of the missing 'universal_newlines' and 'check' options.
    Everything else is passed through. Will also raise the same exceptions as
    subprocess.run() to act as a drop-in replacement.
    """
    import asyncio
    import io
    import locale
    import subprocess

    stdout = ""
    stderr = ""

    while True:
        if shell:
            proc = await asyncio.create_subprocess_shell(args, **kwargs)
        else:
            proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        stdout, stderr = await proc.communicate()

        if (proc.returncode == 0) or (retries == 0):
            break
        retries -= 1
        await asyncio.sleep(1)

    if universal_newlines and (stdout is not None):
        stdout = io.TextIOWrapper(io.BytesIO(stdout), errors=errors).read()
    if universal_newlines and (stderr is not None):
        stderr = io.TextIOWrapper(io.BytesIO(stderr), errors=errors).read()

    if check and (proc.returncode != 0):
        raise subprocess.CalledProcessError(proc.returncode, args,
            stdout, stderr)

    return subprocess.CompletedProcess(args, proc.returncode, stdout,
        stderr)

async def check_output(args, **kwargs):
    """The subprocess.check_output() call as coroutine."""
    import subprocess
    return (await run(args, check=True, stdout=subprocess.PIPE, **kwargs)).stdout

def runInEventLoop(coro):
    """Backwards compatibility stub for asyncio.run()"""
    import asyncio

    if sys.platform == 'win32':
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()

def sslNoVerifyContext():
    """Generate a SSL context that does not validate certificates."""
    import ssl
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context

# Python 3.12 tarfile compliance

def _tarExtractFilter(member, path):
    path = os.path.realpath(path)
    name = member.name

    # Strip absolute path names (GNU tar default)
    if name.startswith(('/', os.sep)):
        name = name.lstrip('/' + os.sep)
        member = member.replace(name=name, deep=False)

    # If still absolute (e.g. C:/...), bail out.
    if os.path.isabs(name):
        raise BuildError(f"Refusing to extract absolute path '{name}' from tar file.")

    # Ensure we stay in the destination
    full_name = os.path.realpath(os.path.join(path, name))
    if os.path.commonpath([full_name, path]) != path:
        raise BuildError(f"Refusing to extract '{name}' from tar file. File is outside of destination directory.")

    return member

def tarfileOpen(*args, **kwargs):
    import tarfile
    ret = tarfile.open(*args, **kwargs)
    # Set a sensible extraction filter, used by Python 3.12 and later...
    ret.extraction_filter = _tarExtractFilter
    return ret
