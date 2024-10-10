# Bob build tool
# Copyright (C) 2016  TechniSat Digital GmbH
#
# SPDX-License-Identifier: GPL-3.0-or-later

from .errors import ParseError
from .stringparser import isTrue
from .tty import colorize, WarnOnce, WARNING
from .utils import replacePath, getPlatformString, SandboxMode
import copy
import errno
import os
import pickle
import sqlite3
import struct
import sys
import zlib

warnNoAttic = WarnOnce(
    "Project was created by old Bob version. Attic directories listing will be incomplete.",
    help="Attic directires were not tracked by Bob version 0.14 and below.")

class DigestAdder:
    """Append a checksum by compuing it on-the-fly"""
    def __init__(self, fd):
        self.fd = fd
        self.csum = 1

    def write(self, data):
        self.csum = zlib.adler32(data, self.csum)
        return self.fd.write(data)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.fd.write(struct.pack("=L", self.csum))
        return False

class JenkinsConfig:
    def __init__(self, url="", uuid=None):
        self.reset()
        self.url = url
        self.uuid = uuid

    @classmethod
    def load(cls, config):
        self = cls()
        self.roots = config.get("roots", []).copy()
        self.prefix = config.get("prefix", "")
        self.nodes = config.get("nodes", "")
        self.defines = config.get("defines", {}).copy()
        self.download = config.get("download", False)
        self.upload = config.get("upload", False)
        self.__sandbox = SandboxMode(config.get("sandbox", True))
        self.credentials = config.get("credentials", None)
        self.clean = config.get("clean", True)
        self.keep = config.get("keep", False)
        self.authtoken = config.get("authtoken", None)
        self.shortdescription = config.get("shortdescription", False)
        self.uuid = config.get("uuid")

        if "hostPlatform" in config:
            self.hostPlatform = config["hostPlatform"]
        else:
            self.hostPlatform = "msys" if config.get("windows", False) else "linux"

        self.__url = config.get("url").copy()
        self.__options = config.get("options", {}).copy()
        return self

    def dump(self):
        return {
                "authtoken" : self.authtoken,
                "clean" : self.clean,
                "credentials" : self.credentials,
                "defines" : self.defines,
                "download" : self.download,
                "windows" : self.windows,
                "hostPlatform" : self.hostPlatform,
                "keep" : self.keep,
                "nodes" : self.nodes,
                "options" : self.__options,
                "prefix" : self.prefix,
                "roots" : self.roots,
                "sandbox" : self.__sandbox.compatMode,
                "shortdescription" : self.shortdescription,
                "upload" : self.upload,
                "url" : self.__url,
                "uuid" : self.uuid,
            }

    def reset(self):
        self.roots = []
        self.prefix = ""
        self.nodes = ""
        self.defines = {}
        self.download = False
        self.upload = False
        self.__sandbox = SandboxMode(True)
        self.credentials = None
        self.clean = True
        self.keep = False
        self.authtoken = None
        self.shortdescription = False
        self.hostPlatform = getPlatformString()
        self.__options = {}

    @property
    def sandbox(self):
        return self.__sandbox

    @sandbox.setter
    def sandbox(self, mode):
        if isinstance(mode, SandboxMode):
            self.__sandbox = mode
        else:
            self.__sandbox = SandboxMode(mode)

    @property
    def url(self):
        url = self.__url
        if url.get('username'):
            userPass = url['username']
            if url.get('password'):
                userPass += ":" + url['password']
            userPass += "@"
        else:
            userPass = ""
        return "{}://{}{}{}{}".format(url['scheme'], userPass, url['server'],
            ":{}".format(url['port']) if url.get('port') else "", url['path'])

    @url.setter
    def url(self, url):
        import urllib
        url = urllib.parse.urlparse(url)
        urlPath = url.path
        if not urlPath.endswith("/"): urlPath = urlPath + "/"

        self.__url = {
            "scheme" : url.scheme,
            "server" : url.hostname,
            "port" : url.port,
            "path" : urlPath,
            "username" : url.username,
            "password" : url.password,
        }

    def setOption(self, key, value, errorHandler):
        if key == "artifacts.copy":
            if value not in ("jenkins", "archive"):
                errorHandler("Invalid option for artifacts.copy. Only 'archive' and 'jenkins' are allowed!")
        elif key.startswith("audit.meta."):
            import re
            if not re.fullmatch(r"[0-9A-Za-z._-]+", key):
                errorHandler("Invalid audit meta variable name: " + key)
        elif key in ("jobs.gc.deps.artifacts", "jobs.gc.deps.builds",
                     "jobs.gc.root.artifacts", "jobs.gc.root.builds"):
            try:
                int(value)
            except ValueError:
                errorHandler("Invalid option '{}': '{}'".format(key, val))
        elif key == "jobs.isolate":
            import re
            try:
                re.compile(value)
            except re.error as e:
                errorHandler("Invalid jobs.isolate regex '{}': {}".format(e.pattern, e))
        elif key == "jobs.policy":
            if value not in ("stable", "unstable", "always"):
                errorHandler("'jobs.policy' extended option has unsupported value!");
        elif key == "jobs.update":
            if value not in ("always", "description", "lazy"):
                errorHandler("'jobs.update' extended option has unsupported value!");
        elif key == "scm.always-checkout":
            if value.lower() not in ("0", "false", "1", "true"):
                errorHandler("scm.always-checkout must be any of: 0/false/1/true")
        elif key == "scm.git.shallow":
            try:
                num = int(value)
            except ValueError:
                errorHandler("scm.git.shallow must be an integer")
            if num < 0:
                errorHandler("scm.git.shallow must not be a negative integer")
        elif key == "scm.git.timeout":
            try:
                num = int(value)
            except ValueError:
                errorHandler("scm.git.timeout must be an integer")
            if num <= 0:
                errorHandler("scm.git.timeout must be a positive integer")
        elif key == "scm.ignore-hooks":
            if value.lower() not in ("0", "false", "1", "true"):
                errorHandler("scm.ignore-hooks must be any of: 0/false/1/true")
        elif key == "shared.quota":
            import re
            if not re.match(r'^[0-9]+([KMGT](i?B)?)?$', value):
                errorHandler("Invalid 'shared.quota' option")
        elif key in ("scm.poll", "shared.dir"):
            pass
        else:
            errorHandler("Unknown extended option" + key)

        self.__options[key] = value

    def delOption(self, opt):
        if opt in self.__options:
            del self.__options[opt]

    def getOptions(self):
        return self.__options

    @property
    def urlWithoutCredentials(self):
        url = self.__url
        return "{}://{}{}{}".format(url['scheme'], url['server'],
            ":{}".format(url['port']) if url.get('port') else "", url['path'])

    @property
    def urlUsername(self):
        return self.__url.get("username")

    @urlUsername.setter
    def urlUsername(self, username):
        self.__url["username"] = username

    @property
    def urlPassword(self):
        return self.__url.get("password")

    @urlPassword.setter
    def urlPassword(self, password):
        self.__url["password"] = password

    @property
    def artifactsCopy(self):
        return self.__options.get("artifacts.copy", "jenkins")

    @property
    def jobsIsolate(self):
        return self.__options.get("jobs.isolate")

    @property
    def jobsPolicy(self):
        return self.__options.get("jobs.policy", "stable")

    @property
    def jobsUpdate(self):
        return self.__options.get('jobs.update', "always")

    @property
    def scmAlwaysCheckout(self):
        return isTrue(self.__options.get("scm.always-checkout", "1"))

    @property
    def scmGitShallow(self):
        return self.__options.get("scm.git.shallow")

    @property
    def scmGitTimeout(self):
        return self.__options.get("scm.git.timeout")

    @property
    def scmIgnoreHooks(self):
        return isTrue(self.__options.get("scm.ignore-hooks", "0"))

    @property
    def scmPoll(self):
        return self.__options.get("scm.poll")

    @property
    def sharedDir(self):
        return self.__options.get("shared.dir", "${JENKINS_HOME}/bob")

    @property
    def sharedQuota(self):
        return self.__options.get("shared.quota")

    @property
    def windows(self):
        return self.hostPlatform in ("cygwin", "msys", "win32")

    def getGcNum(self, root, key):
        key = "jobs.gc." + ("root" if root else "deps") + "." + key
        val = self.__options.get(key, "1" if key == "jobs.gc.deps.artifacts" else "0")
        num = int(val)
        if num <= 0:
            num = -1
        return str(num)

    def getAuditMeta(self):
        return {
            k[len("audit.meta."):] : v for k, v in sorted(self.__options.items())
            if k.startswith("audit.meta.")
        }


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
    #  8 -> 9: checkout directory state tracks import scm / update script state
    MIN_VERSION = 2
    CUR_VERSION = 9

    VERSION_SINCE_ATTIC_TRACKED = 7

    instance = None
    def __init__(self):
        self.__path = ".bob-state.pickle"
        self.__uncommittedPath = self.__path + ".new"
        self.__byNameDirs = {}
        self.__results = {}
        self.__inputs = {}
        self.__jenkins = {}
        self.__asynchronous = 0
        self.__dirty = False
        self.__dirStates = {}
        self.__buildState = {}
        self.__layerStates = {}
        self.__lock = None
        self.__buildIdCache = None
        self.__variantIds = {}
        self.__atticDirs = {}
        self.__createdWithVersion = self.CUR_VERSION
        self.__storagePath = {}

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

        # Commit old state if valid. It may have been left behind if Bob or the
        # machine crashed really hard.
        self.__commit()

        # load state if it exists
        try:
            if os.path.exists(self.__path):
                try:
                    with open(self.__path, 'rb') as f:
                        state = pickle.load(f)
                except OSError as e:
                    raise ParseError("Error loading workspace state: " + str(e))
                except (pickle.PickleError, ValueError) as e:
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
                self.__layerStates = state.get("layerStates", {})
                self.__buildState = state.get("buildState", {})
                self.__variantIds = state.get("variantIds", {})
                self.__atticDirs = state.get("atticDirs", {})
                self.__createdWithVersion = state.get("createdWithVersion", 0)
                self.__storagePath = state.get("storagePath", {})

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
                "layerStates" : self.__layerStates,
                "buildState" : self.__buildState,
                "variantIds" : self.__variantIds,
                "atticDirs" : self.__atticDirs,
                "createdWithVersion" : self.__createdWithVersion,
                "storagePath" : self.__storagePath,
            }
            self.__dirty = False
            try:
                # Atomically replace the last uncommitted state.
                dirtyPath = self.__uncommittedPath + ".dirty"
                with open(dirtyPath, "wb") as f:
                    with DigestAdder(f) as df:
                        pickle.dump(state, df)
                replacePath(dirtyPath, self.__uncommittedPath)
            except OSError as e:
                raise ParseError("Error saving workspace state: " + str(e))
        else:
            self.__dirty = True

    def __commit(self, verify=True):
        if not os.path.exists(self.__uncommittedPath):
            return

        try:
            commit = True
            with open(self.__uncommittedPath, "r+b") as f:
                if verify:
                    data = f.read()
                    csum = struct.pack("=L", zlib.adler32(data[:-4]))
                    commit = (csum == data[-4:])
                os.fsync(f.fileno())
            if commit:
                replacePath(self.__uncommittedPath, self.__path)
                return
            else:
                print(colorize("Warning: discarding corrupted workspace state!", WARNING),
                    file=sys.stderr)
                print("You might experience build problems because state changes of the last invocation were lost.",
                    file=sys.stderr)
        except OSError as e:
            print(colorize("Warning: cannot commit workspace state: "+str(e), WARNING),
                file=sys.stderr)

        try:
            os.unlink(self.__uncommittedPath)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(colorize("Warning: cannot delete corrupted state: "+str(e), WARNING),
                file=sys.stderr)

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
        self.__commit(False)
        if self.__buildIdCache is not None:
            try:
                self.__buildIdCache.execute("END")
                self.__buildIdCache.close()
                self.__buildIdCache.connection.close()
                self.__buildIdCache = None
            except sqlite3.Error as e:
                print(colorize("Warning: cannot commit buildid cache: "+str(e), WARNING),
                    file=sys.stderr)
        if self.__lock:
            try:
                os.unlink(self.__lock)
            except FileNotFoundError:
                print(colorize("Warning: lock file was deleted while Bob was still running!",
                               WARNING),
                    file=sys.stderr)
            except OSError as e:
                print(colorize("Warning: cannot unlock workspace: "+str(e), WARNING),
                    file=sys.stderr)

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
            res = os.path.join(baseDir, str(num))
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

    def getLayers(self):
        return list(self.__layerStates.keys())

    def hasLayerState(self, path):
        return path in self.__layerStates

    def getLayerState(self, path):
        ret = copy.deepcopy(self.__layerStates.get(path, None))
        return ret

    def setLayerState(self, path, digest):
        self.__layerStates[path] = digest
        self.__save()

    def delLayerState(self, path):
        if path in self.__layerStates:
            del self.__layerStates[path]
            self.__save()

    def getDirectories(self):
        return list(self.__dirStates.keys())

    def hasDirectoryState(self, path):
        return path in self.__dirStates

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

    def setStoragePath(self, workspace, storage):
        """Set storage path for workspace.

        Usually the workspace is also where the files are stored. Only if
        workspace and storage path differ we store the actual storage location.
        """
        if storage == workspace: storage = None
        if self.__storagePath.get(workspace) != storage:
            if storage is None:
                del self.__storagePath[workspace]
            else:
                self.__storagePath[workspace] = storage
            self.__save()

    def getStoragePath(self, workspace):
        """Return diverted storage path for workspace (if any)."""
        return self.__storagePath.get(workspace, workspace)

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
        if path in self.__storagePath:
            del self.__storagePath[path]
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
            "config" : config.dump(),
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
        return JenkinsConfig.load(self.__jenkins[name]["config"])

    def setJenkinsConfig(self, name, config):
        self.__jenkins[name]["config"] = config.dump()
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

