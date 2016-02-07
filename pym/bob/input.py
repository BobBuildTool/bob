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

from . import BOB_VERSION
from .errors import ParseError, BuildError
from .state import BobState
from .utils import colorize, joinScripts, compareVersion
from base64 import b64encode
from glob import glob
from pipes import quote
from string import Template
import copy
import hashlib
import os, os.path
import pickle
import re
import sys
import xml.etree.ElementTree
import yaml

def _hashString(string):
    h = hashlib.md5()
    h.update(string.encode("utf8"))
    return h.digest()

def overlappingPaths(p1, p2):
    p1 = os.path.normcase(os.path.normpath(p1)).split(os.sep)
    if p1 == ["."]: p1 = []
    p2 = os.path.normcase(os.path.normpath(p2)).split(os.sep)
    if p2 == ["."]: p2 = []
    for i in range(min(len(p1), len(p2))):
        if p1[i] != p2[i]: return False
    return True

class Env(dict):
    def derive(self, overrides = {}):
        ret = Env(self)
        ret.update(overrides)
        return ret

    def prune(self, allowed):
        ret = Env()
        for (key, value) in self.items():
            if key in allowed: ret[key] = value
        return ret

class BaseScm:
    def __init__(self, spec):
        self.__condition = spec.get("if", None)
        self.__resolved = False

    def enabled(self, env):
        if self.__condition is not None:
            try:
                return eval(self.__condition, env.derive({'__builtins__':{}}))
            except Exception as e:
                raise ParseError("Error evaluating condition on checkoutSCM: {}".format(str(e)))
        else:
            return True

    def resolveEnv(self, env):
        assert not self.__resolved
        self.__resolved = True

