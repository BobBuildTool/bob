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

from .errors import ParseError
import copy
import errno
import os
import pickle

class _BobState():
    # Bump CUR_VERSION if internal state is made backwards incompatible, that is
    # older versions ob Bob will choke on the persisted state. The MIN_VERSION
    # should only be incremented if it is impossible to read such an old state.
    #
    # Version history:
    #  2 -> 3: byNameDirs: values are tuples (directory, isSourceDir)
    MIN_VERSION = 2
    CUR_VERSION = 3

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
        if os.path.exists(self.__path):
            with open(self.__path, 'rb') as f:
                state = pickle.load(f)
            if state["version"] < _BobState.MIN_VERSION:
                raise ParseError("This version of bob cannot read the build tree anymore. Sorry. :-(")
            if state["version"] > _BobState.CUR_VERSION:
                raise ParseError("This version of bob is too old for the build tree.")
            self.__byNameDirs = state["byNameDirs"]
            self.__results = state["results"]
            self.__inputs = state["inputs"]
            self.__jenkins = state.get("jenkins", {})
            self.__dirStates = state.get("dirStates", {})
            self.__buildState = state.get("buildState", {})

            # version upgrades
            if state["version"] == 2:
                self.__byNameDirs = {
                    digest : ((dir, False) if isinstance(dir, str) else dir)
                    for (digest, dir) in self.__byNameDirs.items()
                }

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
            }
            tmpFile = self.__path+".new"
            with open(tmpFile, "wb") as f:
                pickle.dump(state, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmpFile, self.__path)
            self.__dirty = False
        else:
            self.__dirty = True

    def finalize(self):
        assert (self.__asynchronous == 0) and not self.__dirty
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

    def delResultHash(self, stepDigest):
        if stepDigest in self.__results:
            del self.__results[stepDigest]
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

    def getDirectoryState(self, path, default=None):
        return copy.deepcopy(self.__dirStates.get(path, default))

    def setDirectoryState(self, path, digest):
        self.__dirStates[path] = digest
        self.__save()

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

def BobState():
    if _BobState.instance is None:
        _BobState.instance = _BobState()
    return _BobState.instance

def finalize():
    if _BobState.instance is not None:
        _BobState.instance.finalize()

