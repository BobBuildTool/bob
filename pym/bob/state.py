# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import ParseError
from .tty import WarnOnce
import copy
import errno
import os
import pickle
import sqlite3

warnNoAttic = WarnOnce(
    "Project was created by old Bob version. Attic directories listing will be incomplete.",
    help="Attic directires were not tracked by Bob version 0.14 and below.")

class _BobState():
    # Bump CUR_VERSION if internal state is made backwards incompatible, that is
    # older versions ob Bob will choke on the persisted state. The MIN_VERSION
    # should only be incremented if it is impossible to read such an old state.
    #
    # Version history:
    #  2 -> 3: byNameDirs: values are tuples (directory, isSourceDir)
    #  3 -> 4: jenkins job names are lower case
    #  4 -> 5: build state stores step kind (checkout-step vs. others)
    #  5 -> 6: build state stores predicted live-build-ids too
    #  6 -> 7: amended directory state for source steps, store attic directories
    #  7 -> 8: normalize attic directories
    MIN_VERSION = 2
    CUR_VERSION = 8

    VERSION_SINCE_ATTIC_TRACKED = 7

    instance = None
    def __init__(self):
        self.__path = ".bob-state.pickle"
        self.__byNameDirs = {}
        self.__results = {}
        self.__inputs = {}
        self.__jenkins = {}
        self.__asynchronous = 0
        self.__dirty = False
        self.__dirStates = {}
        self.__buildState = {}
        self.__lock = None
        self.__buildIdCache = None
        self.__variantIds = {}
        self.__atticDirs = {}
        self.__createdWithVersion = self.CUR_VERSION

        # lock state
        lockFile = ".bob-state.lock"
        try:
            fd = os.open(lockFile, os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise ParseError("Workspace state locked by other Bob instance!",
                    help="You probably execute Bob concurrently in the same workspace. "
                         "Delete '"+lockFile+"' if Bob crashed or was killed previously "
                         "to get rid of this error.")
            else:
                print("Warning: cannot lock workspace:", str(e))
        else:
            self.__lock = lockFile
            os.close(fd)

        # load state if it exists
        try:
            if os.path.exists(self.__path):
                try:
                    with open(self.__path, 'rb') as f:
                        state = pickle.load(f)
                except OSError as e:
                    raise ParseError("Error loading workspace state: " + str(e))
                except pickle.PickleError as e:
                    raise ParseError("Error decoding workspace state: " + str(e))

                if state["version"] < _BobState.MIN_VERSION:
                    raise ParseError("This version of Bob cannot read the workspace anymore. Sorry. :-(",
                                     help="This workspace was created by an older version of Bob that is no longer supported.")
                if state["version"] > _BobState.CUR_VERSION:
                    raise ParseError("This version of Bob is too old for the workspace.",
                                     help="A more recent version of Bob was previously used in this workspace. You have to use that version instead.")
                self.__byNameDirs = state["byNameDirs"]
                self.__results = state["results"]
                self.__inputs = state["inputs"]
                self.__jenkins = state.get("jenkins", {})
                self.__dirStates = state.get("dirStates", {})
                self.__buildState = state.get("buildState", {})
                self.__variantIds = state.get("variantIds", {})
                self.__atticDirs = state.get("atticDirs", {})
                self.__createdWithVersion = state.get("createdWithVersion", 0)

                # version upgrades
                if state["version"] == 2:
                    self.__byNameDirs = {
                        digest : ((dir, False) if isinstance(dir, str) else dir)
                        for (digest, dir) in self.__byNameDirs.items()
                    }

                if state["version"] <= 3:
                    for j in self.__jenkins.values():
                        jobs = j["jobs"]
                        j["jobs"] = { k.lower() : v for (k,v) in jobs.items() }

                if state["version"] <= 4:
                    self.__buildState = { path : (vid, False)
                        for path, vid in self.__buildState.items() }

                if state["version"] <= 5:
                    self.__buildState = {
                        'wasRun' : self.__buildState,
                        'predictedBuidId' : {}
                    }
                if state["version"] <= 7:
                    self.__atticDirs = { os.path.normpath(k) : v
                        for k, v in self.__atticDirs.items() }
        except:
            self.finalize()
            raise

    def __save(self):
        if self.__asynchronous == 0:
            state = {
                "version" : _BobState.CUR_VERSION,
                "byNameDirs" : self.__byNameDirs,
                "results" : self.__results,
                "inputs" : self.__inputs,
                "jenkins" : self.__jenkins,
                "dirStates" : self.__dirStates,
                "buildState" : self.__buildState,
                "variantIds" : self.__variantIds,
                "atticDirs" : self.__atticDirs,
                "createdWithVersion" : self.__createdWithVersion,
            }
            tmpFile = self.__path+".new"
            try:
                with open(tmpFile, "wb") as f:
                    pickle.dump(state, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmpFile, self.__path)
            except OSError as e:
                raise ParseError("Error saving workspace state: " + str(e))
            self.__dirty = False
        else:
            self.__dirty = True

    def __openBIdCache(self):
        if self.__buildIdCache is None:
            try:
                self.__buildIdCache = sqlite3.connect(".bob-buildids.sqlite3", isolation_level=None).cursor()
                self.__buildIdCache.execute("CREATE TABLE IF NOT EXISTS buildids(key PRIMARY KEY, value)")
                self.__buildIdCache.execute("CREATE TABLE IF NOT EXISTS fingerprints(key PRIMARY KEY, value)")
                self.__buildIdCache.execute("BEGIN")
            except sqlite3.Error as e:
                self.__buildIdCache = None
                raise ParseError("Cannot access buildid cache: " + str(e))

    def finalize(self):
        assert (self.__asynchronous == 0) and not self.__dirty
        if self.__buildIdCache is not None:
            try:
                self.__buildIdCache.execute("END")
                self.__buildIdCache.close()
                self.__buildIdCache.connection.close()
                self.__buildIdCache = None
            except sqlite3.Error as e:
                print(colorize("Warning: cannot commit buildid cache: "+str(e), "33"),
                    file=stderr)
        if self.__lock:
            try:
                os.unlink(self.__lock)
            except FileNotFoundError:
                from .tty import colorize
                from sys import stderr
                print(colorize("Warning: lock file was deleted while Bob was still running!", "33"),
                    file=stderr)
            except OSError as e:
                from .tty import colorize
                from sys import stderr
                print(colorize("Warning: cannot unlock workspace: "+str(e), "33"),
                    file=stderr)

    def setAsynchronous(self):
        self.__asynchronous += 1

    def setSynchronous(self):
        self.__asynchronous -= 1
        assert self.__asynchronous >= 0
        if (self.__asynchronous == 0) and self.__dirty:
            self.__save()

    def getByNameDirectory(self, baseDir, digest, isSourceDir):
        if digest in self.__byNameDirs:
            return self.__byNameDirs[digest][0]
        else:
            num = self.__byNameDirs.setdefault(baseDir, 0) + 1
            res = "{}/{}".format(baseDir, num)
            self.__byNameDirs[baseDir] = num
            self.__byNameDirs[digest] = (res, isSourceDir)
            self.__save()
            return res

    def getExistingByNameDirectory(self, digest):
        if digest in self.__byNameDirs:
            return self.__byNameDirs[digest][0]
        else:
            return None

    def getAllNameDirectores(self):
        return [ d for d in self.__byNameDirs.values() if isinstance(d, tuple) ]

    def getResultHash(self, stepDigest):
        return self.__results.get(stepDigest)

    def setResultHash(self, stepDigest, hash):
        if self.getResultHash(stepDigest) != hash:
            self.__results[stepDigest] = hash
            self.__save()

    def getInputHashes(self, path):
        return self.__inputs.get(path)

    def setInputHashes(self, path, hashes):
        if self.getInputHashes(path) != hashes:
            self.__inputs[path] = hashes
            self.__save()

    def delInputHashes(self, path):
        if path in self.__inputs:
            del self.__inputs[path]
            self.__save()

    def getDirectories(self):
        return list(self.__dirStates.keys())

    def getDirectoryState(self, path, isSourceDir):
        ret = copy.deepcopy(self.__dirStates.get(path, {} if isSourceDir else None))
        if isSourceDir:
            # convert from old format if necessary
            ret = { k : v if isinstance(v, tuple) else (v, None)
                for k, v in ret.items() }
        return ret

    def setDirectoryState(self, path, digest):
        """Store state information about a directory.

        For source directories:     Dict[path : Union[str, None], state : Tuple[digest:bytes, spec:Any]]
        For build directories:      list
        For pacakge directories:    bytes
        """
        self.__dirStates[path] = digest
        self.__save()

    def delDirectoryState(self, path):
        self.resetWorkspaceState(path, None)

    def getVariantId(self, path):
        return self.__variantIds.get(path)

    def setVariantId(self, path, variantId):
        if self.getVariantId(path) != variantId:
            self.__variantIds[path] = variantId
            self.__save()

    def resetWorkspaceState(self, path, dirState):
        needSave = False
        if path in self.__results:
            del self.__results[path]
            needSave = True
        if path in self.__inputs:
            del self.__inputs[path]
            needSave = True
        if self.__dirStates.get(path) != dirState:
            if dirState is None:
                del self.__dirStates[path]
            else:
                self.__dirStates[path] = dirState
            needSave = True
        if path in self.__variantIds:
            del self.__variantIds[path]
            needSave = True
        if needSave:
            self.__save()

    def setAtticDirectoryState(self, path, state):
        self.__atticDirs[os.path.normpath(path)] = state
        self.__save()

    def getAtticDirectoryState(self, path):
        if self.__createdWithVersion < self.VERSION_SINCE_ATTIC_TRACKED:
            warnNoAttic.warn()
        return copy.deepcopy(self.__atticDirs.get(path))

    def delAtticDirectoryState(self, path):
        if path in self.__atticDirs:
            del self.__atticDirs[path]
            self.__save()

    def getAtticDirectories(self):
        if self.__createdWithVersion < self.VERSION_SINCE_ATTIC_TRACKED:
            warnNoAttic.warn()
        return list(self.__atticDirs.keys())

    def getAllJenkins(self):
        return self.__jenkins.keys()

    def addJenkins(self, name, config):
        self.__jenkins[name] = {
            "config" : copy.deepcopy(config),
            "jobs" : {},
            "byNameDirs" : {},
        }
        self.__save()

    def delJenkins(self, name):
        if name in self.__jenkins:
            del self.__jenkins[name]
            self.__save()

    def getJenkinsByNameDirectory(self, jenkins, baseDir, digest):
        byNameDirs = self.__jenkins[jenkins].setdefault('byNameDirs', {})
        if digest in byNameDirs:
            return byNameDirs[digest]
        else:
            num = byNameDirs.setdefault(baseDir, 0) + 1
            res = "{}/{}".format(baseDir, num)
            byNameDirs[baseDir] = num
            byNameDirs[digest] = res
            self.__save()
            return res

    def getJenkinsConfig(self, name):
        return copy.deepcopy(self.__jenkins[name]["config"])

    def setJenkinsConfig(self, name, config):
        self.__jenkins[name]["config"] = copy.deepcopy(config)
        self.__save()

    def getJenkinsAllJobs(self, name):
        return set(self.__jenkins[name]["jobs"].keys())

    def addJenkinsJob(self, jenkins, job, jobConfig):
        self.__jenkins[jenkins]["jobs"][job] = copy.deepcopy(jobConfig)
        self.__save()

    def delJenkinsJob(self, jenkins, job):
        del self.__jenkins[jenkins]["jobs"][job]
        self.__save()

    def getJenkinsJobConfig(self, jenkins, job):
        return copy.deepcopy(self.__jenkins[jenkins]['jobs'][job])

    def setJenkinsJobConfig(self, jenkins, job, jobConfig):
        self.__jenkins[jenkins]['jobs'][job] = copy.deepcopy(jobConfig)
        self.__save()

    def setBuildState(self, digest2Dir):
        self.__buildState = copy.deepcopy(digest2Dir)
        self.__save()

    def getBuildState(self):
        return copy.deepcopy(self.__buildState)

    def getBuildId(self, key):
        self.__openBIdCache()
        try:
            self.__buildIdCache.execute("SELECT value FROM buildids WHERE key=?", (key,))
            ret = self.__buildIdCache.fetchone()
            return ret and ret[0]
        except sqlite3.Error as e:
            raise ParseError("Cannot access buildid cache: " + str(e))

    def setBuildId(self, key, val):
        self.__openBIdCache()
        try:
            self.__buildIdCache.execute("INSERT OR REPLACE INTO buildids VALUES (?, ?)", (key, val))
        except sqlite3.Error as e:
            raise ParseError("Cannot access buildid cache: " + str(e))

    def delBuildId(self, key):
        self.__openBIdCache()
        try:
            self.__buildIdCache.execute("DELETE FROM buildids WHERE key=?", (key,))
        except sqlite3.Error as e:
            raise ParseError("Cannot access buildid cache: " + str(e))

    def getFingerprint(self, key):
        self.__openBIdCache()
        try:
            self.__buildIdCache.execute("SELECT value FROM fingerprints WHERE key=?", (key,))
            ret = self.__buildIdCache.fetchone()
            return ret and ret[0]
        except sqlite3.Error as e:
            raise ParseError("Cannot access fingerprint cache: " + str(e))

    def setFingerprint(self, key, val):
        self.__openBIdCache()
        try:
            self.__buildIdCache.execute("INSERT OR REPLACE INTO fingerprints VALUES (?, ?)", (key, val))
        except sqlite3.Error as e:
            raise ParseError("Cannot access fingerprint cache: " + str(e))

def BobState():
    if _BobState.instance is None:
        _BobState.instance = _BobState()
    return _BobState.instance

def finalize():
    if _BobState.instance is not None:
        _BobState.instance.finalize()
        _BobState.instance = None