class GitScm(BaseScm):
    def __init__(self, spec):
        super().__init__(spec)
        self.__url = spec["url"]
        self.__branch = spec.get("branch", "master")
        self.__tag = spec.get("tag")
        self.__commit = spec.get("commit")
        self.__dir = spec.get("dir", ".")

    def resolveEnv(self, env):
        super().resolveEnv(env)
        if self.__url:
            self.__url = Template(self.__url).substitute(env)
        if self.__branch:
            self.__branch = Template(self.__branch).substitute(env)
        if self.__tag:
            self.__tag = Template(self.__tag).substitute(env)
        if self.__commit:
            self.__commit = Template(self.__commit).substitute(env).lower()
            # validate commit
            if re.fullmatch("[0-9a-f]{40}", self.__commit) is None:
                raise ParseError("Invalid commit id: " + str(self.__commit))
        if self.__dir:
            self.__dir = Template(self.__dir).substitute(env)

    def asScript(self):
        if self.__tag or self.__commit:
            return """
export GIT_SSL_NO_VERIFY=true
if [ ! -d {DIR}/.git ] ; then
    git init {DIR}
    cd {DIR}
    git remote add origin {URL}
else
    cd {DIR}
fi
git fetch -t origin
git checkout -f {TAG}
""".format(URL=self.__url, TAG=self.__commit if self.__commit else self.__tag, DIR=self.__dir)
        else:
            return """
export GIT_SSL_NO_VERIFY=true
if [ -d {DIR}/.git ] ; then
    cd {DIR}
    git pull
else
    if ! git clone -b {BRANCH} {URL} {DIR} ; then
        rm -rf {DIR}/.git {DIR}/*
        exit 1
    fi
fi
""".format(URL=self.__url, BRANCH=self.__branch, DIR=self.__dir)

    def asDigestScript(self):
        """Return forward compatible stable string describing this git module.

        The format is "url rev-spec dir" where rev-spec depends on the given reference.
        """
        if self.__commit:
            return self.__commit + " " + self.__dir
        elif self.__tag:
            return self.__url + " refs/tags/" + self.__tag + " " + self.__dir
        else:
            return self.__url + " refs/heads/" + self.__branch + " " + self.__dir

    def asJenkins(self, workPath):
        scm = xml.etree.ElementTree.Element("scm", attrib={
            "class" : "hudson.plugins.git.GitSCM",
            "plugin" : "git@2.2.7",
        })
        xml.etree.ElementTree.SubElement(scm, "configVersion").text = "2"

        url = xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "userRemoteConfigs"),
                "hudson.plugins.git.UserRemoteConfig"),
            "url")
        url.text = self.__url

        branch = xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "branches"),
                "hudson.plugins.git.BranchSpec"),
            "name")
        if self.__commit:
            branch.text = self.__commmit
        elif self.__tag:
            branch.text = "refs/tags/" + self.__tag
        else:
            branch.text = "refs/heads/" + self.__branch

        xml.etree.ElementTree.SubElement(scm, "doGenerateSubmoduleConfigurations").text = "false"
        xml.etree.ElementTree.SubElement(scm, "submoduleCfg", attrib={"class" : "list"})

        extensions = xml.etree.ElementTree.SubElement(scm, "extensions")
        xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(extensions,
                "hudson.plugins.git.extensions.impl.RelativeTargetDirectory"),
            "relativeTargetDir").text = os.path.normpath(os.path.join(workPath, self.__dir))

        return scm

    def merge(self, other):
        return False

    def getDirectories(self):
        return { self.__dir : _hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return self.__tag or self.__commit

    def hasJenkinsPlugin(self):
        return True

class SvnScm(BaseScm):
    def __init__(self, spec):
        super().__init__(spec)
        self.__modules = [{
            "url" : spec["url"],
            "dir" : spec.get("dir"),
            "revision" : spec.get("revision")
        }]

    @staticmethod
    def __moduleAsScript(m):
        return """
if [[ -d "{SUBDIR}/.svn" ]] ; then
    if [[ "{URL}" != */tags/* ]] ; then
        svn up {REVISION_ARG} "{SUBDIR}"
    fi
else
    if ! svn co {REVISION_ARG} "{URL}" "{SUBDIR}" ; then
        rm -rf "{SUBDIR}"
        exit 1
    fi
fi
""".format(
          URL=m["url"],
          SUBDIR=m["dir"] if m["dir"] else ".",
          REVISION_ARG=(("-r " + str( m["revision"] ) ) if m["revision"] else '')
          )

    @staticmethod
    def __moduleAsDigestScript(m):
        return (m["url"] + ( ("@"+str(m["revision"])) if m["revision"] else "" ) + " > "
                + (m["dir"] if m["dir"] else "."))

    def resolveEnv(self, env):
        super().resolveEnv(env)
        self.__modules = [
            { k : (Template(v).substitute(env) if isinstance(v, str) else v) for (k,v) in m.items() }
            for m in self.__modules ]

    def asScript(self):
        return joinScripts([ SvnScm.__moduleAsScript(m) for m in self.__modules ])

    def asDigestScript(self):
        """Return forward compatible stable string describing this/these svn module(s).

        Each module has its own line where the module is represented as "url[@rev] > dir".
        """
        return "\n".join([ SvnScm.__moduleAsDigestScript(m) for m in self.__modules ])

    def asJenkins(self, workPath):
        scm = xml.etree.ElementTree.Element("scm", attrib={
            "class" : "hudson.scm.SubversionSCM",
            "plugin" : "subversion@2.4.5",
        })

        locations = xml.etree.ElementTree.SubElement(scm, "locations")
        for m in self.__modules:
            location = xml.etree.ElementTree.SubElement(locations,
                "hudson.scm.SubversionSCM_-ModuleLocation")

            url = m[ "url" ]
            if m["revision"]:
                url += ( "@" + m["revision"] )

            xml.etree.ElementTree.SubElement(location, "remote").text = url
            xml.etree.ElementTree.SubElement(location, "credentialsId")
            xml.etree.ElementTree.SubElement(location, "local").text = (
                os.path.join(workPath, m["dir"]) if m["dir"] else workPath )
            xml.etree.ElementTree.SubElement(location, "depthOption").text = "infinity"
            xml.etree.ElementTree.SubElement(location, "ignoreExternalsOption").text = "true"

            xml.etree.ElementTree.SubElement(scm, "excludedRegions")
            xml.etree.ElementTree.SubElement(scm, "includedRegions")
            xml.etree.ElementTree.SubElement(scm, "excludedUsers")
            xml.etree.ElementTree.SubElement(scm, "excludedRevprop")
            xml.etree.ElementTree.SubElement(scm, "excludedCommitMessages")
            xml.etree.ElementTree.SubElement(scm, "workspaceUpdater",
                attrib={"class":"hudson.scm.subversion.UpdateUpdater"})
            xml.etree.ElementTree.SubElement(scm, "ignoreDirPropChanges").text = "false"
            xml.etree.ElementTree.SubElement(scm, "filterChangelog").text = "false"

        return scm

    def merge(self, other):
        if not isinstance(other, SvnScm):
            return False

        self.__modules.extend(other.__modules)
        return True

    def getDirectories(self):
        return { m['dir'] : _hashString(SvnScm.__moduleAsDigestScript(m)) for m in self.__modules }

    def isDeterministic(self):
        return all([ str(m['revision']).isnumeric() for m in self.__modules ])

    def hasJenkinsPlugin(self):
        return True

class UrlScm(BaseScm):

    EXTENSIONS = [
        (".tar.gz",    "tar"),
        (".tar.xz",    "tar"),
        (".tar.bz2",   "tar"),
        (".tar.bzip2", "tar"),
        (".tgz",       "tar"),
        (".tar",       "tar"),
        (".gz",        "gzip"),
        (".xz",        "xz"),
        (".7z",        "7z"),
        (".zip",       "zip"),
    ]

    EXTRACTORS = {
        "tar"  : "tar xf",
        "gzip" : "gunzip -kf",
        "xz"   : "unxz -kf",
        "7z"   : "7z x -y",
        "zip"  : "unzip",
    }

    def __init__(self, spec):
        super().__init__(spec)
        self.__url = spec["url"]
        self.__digestSha1 = spec.get("digestSHA1")
        self.__digestSha256 = spec.get("digestSHA256")
        self.__dir = spec.get("dir", ".")
        self.__extract = spec.get("extract", "auto")

    def resolveEnv(self, env):
        super().resolveEnv(env)
        if self.__url:
            self.__url = Template(self.__url).substitute(env)
        if self.__digestSha1:
            self.__digestSha1 = Template(self.__digestSha1).substitute(env).lower()
            # validate digest
            if re.fullmatch("[0-9a-f]{40}", self.__digestSha1) is None:
                raise ParseError("Invalid SHA1 digest: " + str(self.__digestSha1))
        if self.__digestSha256:
            self.__digestSha256 = Template(self.__digestSha256).substitute(env).lower()
            # validate digest
            if re.fullmatch("[0-9a-f]{64}", self.__digestSha256) is None:
                raise ParseError("Invalid SHA256 digest: " + str(self.__digestSha256))
        if self.__dir:
            self.__dir = Template(self.__dir).substitute(env)
        if isinstance(self.__extract, str):
            self.__extract = Template(self.__extract).substitute(env)

    def asScript(self):
        fn = quote(self.__url.split("/")[-1])
        ret = """
mkdir -p {DIR}
cd {DIR}
if [ -e {FILE} ] ; then
    curl -sSgL -o {FILE} -z {FILE} {URL}
else
    (
        F=$(mktemp)
        trap 'rm -f $F' EXIT
        curl -sSgL -o $F {URL}
        mv $F {FILE}
    )
fi
""".format(DIR=quote(self.__dir), URL=quote(self.__url), FILE=quote(fn))

        if self.__digestSha1:
            ret += "echo {DIGEST} {FILE} | sha1sum -c\n".format(DIGEST=self.__digestSha1, FILE=fn)
        if self.__digestSha256:
            ret += "echo {DIGEST} {FILE} | sha256sum -c\n".format(DIGEST=self.__digestSha256, FILE=fn)

        extractor = None
        if self.__extract in ["yes", "auto", True]:
            for (ext, tool) in UrlScm.EXTENSIONS:
                if fn.endswith(ext):
                    extractor = UrlScm.EXTRACTORS[tool]
                    break
            if not extractor and self.__extract != "auto":
                raise ParseError("Don't know how to extract '"+fn+"' automatically.")
        elif self.__extract in UrlScm.EXTRACTORS:
            extractor = UrlScm.EXTRACTORS[tool]
        elif self.__extract not in ["no", False]:
            raise ParseError("Invalid extract mode: " + self.__extract)

        if extractor:
            ret += """
if [ {FILE} -nt .{FILE}.extracted ] ; then
    {TOOL} {FILE}
    touch .{FILE}.extracted
fi
""".format(FILE=quote(fn), TOOL=extractor)

        return ret

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir" if a SHA1 checksum was specified. Otherwise it
        is "url dir".
        """
        return ( self.__digestSha256 if self.__digestSha256
                 else (self.__digestSha1 if self.__digestSha1 else self.__url)
                    ) + " " + self.__dir

    def merge(self, other):
        return False

    def getDirectories(self):
        return { self.__dir : _hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return (self.__digestSha1 is not None) or (self.__digestSha256 is not None)

    def hasJenkinsPlugin(self):
        return False


def Scm(spec):
    scm = spec["scm"]
    if scm == "git":
        return GitScm(spec)
    elif scm == "svn":
        return SvnScm(spec)
    elif scm == "url":
        return UrlScm(spec)
    else:
        raise ParseError("Unknown SCM '{}'".format(scm))

class AbstractTool:
    def __init__(self, spec):
        if isinstance(spec, str):
            self.path = spec
            self.libs = []
        else:
            self.path = spec['path']
            self.libs = spec.get('libs', [])

    def prepare(self, step, env):
        """Create concrete tool for given step."""
        try:
             path = Template(self.path).substitute(env)
             libs = [ Template(l).substitute(env) for l in self.libs ]
        except KeyError as e:
            raise ParseError("Error substituting {} in provideTools: {}".format(key, str(e)))
        except ValueError as e:
            raise ParseError("Error substituting {} in provideTools: {}".format(key, str(e)))
        return ConcreteTool(step, path, libs)

class ConcreteTool:
    def __init__(self, step, path, libs):
        self.step = step
        self.path = path
        self.libs = libs

class Sandbox:
    def __init__(self, step, env, spec):
        self.step = step
        self.paths = spec['paths']
        self.mounts = []
        for mount in spec.get('mount', []):
            m = (mount, mount) if isinstance(mount, str) else mount
            try:
                self.mounts.append(
                    (Template(m[0]).substitute(env),
                     Template(m[1]).substitute(env)))
            except KeyError as e:
                raise ParseError("Error substituting {} in provideSandbox: {}".format(mount, str(e)))
            except ValueError as e:
                raise ParseError("Error substituting {} in provideSandbox: {}".format(mount, str(e)))

    def getStep(self):
        return self.step

    def getPaths(self):
        return self.paths

    def getMounts(self):
        return self.mounts

    def getDigest(self):
        """Return digest of the sandbox.

        Mounts are considered invariants and do not contribute to the digest.
        """
        h = hashlib.md5()
        h.update(self.step.getDigest())
        for p in self.paths: h.update(p.encode('utf8'))
        return h.digest()

    def getBuildId(self):
        """Return build id of the sandbox.

        See getDigest()
        """
        bid = self.step.getBuildId()
        if bid is None: return None

        h = hashlib.md5()
        h.update(bid)
        for p in self.paths: h.update(p.encode('utf8'))
        return h.digest()

class BaseStep(object):
    """Represents the smalles unit of execution of a package.

    A step is what gets actually executed when building packages.

    Steps can be compared and sorted. This is done based on the digest of
    the step. See getDigest() for details how the hash is calculated.
    """
    def __init__(self, package, pathFormatter, sandbox, label, env={},
                 tools={}, args=[]):
        self.__package = package
        self.__pathFormatter = pathFormatter
        self.__sandbox = sandbox
        self.__label = label
        self.__tools = tools
        self.__env = env
        self.__args = args
        self.__providedEnv = {}
        self.__providedTools = {}
        self.__providedDeps = []
        self.__providedSandbox = None
        self.__digest = None
        self.__shared = False

    def __hash__(self):
        return int.from_bytes(self.getDigest()[0:8], sys.byteorder)

    def __lt__(self, other):
        return self.getDigest() < other.getDigest()

    def __le__(self, other):
        return self.getDigest() <= other.getDigest()

    def __eq__(self, other):
        return self.getDigest() == other.getDigest()

    def __ne__(self, other):
        return self.getDigest() != other.getDigest()

    def __gt__(self, other):
        return self.getDigest() > other.getDigest()

    def __ge__(self, other):
        return self.getDigest() >= other.getDigest()

    def isValid(self):
        return self.getScript() is not None

    def isCheckoutStep(self):
        return False

    def isBuildStep(self):
        return False

    def isPackageStep(self):
        return False

    def getPackage(self):
        return self.__package

    def getDigest(self):
        if self.__digest is None:
            h = hashlib.md5()
            if self.__sandbox:
                h.update(self.__sandbox.getDigest())
            script = self.getDigestScript()
            if script:
                h.update(script.encode("utf8"))
            for tool in sorted(self.__tools.values(), key=lambda t: (t.step.getDigest(), t.path, t.libs)):
                h.update(tool.step.getDigest())
                h.update(tool.path.encode("utf8"))
                for l in tool.libs: h.update(l.encode('utf8'))
            h.update(pickle.dumps(sorted(self.__env.items())))
            for arg in self.__args:
                h.update(arg.getDigest())
            self.__digest = h.digest()

        return self.__digest

    def getBuildId(self):
        if not self.isDeterministic():
            return None

        h = hashlib.md5()
        if self.__sandbox:
            bid = self.__sandbox.getBuildId()
            if bid is None: return None
            h.update(bid)
        script = self.getDigestScript()
        if script:
            h.update(script.encode("utf8"))
        for tool in sorted(self.__tools.values(), key=lambda t: (t.step.getDigest(), t.path, t.libs)):
            bid = tool.step.getBuildId()
            if bid is None: return None
            h.update(bid)
            h.update(tool.path.encode("utf8"))
            for l in tool.libs: h.update(l.encode('utf8'))
        h.update(pickle.dumps(sorted(self.__env.items())))
        for arg in self.__args:
            bid = arg.getBuildId()
            if bid is None: return None
            h.update(bid)
        return h.digest()

    def getSandbox(self):
        return self.__sandbox

    def getLabel(self):
        return self.__label

    def getExecPath(self):
        """Return the execution path of the step.

        The execution path is where the step is actually run. It may be distinct
        from the workspace path if the build is performed in a sandbox.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'exec')
        else:
            return "/invalid/exec/path/of/{}".format(self.__package.getName())

    def getWorkspacePath(self):
        """Return the workspace path of the step.

        The workspace path represents the location of the step in the users
        workspace. When building in a sandbox this path is not passed to the
        script but the one from getExecPath() instead.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'workspace')
        else:
            return "/invalid/workspace/path/of/{}".format(self.__package.getName())

    def getPaths(self):
        """Get sorted list of execution paths to used tools.

        The returned list is intended to be passed as PATH environment variable.
        The paths are sorted by name.
        """
        return sorted([ os.path.join(tool.step.getExecPath(), tool.path)
            for tool in self.__tools.values() ])

    def getLibraryPaths(self):
        """Get sorted list of library paths of used tools.

        The returned list is intended to be passed as LD_LIBRARY_PATH environment
        variable. The paths are first sorted by tool name. The order of paths of
        a single tool is kept.
        """
        paths = []
        for (name, tool) in sorted(self.__tools.items()):
            paths.extend([ os.path.join(tool.step.getExecPath(), l) for l in tool.libs ])
        return paths

    def getTools(self):
        """Get dictionary of tools.

        The dict maps the tool name to the respective execution path.
        """
        return { name : os.path.join(tool.step.getExecPath(), tool.path)
            for (name, tool) in self.__tools.items() }

    def getArguments(self):
        return self.__args

    def getAllDepSteps(self):
        return self.__args + sorted([ d.step for d in self.__tools.values() ]) + (
            [self.__sandbox.getStep()] if self.__sandbox else [])

    def getEnv(self):
        return self.__env

    def setProvidedEnv(self, provides):
        self.__providedEnv = provides

    def getProvidedEnv(self):
        return self.__providedEnv

    def setProvidedTools(self, provides):
        self.__providedTools = provides

    def getProvidedTools(self):
        return self.__providedTools

    def doesProvideTools(self):
        return self.__providedTools != {}

    def setProvidedDeps(self, deps):
        self.__providedDeps = deps

    def getProvidedDeps(self):
        return self.__providedDeps

    def setProvidedSandbox(self, sandbox):
        self.__providedSandbox = sandbox

    def getProvidedSandbox(self):
        return self.__providedSandbox

    def setShared(self, shared):
        self.__shared = shared

    def isShared(self):
        return self.__shared

class CheckoutStep(BaseStep):
    def __init__(self, package, pathFormatter, sandbox=None, checkout=None,
                 fullEnv={}, env={}, tools={}, deterministic=False):
        if checkout:
            self.__script = checkout[0] if checkout[0] is not None else ""
            self.__scmList = [ copy.deepcopy(scm) for scm in checkout[1] if scm.enabled(env) ]
            try:
                for s in self.__scmList: s.resolveEnv(fullEnv)
            except KeyError as e:
                raise ParseError("Error substituting variable in checkoutSCM: {}".format(str(e)))
            except ValueError as e:
                raise ParseError("Error substituting variable in checkoutSCM: {}".format(str(e)))
            self.__deterministic = deterministic

            # Validate that SCM paths do not overlap
            knownPaths = []
            for s in self.__scmList:
                for p in s.getDirectories().keys():
                    if os.path.isabs(p):
                        raise ParseError("SCM paths must be relative! Offending path: " + p)
                    for known in knownPaths:
                        if overlappingPaths(known, p):
                            raise ParseError("SCM paths '{}' and '{}' overlap."
                                                .format(known, p))
                    knownPaths.append(p)
        else:
            self.__script = None
            self.__scmList = []
            self.__deterministic = True

        super().__init__(package, pathFormatter, sandbox, "src", env, tools)

    def isCheckoutStep(self):
        return True

    def getScript(self):
        """Return a single big script of the whole checkout step"""
        if self.__script is not None:
            return joinScripts([s.asScript() for s in self.__scmList] + [self.__script])
        else:
            return None

    def getJenkinsScript(self):
        """Return the relevant parts as shell script that have no plugin"""
        return joinScripts([ s.asScript() for s in self.__scmList if not s.hasJenkinsPlugin() ]
            + [self.__script])

    def getDigestScript(self):
        """Return a long term stable script.

        The digest script will not be executed but is the basis to calculate if
        the step has changed. In case of the checkout step the involved SCMs will
        return a stable representation of _what_ is checked out and not the real
        script of _how_ this is done.
        """
        if self.__script is not None:
            return "\n".join([s.asDigestScript() for s in self.__scmList] + [self.__script])
        else:
            return None

    def getJenkinsXml(self):
        return [ s.asJenkins(self.getExecPath()) for s in self.__scmList if s.hasJenkinsPlugin() ]

    def getScmDirectories(self):
        dirs = {}
        for s in self.__scmList:
            dirs.update(s.getDirectories())
        return dirs

    def isDeterministic(self):
        """Return whether the checkout step is deterministic.

        Having a script is considered indeterministic unless the recipe declares
        it otherwise (checkoutDeterministic). Then the SCMs are checked if they
        all consider themselves deterministic.
        """
        return self.__deterministic and all([ s.isDeterministic() for s in self.__scmList ])

class RegularStep(BaseStep):
    def __init__(self, package, pathFormatter, sandbox, label, script=None,
                 env={}, tools={}, args=[]):
        self.__script = script
        super().__init__(package, pathFormatter, sandbox, label, env, tools, args)

    def getScript(self):
        return self.__script

    def getJenkinsScript(self):
        return self.__script

    def getDigestScript(self):
        """Nothing fancy here, just return the script."""
        return self.__script

    def isDeterministic(self):
        """Regular steps are assumed to be deterministic."""
        return True

class BuildStep(RegularStep):
    def __init__(self, package, pathFormatter, sandbox=None, script=None, env={},
                 tools={}, args=[]):
        self.__script = script
        super().__init__(package, pathFormatter, sandbox, "build", script, env,
                         tools, args)

    def isBuildStep(self):
        return True

class PackageStep(RegularStep):
    def __init__(self, package, pathFormatter, sandbox=None, script=None, env={},
                 tools={}, args=[]):
        self.__script = script
        super().__init__(package, pathFormatter, sandbox, "dist", script, env,
                         tools, args)

    def isPackageStep(self):
        return True


class Package(object):
    def __init__(self, name, stack, pathFormatter, recipe, sandbox,
                 directDepSteps, indirectDepSteps):
        self.__name = name
        self.__stack = stack
        self.__pathFormatter = pathFormatter
        self.__recipe = recipe
        self.__sandbox = sandbox
        self.__directDepSteps = directDepSteps[:]
        tmp = set(indirectDepSteps)
        if sandbox is not None: tmp.add(sandbox.getStep())
        self.__indirectDepSteps = sorted(tmp)
        self.__checkoutStep = CheckoutStep(self, pathFormatter)
        self.__buildStep = BuildStep(self, pathFormatter)
        self.__packageStep = PackageStep(self, pathFormatter)

    def getName(self):
        return self.__name

    def getPath(self):
         return self.getName().replace( '::', os.sep )

    def getStack(self):
        return self.__stack

    def getRecipe(self):
        return self.__recipe

    def getSandboxState(self):
        return self.__sandbox

    def getDirectDepSteps(self):
        """Return list to the package steps of the direct dependencies.

        Direct dependencies are the ones that are named explicitly in the
        ``depends`` section of the recipe. The order of the items is
        preserved from the recipe.
        """
        return self.__directDepSteps

    def getIndictectDepSteps(self):
        """Return list of indirect dependencies of the package.

        Indirect dependencies are the package steps of tools or the sandbox
        that were forwarded or inheried from other recipes. They are not
        directly named in the recipe.
        """
        return self.__indirectDepSteps

    def getAllDepSteps(self):
        return sorted(set(self.__directDepSteps) | set(self.__indirectDepSteps))

    def setCheckoutStep(self, script, fullEnv, env, tools, deterministic):
        self.__checkoutStep = CheckoutStep(
            self, self.__pathFormatter, self.__sandbox, script, fullEnv, env,
            tools, deterministic)
        return self.__checkoutStep

    def getCheckoutStep(self):
        return self.__checkoutStep

    def setBuildStep(self, script, env, tools, args):
        self.__buildStep = BuildStep(
            self, self.__pathFormatter, self.__sandbox, script, env, tools, args)
        return self.__buildStep

    def getBuildStep(self):
        return self.__buildStep

    def setPackageStep(self, script, env, tools, args):
        self.__packageStep = PackageStep(
            self, self.__pathFormatter, self.__sandbox, script, env, tools, args)
        return self.__packageStep

    def getPackageStep(self):
        return self.__packageStep


# FIXME: implement this on our own without the Template class. How to do proper
# escaping?
class IncludeHelper:

    class Resolver:
        def __init__(self, baseDir, varBase):
            self.baseDir = baseDir
            self.varBase = varBase
            self.prolog = []
            self.count = 0

        def __getitem__(self, item):
            mode = item[0]
            item = item[1:]
            content = []
            try:
                for path in sorted(glob(os.path.join(self.baseDir, item))):
                    with open(path, "rb") as f:
                        content.append(f.read(-1))
            except OSError as e:
                raise ParseError("Error including '"+item+"': " + str(e))
            content = b''.join(content)

            if mode == '<':
                var = "_{}{}".format(self.varBase, self.count)
                self.count += 1
                self.prolog.extend([
                    "{VAR}=$(mktemp)".format(VAR=var),
                    "_BOB_TMP_CLEANUP=( ${VAR} )".format(VAR=var),
                    "base64 -d > ${VAR} <<EOF".format(VAR=var),
                    b64encode(content).decode("ascii"),
                    "EOF"])
                ret = "${" + var + "}"
            else:
                assert mode == "'"
                ret = quote(content.decode('utf8'))

            return ret

    def __init__(self, baseDir, varBase):
        self.__pattern = re.compile(r"""
            \$<(?:
                (?P<escaped>\$)     |
                (?P<named>[<'][^'>]+)['>]>  |
                (?P<braced>[<'][^'>]+)['>]> |
                (?P<invalid>)
            )
            """, re.VERBOSE)
        self.__baseDir = baseDir
        self.__varBase = re.sub(r'[^a-zA-Z0-9-_]', '_', varBase, flags=re.DOTALL)

    def resolve(self, text):
        if isinstance(text, str):
            resolver = IncludeHelper.Resolver(self.__baseDir, self.__varBase)
            t = Template(text)
            t.delimiter = '$<'
            t.pattern = self.__pattern
            ret = t.substitute(resolver)
            return "\n".join(resolver.prolog + [ret])
        else:
            return text

class Recipe(object):
    class Dependency(object):
        def __init__(self, dep):
            if isinstance(dep, str):
                self.recipe = dep
                self.envOverride = {}
                self.provideGlobal = False
                self.useEnv = False
                self.useTools = False
                self.useBuildResult = True
                self.useDeps = True
                self.useSandbox = False
                self.condition = None
            else:
                self.recipe = dep["name"]
                self.envOverride = dep.get("environment", {}).copy()
                self.provideGlobal = dep.get("forward", False)
                useClause = dep.get("use", ["result", "deps"])
                self.useEnv = "environment" in useClause
                self.useTools = "tools" in useClause
                self.useBuildResult = "result" in useClause
                self.useDeps = "deps" in useClause
                self.useSandbox = "sandbox" in useClause
                self.condition = dep.get("if", None)

        def isCompatible(self, other):
            if self.recipe != other.recipe: return True
            return ((self.envOverride == other.envOverride) and (self.provideGlobal == other.provideGlobal)
                    and (self.useEnv == other.useEnv) and (self.useTools == other.useTools)
                    and (self.useBuildResult == other.useBuildResult) and (self.useDeps == other.useDeps)
                    and (self.useSandbox == other.useSandbox) and (self.condition == other.condition))

    class DependencyList(list):
        def __verify(self, item):
            for d in self:
                if not item.isCompatible(d):
                    raise ParseError("Injected dependency '{}' conflicts with existing one.".format(item.recipe))
                if d.recipe == item.recipe:
                    return False
            return True

        def append(self, item):
            if self.__verify(item):
                list.append(self, item)

        def insert(self, i, item):
            if self.__verify(item):
                list.insert(self, i, item)

    @staticmethod
    def loadFromFile(recipeSet, fileName, isClass):
        try:
            with open(fileName, "r") as f:
                recipe = yaml.load(f.read())
        except Exception as e:
            raise ParseError("Error while parsing {}: {}".format(fileName, str(e)))

        # MultiPackages are handled as separate recipes with an anonymous base
        # class. Ignore first dir in path, which is 'recipes' by default.
        # Following dirs are treated as categories separated by '::'.
        baseName = "::".join( os.path.splitext( fileName )[0].split( os.sep )[1:] )
        baseDir = os.path.dirname(fileName)
        if "multiPackage" in recipe:
            if isClass:
                raise ParseError("Classes may not use 'multiPackage'")

            anonBaseClass = Recipe(recipeSet, recipe, baseDir, baseName, baseName)
            return [
                Recipe(recipeSet, subSpec, baseDir, baseName+"-"+subName, baseName, anonBaseClass)
                for (subName, subSpec) in recipe["multiPackage"].items() ]
        else:
            return [ Recipe(recipeSet, recipe, baseDir, baseName, baseName) ]

    def __init__(self, recipeSet, recipe, baseDir, packageName, baseName, anonBaseClass=None):
        self.__recipeSet = recipeSet
        self.__deps = [ Recipe.Dependency(d) for d in recipe.get("depends", []) ]
        self.__packageName = packageName
        self.__baseName = baseName
        self.__root = recipe.get("root", False)
        self.__provideTools = { name : AbstractTool(spec)
            for (name, spec) in recipe.get("provideTools", {}).items() }
        self.__provideVars = recipe.get("provideVars", {})
        self.__provideDeps = set(recipe.get("provideDeps", []))
        self.__provideSandbox = recipe.get("provideSandbox")
        self.__varSelf = recipe.get("environment", {})
        self.__varDepCheckout = set(recipe.get("checkoutVars", []))
        if "checkoutConsume" in recipe:
            print(colorize("WARNING: {}: usage of checkoutConsume is deprecated. Use checkoutVars instead.".format(baseName), "33"))
            self.__varDepCheckout |= set(recipe["checkoutConsume"])
        self.__varDepBuild = set(recipe.get("buildVars", []))
        if "buildConsume" in recipe:
            print(colorize("WARNING: {}: usage of buildConsume is deprecated. Use buildVars instead.".format(baseName), "33"))
            self.__varDepBuild |= set(recipe["buildConsume"])
        self.__varDepBuild |= self.__varDepCheckout
        self.__varDepPackage = set(recipe.get("packageVars", []))
        if "packageConsume" in recipe:
            print(colorize("WARNING: {}: usage of packageConsume is deprecated. Use packageVars instead.".format(baseName), "33"))
            self.__varDepPackage |= set(recipe["packageConsume"])
        self.__varDepPackage |= self.__varDepBuild
        self.__toolDepCheckout = set(recipe.get("checkoutTools", []))
        self.__toolDepBuild = set(recipe.get("buildTools", []))
        self.__toolDepBuild |= self.__toolDepCheckout
        self.__toolDepPackage = set(recipe.get("packageTools", []))
        self.__toolDepPackage |= self.__toolDepBuild
        self.__shared = recipe.get("shared", False)

        incHelper = IncludeHelper(baseDir, packageName)

        checkoutScript = incHelper.resolve(recipe.get("checkoutScript"))
        scms = recipe.get("checkoutSCM", [])
        if isinstance(scms, dict):
            scms = [scms]
        elif not isinstance(scms, list):
            raise ParseError("checkoutSCM must be a dict or a list")
        checkoutSCMs = [ Scm(s) for s in scms ]
        self.__checkout = (checkoutScript, checkoutSCMs)
        self.__build = incHelper.resolve(recipe.get("buildScript"))
        self.__package = incHelper.resolve(recipe.get("packageScript"))

        # Consider checkout deterministic by default if no checkout script is
        # involved.
        self.__checkoutDeterministic = recipe.get("checkoutDeterministic", checkoutScript is None)

        self.__inherited = set()
        inherit = [ self.__recipeSet.getClass(c) for c in recipe.get("inherit", []) ]
        if anonBaseClass: inherit.append(anonBaseClass)
        inherit.reverse()
        for cls in inherit:
            if cls.getName() in self.__inherited:
                continue
            self.__deps[0:0] = cls.__deps
            tmp = cls.__provideTools.copy()
            tmp.update(self.__provideTools)
            self.__provideTools = tmp
            tmp = cls.__provideVars.copy()
            tmp.update(self.__provideVars)
            self.__provideVars = tmp
            self.__provideDeps |= cls.__provideDeps
            tmp = cls.__varSelf.copy()
            tmp.update(self.__varSelf)
            self.__varSelf = tmp
            self.__varDepCheckout |= cls.__varDepCheckout
            self.__varDepBuild |= cls.__varDepBuild
            self.__varDepPackage |= cls.__varDepPackage
            self.__toolDepCheckout |= cls.__toolDepCheckout
            self.__toolDepBuild |= cls.__toolDepBuild
            self.__toolDepPackage |= cls.__toolDepPackage
            (checkoutScript, checkoutSCMs) = self.__checkout
            self.__checkoutDeterministic = self.__checkoutDeterministic and cls.__checkoutDeterministic
            # merge scripts
            checkoutScript = joinScripts([cls.__checkout[0], checkoutScript])
            # merge SCMs
            scms = cls.__checkout[1][:]
            scms.extend(checkoutSCMs)
            checkoutSCMs = scms
            # store result
            self.__checkout = (checkoutScript, checkoutSCMs)
            self.__build = joinScripts([cls.__build, self.__build])
            self.__package = joinScripts([cls.__package, self.__package])

            self.__inherited.add(cls.getName())
            self.__inherited |= cls.__inherited

        # the package step must always be valid
        if self.__package is None:
            self.__package = ""

        # check provided dependencies
        availDeps = [ d.recipe for d in self.__deps ]
        for d in self.__provideDeps:
            if d not in availDeps:
                raise ParseError("Unknown dependency '{}' in provideDeps".format(d))

        # try to merge compatible SCMs
        (checkoutScript, checkoutSCMs) = self.__checkout
        checkoutSCMs = checkoutSCMs[:]
        mergedCheckoutSCMs = []
        while checkoutSCMs:
            head = checkoutSCMs.pop(0)
            checkoutSCMs = [ s for s in checkoutSCMs if not head.merge(s) ]
            mergedCheckoutSCMs.append(head)
        self.__checkout = (checkoutScript, mergedCheckoutSCMs)

    def getRecipeSet(self):
        return self.__recipeSet

    def getName(self):
        return self.__packageName

    def getBaseName(self):
        return self.__baseName

    def isRoot(self):
        return self.__root

    def prepare(self, pathFormatter, inputEnv, sandboxEnabled, sandbox=None,
                inputTools=Env(), inputStack=[]):
        stack = inputStack + [self.__packageName]

        # make copies because we will modify them
        varSelf = {}
        for (key, value) in self.__varSelf.items():
            try:
                 varSelf[key] = Template(value).substitute(inputEnv)
            except KeyError as e:
                raise ParseError("Error substituting {} in environment: {}".format(key, str(e)))
            except ValueError as e:
                raise ParseError("Error substituting {} in environment: {}".format(key, str(e)))
        env = inputEnv.derive(varSelf)
        tools = inputTools.derive()

        # traverse dependencies
        packages = []
        results = []
        depEnv = env.derive()
        depTools = tools.derive()
        depSandbox = sandbox
        allDeps = Recipe.DependencyList(self.__deps)
        i = 0
        while i < len(allDeps):
            dep = allDeps[i]
            i += 1
            if dep.condition is not None:
                try:
                    if not eval(dep.condition, env.derive({'__builtins__':{}})): continue
                except Exception as e:
                    raise ParseError("Error evaluating condition on dependency {}: {}".format(dep.recipe, str(e)))

            r = self.__recipeSet.getRecipe(dep.recipe)
            try:
                p = r.prepare(pathFormatter, depEnv.derive(dep.envOverride),
                              sandboxEnabled, depSandbox, depTools,
                              stack).getPackageStep()
            except ParseError as e:
                e.pushFrame(r.getName())
                raise e

            if dep.useDeps:
                # inject provided depndencies right after current one
                providedDeps = copy.deepcopy(p.getProvidedDeps())
                providedDeps.reverse()
                for d in providedDeps: allDeps.insert(i, d)

            if dep.useBuildResult:
                results.append(p)
            if dep.useTools:
                tools.update(p.getProvidedTools())
                if dep.provideGlobal: depTools.update(p.getProvidedTools())
            if dep.useEnv:
                env.update(p.getProvidedEnv())
                if dep.provideGlobal: depEnv.update(p.getProvidedEnv())
            if dep.useSandbox and sandboxEnabled:
                sandbox = p.getProvidedSandbox()
                if dep.provideGlobal: depSandbox = p.getProvidedSandbox()
            if dep.useBuildResult or dep.useTools or (dep.useSandbox and sandboxEnabled):
                packages.append(p)

        # create package
        p = Package(self.__packageName, stack, pathFormatter, self, sandbox, packages,
                    [ t.step for t  in tools.prune(self.__toolDepPackage).values()  ])

        # optional checkout step
        if self.__checkout != (None, []):
            srcStep = p.setCheckoutStep(self.__checkout, env, env.prune(self.__varDepCheckout),
                tools.prune(self.__toolDepCheckout), self.__checkoutDeterministic)
        else:
            srcStep = p.getCheckoutStep() # return invalid step

        # optional build step
        if self.__build:
            buildStep = p.setBuildStep(self.__build, env.prune(self.__varDepBuild),
                tools.prune(self.__toolDepBuild), [srcStep] + results)
        else:
            buildStep = p.getBuildStep() # return invalid step

        # mandatory package step
        p.setPackageStep(self.__package, env.prune(self.__varDepPackage),
            tools.prune(self.__toolDepPackage), [buildStep])
        packageStep = p.getPackageStep()

        # provide environment
        provideEnv = {}
        for (key, value) in self.__provideVars.items():
            try:
                 provideEnv[key] = Template(value).substitute(env)
            except KeyError as e:
                raise ParseError("Error substituting {} in provideVars: {}".format(key, str(e)))
            except ValueError as e:
                raise ParseError("Error substituting {} in provideVars: {}".format(key, str(e)))
        packageStep.setProvidedEnv(provideEnv)

        # provide tools
        provideTools = { name : tool.prepare(packageStep, env)
            for (name, tool) in self.__provideTools.items() }
        packageStep.setProvidedTools(provideTools)

        # provide deps (direct and indirect deps)
        provideDeps = Recipe.DependencyList()
        for dep in self.__deps:
            if dep.recipe not in self.__provideDeps: continue
            provideDeps.append(dep)
            [subDep] = [ p for p in packages if p.getPackage().getName() == dep.recipe ]
            for d in subDep.getProvidedDeps(): provideDeps.append(d)
        packageStep.setProvidedDeps(provideDeps)

        # provide Sandbox
        if self.__provideSandbox:
            packageStep.setProvidedSandbox(Sandbox(packageStep, env,
                                                   self.__provideSandbox))

        if self.__shared:
            if packageStep.getBuildId() is None:
                raise ParseError("Shared packages must be deterministic!")
            packageStep.setShared(True)

        return p


class RecipeSet:
    def __init__(self):
        self.__defaultEnv = {}
        self.__rootRecipes = []
        self.__recipes = {}
        self.__classes = {}
        self.__whiteList = set(["TERM", "SHELL", "USER", "HOME"])
        self.__archive = { "backend" : "none" }

    def __addRecipe(self, recipe):
        name = recipe.getName()
        if name in self.__recipes:
            raise ParseError("Package "+name+" already defined")
        self.__recipes[name] = recipe
        if recipe.isRoot():
            self.__rootRecipes.append(recipe)

    def envWhiteList(self):
        return set(self.__whiteList)

    def archiveSpec(self):
        return self.__archive

    def parse(self):
        if os.path.exists("config.yaml"):
            try:
                with open("config.yaml", "r") as f:
                    config = yaml.load(f.read())
            except Exception as e:
                raise ParseError("Error while parsing config.yaml: {}".format(str(e)))
        else:
            config = {}

        minVer = config.get("bobMinimumVersion", "0.1")
        if compareVersion(BOB_VERSION, minVer) < 0:
            raise ParseError("Your Bob is too old. At least version "+minVer+" is required!")

        if os.path.exists("default.yaml"):
            try:
                with open("default.yaml", "r") as f:
                    defaults = yaml.load(f.read())
            except Exception as e:
                raise ParseError("Error while parsing default.yaml: {}".format(str(e)))
            if "environment" in defaults:
                self.__defaultEnv = defaults["environment"]
                if not isinstance(self.__defaultEnv, dict):
                    raise ParseError("default.yaml environment must be a dict")
            self.__whiteList |= set(defaults.get("whitelist", []))
            self.__archive = defaults.get("archive", { "backend" : "none" })

        if not os.path.isdir("recipes"):
            raise ParseError("No recipes directory found.")

        for path in ( glob( 'recipes/*.yaml' ) + glob( 'recipes/**/*.yaml' ) ):
            try:
                for r in Recipe.loadFromFile(self, path, False):
                    self.__addRecipe(r)
            except ParseError as e:
                e.pushFrame(path)
                raise

    def getRecipe(self, packageName):
        if packageName not in self.__recipes:
            raise ParseError("Package {} requested but not found.".format(packageName))
        return self.__recipes[packageName]

    def getClass(self, name):
        if name in self.__classes:
            return self.__classes[name]
        else:
            fileName = os.path.join("classes", name+".yaml")
            try:
                [r] = Recipe.loadFromFile(self, fileName, True)
            except ParseError as e:
                e.pushFrame(fileName)
                raise
            self.__classes[name] = r
            return r

    def generatePackages(self, nameFormatter, envOverrides={}, sandboxEnabled=False):
        result = {}
        env = Env(os.environ).prune(self.__whiteList)
        env.update(self.__defaultEnv)
        env.update(envOverrides)
        try:
            BobState().setAsynchronous()
            for root in self.__rootRecipes:
                try:
                    result[root.getName()] = root.prepare(nameFormatter, env,
                                                          sandboxEnabled)
                except ParseError as e:
                    e.pushFrame(root.getName())
                    raise e
        finally:
            BobState().setSynchronous()
        return result


def walkPackagePath(rootPackages, path):
    thisPackage = None
    nextPackages = rootPackages.copy()
    steps = [ s for s in path.split("/") if s != "" ]
    trail = []
    for step in steps:
        if step not in nextPackages:
            raise BuildError("Package '{}' not found under '{}'".format(step, "/".join(trail)))
        thisPackage = nextPackages[step]
        trail.append(step)
        nextPackages = { s.getPackage().getName() : s.getPackage()
            for s in thisPackage.getDirectDepSteps() }

    if not thisPackage:
        raise BuildError("Must specify a valid package to build")

    return thisPackage

