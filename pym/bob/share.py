# Bob build tool
# Copyright (C) 2021  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import BuildError
from .tty import Warn, WarnOnce
from .utils import asHexStr, hashDirectoryWithSize, isWindows
import errno
import os, os.path
import json
import shutil
import sys
import tempfile

warnRepoSize = WarnOnce("The shared repository is over its quota. Run 'bob clean --shared' to free disk space!")
warnGcDidNotHelp = WarnOnce("The automatic garbage collection of the shared repository was unable to free enough space. Run 'bob clean --shared' manually.")
warnNoShareConfigured = Warn("No shared directory configured! Nothing cleaned.")

if sys.platform == 'win32':
    import msvcrt
    def lockFile(fd, exclusive):
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_RLCK, 0x100000)
    def unlockFile(fd):
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 0x100000)
else:
    import fcntl
    def lockFile(fd, exclusive):
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    def unlockFile(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)

# The first two generations were used by the Jenkins backend.
SHARED_GENERATION = '-3'

class OpenLocked:

    def __init__(self, fileName, mode, exclusive):
        self.fileName = fileName
        self.mode = mode
        self.exclusive = exclusive

    def __enter__(self):
        self.fd = open(self.fileName, self.mode)
        try:
            lockFile(self.fd, self.exclusive)
        except:
            self.fd.close()
            raise
        return self.fd

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            unlockFile(self.fd)
        finally:
            self.fd.close()


def sameWorkspace(link, sharePath):
    """Is the workspace shared and points to sharePath?

    Depending on the OS the shared package could be a symlink to the shared
    place or have a place-holder file with the path. The workspace could also
    be something completely different by now.
    """
    try:
        if os.path.islink(link):
            dst = os.readlink(link)
        elif os.path.isfile(link):
            with open(link) as f:
                dst = f.read(0x10000)
        else:
            return False
        return os.path.samefile(dst, sharePath)
    except OSError as e:
        raise BuildError("Error inspecting workspace: " + str(e))

def checkUnused(pkgMeta, pkgPath):
    pkgWorkspace = os.path.join(pkgPath, "workspace")
    return all((not sameWorkspace(user, pkgWorkspace)) for user in pkgMeta.get("users", []))

class NullShare:
    def __init__(self):
        self.quota = 0

    def remoteName(self, buildId):
        return ""

    def canInstall(self):
        return False

    def useSharedPackage(self, workspace, buildId):
        return None, None

    def installSharedPackage(self, workspace, buildId, sharedHash, mayMove):
        return None, False

    def gc(self, pruneUsed, pruneUnused, dryRun=False, progress=lambda x: None, newPkg=None):
        warnNoShareConfigured.warn()
        return None

    def contains(self, buildId):
        return False


class LocalShare:
    UNITS = [("KiB", 1024**1), ("MiB", 1024**2), ("GiB", 1024**3), ("TiB", 1024**4),
             ("K",   1024**1), ("M",   1024**2), ("G",   1024**3), ("T",   1024**4),
             ("KB",  1000**1), ("MB",  1000**2), ("GB",  1000**3), ("TB",  1000**4)]

    def __init__(self, spec):
        self.__path = os.path.abspath(os.path.expanduser(spec['path']))
        quota = spec.get('quota')
        if isinstance(quota, str):
            for ending, factor in self.UNITS:
                if quota.endswith(ending): break
            else:
                ending = None
                factor = 1

            if ending is not None:
                quota = quota[:-len(ending)]
            quota = int(quota) * factor
        self.__quota = quota
        self.__autoClean = spec.get('autoClean', True)

    def __buildPath(self, buildId):
        buildId = asHexStr(buildId) + SHARED_GENERATION
        return os.path.join(*[self.__path, buildId[0:2], buildId[2:4], buildId[4:]])

    def __addPackage(self, buildId, size):
        def update(f):
            meta = json.load(f)
            meta.setdefault("pkgs", {})[asHexStr(buildId)] = size
            f.seek(0)
            f.truncate()
            json.dump(meta, f)

            ret = 0
            for v in meta["pkgs"].values(): ret += v
            return ret

        fn = os.path.join(self.__path, "repo.json")
        try:
            try:
                # Usual case: update with lock
                with OpenLocked(fn, "r+", True) as f:
                    return update(f)
            except FileNotFoundError:
                # Unusual case: does not exist yet -> create atomically.
                try:
                    with OpenLocked(fn, "x", True) as f:
                        json.dump({"pkgs" : {asHexStr(buildId) : size}}, f)
                        return size
                except FileExistsError:
                    # Almost impossible case: lost creation race -> update
                    with OpenLocked(fn, "r+", True) as f:
                        return update(f)
        except OSError as e:
            raise BuildError("Error updating shared repo: "+str(e))

    def remoteName(self, buildId):
        return self.__buildPath(buildId)

    def canInstall(self):
        return True

    def useSharedPackage(self, workspace, buildId):
        path = self.__buildPath(buildId)
        workspace = os.path.abspath(workspace)
        try:
            with OpenLocked(os.path.join(self.__path, "repo.json"), "r", False):
                pkgMetaFile = os.path.join(path, "pkg.json")
                with OpenLocked(pkgMetaFile, "r+", True) as f:
                    if not os.path.isdir(path):
                        return None, None # concurrent gc wiped the package :(
                    meta = json.load(f)
                    sharedHash = bytes.fromhex(meta.get("hash", ""))
                    if len(sharedHash) != 20:
                        raise BuildError("Invalid shared result hash in " + path)

                    # Make sure our workspace is recorded in the metainfo.
                    users = meta.setdefault("users", [])
                    if workspace not in users:
                        users.append(workspace)
                        f.seek(0)
                        f.truncate()
                        json.dump(meta, f)
                    else:
                        os.utime(pkgMetaFile)
        except FileNotFoundError:
            return None, None
        except OSError as e:
            raise BuildError("Could not read shared result meta info: " + str(e))
        except (json.JSONDecodeError, ValueError) as e:
            raise BuildError("Corrupt meta info in {}: {}".format(path, str(e)))

        return path, sharedHash

    def installSharedPackage(self, workspace, buildId, sharedHash, mayMove):
        # Quick check: was somebody faster?
        sharedPath = self.__buildPath(buildId)
        if os.path.isdir(sharedPath):
            return sharedPath, False

        # Prepare everyting in temporary directory next to the shared packages
        # to atomically "install" the whole package with a single move. Can
        # still lose the race at the final rename!
        repoSize = 0
        try:
            os.makedirs(os.path.dirname(sharedPath), exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.__path) as tmpDir:
                tmpSharedPath = os.path.join(tmpDir, "pkg")
                os.mkdir(tmpSharedPath)
                shutil.copyfile(os.path.join(workspace, "..", "audit.json.gz"),
                                os.path.join(tmpSharedPath, "audit.json.gz"))
                cacheBinSrc = os.path.join(workspace, "..", "cache.bin")
                cacheBinDst = os.path.join(tmpDir, "cache.bin")
                # Might not exist if workspace was emtpy
                if os.path.exists(cacheBinSrc):
                    shutil.copyfile(cacheBinSrc, cacheBinDst)
                if mayMove:
                    shutil.move(workspace, tmpSharedPath)
                else:
                    shutil.copytree(workspace, os.path.join(tmpSharedPath, "workspace"),
                        symlinks=True)

                # Cerify the result hash and count file system size. The user
                # could have an incompatible file system at the destination.
                # The storage size is used for garbage collection later...
                actualHash, actualSize = hashDirectoryWithSize(
                    os.path.join(tmpSharedPath, "workspace"),
                    cacheBinDst)
                if actualHash != sharedHash:
                    raise BuildError("The shared package hash changed at destination. Incompatible file system?")
                with open(os.path.join(tmpSharedPath, "pkg.json"), "w") as f:
                    json.dump({
                        "hash" : asHexStr(sharedHash),
                        "size" : actualSize,
                        "users" : [ os.path.abspath(workspace) ]
                    }, f)

                # Atomic install. Loosing the race is not considered a problem.
                try:
                    os.rename(tmpSharedPath, sharedPath)
                except OSError as e:
                    if e.errno in (errno.ENOTEMPTY, errno.EEXIST):
                        return sharedPath, False
                    raise

                # Add to quota
                repoSize = self.__addPackage(buildId, actualSize)
        except OSError as e:
            raise BuildError("Error installing shared package: " + str(e))

        if (self.__quota is not None) and (repoSize > self.__quota):
            if self.__autoClean:
                repoSize = self.gc(False, False, newPkg=sharedPath)
                if repoSize > self.__quota: warnGcDidNotHelp.show(self.__path)
            else:
                warnRepoSize.show(self.__path)

        return sharedPath, True

    def gc(self, pruneUsed, pruneUnused, dryRun=False, progress=lambda x: None, newPkg=None):
        if (self.__quota is None) and not pruneUnused:
            return None
        if not os.path.isdir(self.__path):
            return 0

        # Create a temporary attic directory. All garbage collected packages
        # are moved there to delete them without holding any locks.
        repoSize = 0
        with tempfile.TemporaryDirectory(dir=self.__path) as attic:
            # Get exclusive lock on repository. Prohibits further installations
            # and usage of packages.
            candidates = []
            with OpenLocked(os.path.join(self.__path, "repo.json"), "r+", True) as rf:
                repoMeta = json.load(rf)

                # Scan all packages
                for pkg, size in repoMeta.get("pkgs", {}).items():
                    repoSize += size
                    pkgPath = self.__buildPath(bytes.fromhex(pkg))
                    try:
                        with OpenLocked(os.path.join(pkgPath, "pkg.json"), "r", False) as pf:
                            pkgMeta = json.load(pf)
                            pkgTime = os.fstat(pf.fileno()).st_mtime_ns
                            pkgUnused = checkUnused(pkgMeta, pkgPath) and (pkgPath != newPkg)
                        if pkgUnused or pruneUsed:
                            candidates.append((pkgUnused, pkgTime, size, pkg))
                    except FileNotFoundError:
                        pass

                # Move all candidates to the attic until we are under the quota
                for pkgUnused, _, pkgSize, pkgBuildId in sorted(candidates):
                    if (not pkgUnused or not pruneUnused) and (repoSize <= self.__quota):
                        break
                    pkgPath = self.__buildPath(bytes.fromhex(pkgBuildId))
                    repoSize -= pkgSize
                    progress(pkgPath)
                    if not dryRun:
                        os.rename(pkgPath, os.path.join(attic, pkgBuildId))
                        del repoMeta["pkgs"][pkgBuildId]
                        rf.seek(0)
                        rf.truncate()
                        json.dump(repoMeta, rf)

        return repoSize

    def contains(self, buildId):
        sharedPath = self.__buildPath(buildId)
        return os.path.isdir(sharedPath)

    @property
    def quota(self):
        return self.__quota

def getShare(spec):
    if 'path' in spec:
        return LocalShare(spec)
    else:
        return NullShare()
