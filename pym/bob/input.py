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
from .tty import colorize, WarnOnce
from .utils import asHexStr, joinScripts, sliceString, compareVersion, binLstat
from abc import ABCMeta, abstractmethod
from base64 import b64encode
from itertools import chain
from glob import glob
from pipes import quote
from string import Template
import copy
import hashlib
import fnmatch
import os, os.path
import pickle
import re
import schema
import shelve
import subprocess
import struct
import sys
import xml.etree.ElementTree
import yaml

# PLEASE TAKE YOUR TIME READING THE FOLLOWING PARAGRAPH CAREFULLY...
#
# Bob will cache almost all internally generated objects if possible. The
# parsed and validated yaml files are always cached. The generated Packages are
# reused if the recipes (including plugins) and the environment did not change
# since the last run. This implies that the classes must stay compatible
# because the 'pickle' module does not persist the actual code!
#
# Therefore the follwing defintion must be incremented virtually with any
# change that is done in this file. If in doubt, change it. It will invalidate
# the cached results and make sure they are re-generated.
CACHE_VERSION = 2

warnCheckoutConsume = WarnOnce("Usage of checkoutConsume is deprecated. Use checkoutVars instead.")
warnBuildConsume = WarnOnce("Usage of buildConsume is deprecated. Use buildVars instead.")
warnPackageConsume = WarnOnce("Usage of packageConsume is deprecated. Use packageVars instead.")
warnFilter = WarnOnce("The filter keyword is experimental and might change or vanish in the future.")
warnCredentials = WarnOnce("JENKINS_GIT_CREDENTIALS_ID is deprecated. Use '--credentials' instead.")

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

def __maybeGlob(pred):
    if pred.startswith("!"):
        pred = pred[1:]
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: False if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: False if elem == pred else prev
    else:
        if any(i in pred for i in '*?[]'):
            return lambda prev, elem: True if fnmatch.fnmatchcase(elem, pred) else prev
        else:
            return lambda prev, elem: True if elem == pred else prev

def maybeGlob(pattern):
    if isinstance(pattern, list):
        return [ __maybeGlob(p) for p in pattern ]
    else:
        return None

def checkGlobList(name, allowed):
    if allowed is None: return True
    ok = False
    for pred in allowed: ok = pred(ok, name)
    return ok

def _isFalse(val):
    return val in [ "", "0", "false" ]

class StringParser:
    """Utility class for complex string parsing/manipulation"""

    def __init__(self, env, funs, funArgs):
        self.env = env
        self.funs = funs
        self.funArgs = funArgs

    def parse(self, text):
        """Parse the text and make substitutions"""
        if all((c not in text) for c in '\\\"\'$'):
            return text
        else:
            self.text = text
            self.index = 0
            self.end = len(text)
            return self.getString()

    def nextChar(self):
        """Get next character"""
        i = self.index
        if i >= self.end:
            raise ParseError('Unexpected end of string')
        self.index += 1
        return self.text[i:i+1]

    def nextToken(self, extra=None):
        delim=['\"', '\'', '$']
        if extra: delim.extend(extra)

        # EOS?
        i = start = self.index
        if i >= self.end:
            return None

        # directly on delimiter?
        if self.text[i] in delim:
            self.index = i+1
            return self.text[i]

        # scan
        tok = []
        while i < self.end:
            if self.text[i] in delim: break
            if self.text[i] == '\\':
                tok.append(self.text[start:i])
                start = i = i + 1
                if i >= self.end:
                    raise ParseError("Unexpected end after escape")
            i += 1
        tok.append(self.text[start:i])
        self.index = i
        return "".join(tok)

    def getSingleQuoted(self):
        i = self.index
        while i < self.end:
            if self.text[i] == "'":
                i += 1
                break
            i += 1
        if i >= self.end:
            raise ParseError("Missing closing \"'\"")
        ret = self.text[self.index:i-1]
        self.index = i
        return ret

    def getString(self, delim=[None], keep=False):
        s = []
        tok = self.nextToken(delim)
        while tok not in delim:
            if tok == '"':
                s.append(self.getString(['"']))
            elif tok == '\'':
                s.append(self.getSingleQuoted())
            elif tok == '$':
                tok = self.nextChar()
                if tok == '{':
                    s.append(self.getVariable())
                elif tok == '(':
                    s.append(self.getCommand())
                else:
                    raise ParseError("Invalid $-subsitituion")
            elif tok == None:
                if None not in delim:
                    raise ParseError('Unexpected end of string')
                break
            else:
                s.append(tok)
            tok = self.nextToken(delim)
        else:
            if keep: self.index -= 1
        return "".join(s)

    def getVariable(self):
        # get variable name
        varName = self.getString([':', '-', '+', '}'], True)

        # process?
        op = self.nextChar()
        unset = varName not in self.env
        if op == ':':
            # or null...
            if not unset: unset = self.env[varName] == ""
            op = self.nextChar()

        if op == '-':
            default = self.getString(['}'])
            if unset:
                return default
            else:
                return self.env[varName]
        elif op == '+':
            alternate = self.getString(['}'])
            if unset:
                return ""
            else:
                return alternate
        elif op == '}':
            if varName not in self.env:
                raise ParseError("Unset variable: " + varName)
            return self.env[varName]
        else:
            raise ParseError("Unterminated variable: " + str(op))

    def getCommand(self):
        words = []
        delim = [",", ")"]
        while True:
            word = self.getString(delim, True)
            words.append(word)
            end = self.nextChar()
            if end == ")": break

        if len(words) < 1:
            raise ParseError("Expected function name")
        cmd = words[0]
        del words[0]

        if cmd not in self.funs:
            raise ParseError("Unknown function: "+cmd)

        return self.funs[cmd](words, env=self.env, **self.funArgs)

class Env(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.funs = []
        self.funArgs = {}
        self.legacy = False

    def copy(self):
        ret = Env(self)
        ret.funs = self.funs
        ret.funArgs = self.funArgs
        ret.legacy = self.legacy
        return ret

    def setLegacy(self, enable):
        self.legacy = enable

    def setFuns(self, funs):
        self.funs = funs

    def setFunArgs(self, funArgs):
        self.funArgs = funArgs

    def derive(self, overrides = {}):
        ret = self.copy()
        ret.update(overrides)
        return ret

    def prune(self, allowed):
        if allowed is None:
            return self.copy()
        else:
            ret = Env()
            ret.funs = self.funs
            ret.funArgs = self.funArgs
            ret.legacy = self.legacy
            for (key, value) in self.items():
                if checkGlobList(key, allowed): ret[key] = value
            return ret

    def substitute(self, value, prop):
        if self.legacy:
            try:
                return Template(value).substitute(self)
            except KeyError as e:
                raise ParseError("Error substituting {}: {}".format(prop, str(e)))
            except ValueError as e:
                raise ParseError("Error substituting {}: {}".format(prop, str(e)))
        else:
            try:
                return StringParser(self, self.funs, self.funArgs).parse(value)
            except ParseError as e:
                raise ParseError("Error substituting {}: {}".format(prop, str(e.slogan)))

    def evaluate(self, condition, prop):
        if condition is None:
            return True

        if self.legacy:
            try:
                return eval(condition, self.derive({'__builtins__':{}}))
            except Exception as e:
                raise ParseError("Error evaluating condition on {}: {}".format(prop, str(e)))
        else:
            s = self.substitute(condition, "condition on "+prop)
            return not _isFalse(s.lower())


class PluginProperty:
    """Base class for plugin property handlers.

    A plugin should sub-class this class to parse custom properties in a
    recipe. For each recipe an object of that class is created then. The
    default constructor just stores the *present* and *value* parameters as
    attributes in the object.

    :param bool present: True if property is present in recipe
    :param value: Unmodified value of property from recipe or None if not present.
    """

    def __init__(self, present, value):
        self.present = present
        self.value = value

    def inherit(self, cls):
        """Inherit from a class.

        The default implementation will use the value from the class if the
        property was not present. Otherwise the class value will be ignored.
        """
        if not self.present:
            self.present = cls.present
            self.value = cls.value

    def isPresent(self):
        """Return True if the property was present in the recipe."""
        return self.present

    def getValue(self):
        """Get (parsed) value of the property."""
        return self.value

    @staticmethod
    def validate(data):
        """Validate type of property.

        Ususally the plugin will reimplement this static method and return True
        only if *data* has the expected type. The default implementation will
        always return True.

        :param data: Parsed property data from the recipe
        :return: True if data has expected type, otherwise False.
        """
        return True


class PluginState:
    """Base class for plugin state trackers.

    State trackers are used by plugins to compute the value of one or more
    properties as the dependency tree of all recipes is traversed.
    """

    def copy(self):
        """Return a copy of the object.

        The default implementation uses copy.deepcopy() which should usually be
        enough. If the plugin uses a sophisticated state tracker, especially
        when holding references to created packages, it might be usefull to
        provide a specialized implementation.
        """
        return copy.deepcopy(self)

    def onEnter(self, env, tools, properties):
        """Begin creation of a package.

        The state tracker is about to witness the creation of a package. The passed
        environment, tools and (custom) properties are in their initial state that
        was inherited from the parent recipe.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param tools: All upstream declared or inherited tools
        :type tools: Mapping[str, :class:`bob.input.Tool`]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        """
        pass

    def onUse(self, downstream):
        """Use provided state of downstream package.

        This method is called if the user added the name of the state tracker
        to the ``use`` clause in the recipe. A state tracker supporting this
        notion should somehow pick up and merge the state of the downstream
        package.

        The default implementation does nothing.

        :param bob.input.PluginState downstream: State of downstream package
        """
        pass

    def onFinish(self, env, tools, properties, package):
        """Finish creation of a package.

        The package was computed and the result is available as parameter
        *package*. The passed *env*, *tools*, and *properties* have their final
        state after all downstream dependencies have been resolved.

        :param env: Complete environment
        :type env: Mapping[str, str]
        :param tools: All upstream declared or inherited tools
        :type tools: Mapping[str, :class:`bob.input.Tool`]
        :param properties: All custom properties
        :type properties: Mapping[str, :class:`bob.input.PluginProperty`]
        :param bob.input.Package packages: The created package
        """
        pass

class GitScm:

    SCHEMA = schema.Schema({
        'scm' : 'git',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('branch') : str,
        schema.Optional('tag') : str,
        schema.Optional('commit') : str,
        schema.Optional('rev') : str,
    })

    def __init__(self, spec):
        self.__recipe = spec['recipe']
        self.__url = spec["url"]
        self.__branch = None
        self.__tag = None
        self.__commit = None
        if "rev" in spec:
            rev = spec["rev"]
            if rev.startswith("refs/heads/"):
                self.__branch = rev[11:]
            elif rev.startswith("refs/tags/"):
                self.__tag = rev[10:]
            elif len(rev) == 40:
                self.__commit = rev
            else:
                raise ParseError("Invalid rev format: " + rev)
        self.__branch = spec.get("branch", self.__branch)
        self.__tag = spec.get("tag", self.__tag)
        self.__commit = spec.get("commit", self.__commit)
        if self.__commit:
            # validate commit
            if re.match("^[0-9a-f]{40}$", self.__commit) is None:
                raise ParseError("Invalid commit id: " + str(self.__commit))
        elif not self.__branch and not self.__tag:
            # nothing secified at all -> master branch
            self.__branch = "master"
        self.__dir = spec.get("dir", ".")

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'git',
            'url' : self.__url,
            'branch' : self.__branch,
            'tag' : self.__tag,
            'commit' : self.__commit,
            'dir' : self.__dir,
            'rev' : ( self.__commit if self.__commit else
                (("refs/tags/" + self.__tag) if self.__tag else
                    ("refs/heads/" + self.__branch))
            )
        }]

    def asScript(self):
        if self.__tag or self.__commit:
            return """
export GIT_SSL_NO_VERIFY=true
if [ ! -d {DIR}/.git ] ; then
    git init {DIR}
fi
cd {DIR}
# see if we have a remote
if [[ -z $(git remote) ]] ; then
    git remote add origin {URL}
fi
# checkout only if HEAD is invalid
if ! git rev-parse --verify -q HEAD >/dev/null ; then
    git fetch -t origin
    git checkout -q {REF}
fi
""".format(URL=self.__url, REF=self.__commit if self.__commit else "tags/"+self.__tag, DIR=self.__dir)
        else:
            return """
export GIT_SSL_NO_VERIFY=true
if [ -d {DIR}/.git ] ; then
    cd {DIR}
    if [[ $(git rev-parse --abbrev-ref HEAD) == "{BRANCH}" ]] ; then
        git pull --ff-only
    else
        echo "Warning: not updating {DIR} because branch was changed manually..." >&2
    fi
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

    def asJenkins(self, workPath, credentials, options):
        scm = xml.etree.ElementTree.Element("scm", attrib={
            "class" : "hudson.plugins.git.GitSCM",
            "plugin" : "git@2.2.7",
        })
        xml.etree.ElementTree.SubElement(scm, "configVersion").text = "2"

        userconfigs =  xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "userRemoteConfigs"),
                "hudson.plugins.git.UserRemoteConfig")

        url = xml.etree.ElementTree.SubElement(userconfigs,
            "url")
        url.text = self.__url

        if "JENKINS_GIT_CREDENTIALS_ID" in os.environ:
            warnCredentials.warn()
            if not credentials:
                credentials = os.environ["JENKINS_GIT_CREDENTIALS_ID"]
        if credentials:
            credentialsId = xml.etree.ElementTree.SubElement(userconfigs,
                         "credentialsId")
            credentialsId.text = credentials

        branch = xml.etree.ElementTree.SubElement(
            xml.etree.ElementTree.SubElement(
                xml.etree.ElementTree.SubElement(scm, "branches"),
                "hudson.plugins.git.BranchSpec"),
            "name")
        if self.__commit:
            branch.text = self.__commit
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
        # remove untracked files
        xml.etree.ElementTree.SubElement(extensions,
            "hudson.plugins.git.extensions.impl.CleanCheckout")
        shallow = options.get("scm.git.shallow")
        if shallow is not None:
            try:
                shallow = int(shallow)
                if shallow < 0: raise ValueError()
            except ValueError:
                raise BuildError("Invalid 'git.shallow' option: " + str(shallow))
            if shallow > 0:
                co = xml.etree.ElementTree.SubElement(extensions,
                    "hudson.plugins.git.extensions.impl.CloneOption")
                xml.etree.ElementTree.SubElement(co, "shallow").text = "true"
                xml.etree.ElementTree.SubElement(co, "noTags").text = "false"
                xml.etree.ElementTree.SubElement(co, "reference").text = ""
                xml.etree.ElementTree.SubElement(co, "depth").text = str(shallow)
                xml.etree.ElementTree.SubElement(co, "honorRefspec").text = "false"

        return scm

    def merge(self, other):
        return False

    def getDirectories(self):
        return { self.__dir : _hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return bool(self.__tag) or bool(self.__commit)

    def hasJenkinsPlugin(self):
        return True

    def callGit(self, workspacePath, *args):
        cmdLine = ['git']
        cmdLine.extend(args)
        try:
            output = subprocess.check_output(cmdLine, cwd=os.path.join(os.getcwd(), workspacePath, self.__dir),
                universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise BuildError("git error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                os.path.join(workspacePath, self.__dir), " ".join(cmdLine), e.output.rstrip()))
        return output

    # Get GitSCM status. The purpose of this function is to return the status of the given directory
    # and if verbose is not zero print additional informations about it.
    #
    # return values:
    #  - error: the scm is in a error state. Use this if git returned a error code.
    #  - unclean: SCM is unclean. Could be: modified files, switched to another branch/tag/commit/repo, unpushed commits
    #  - clean: same branch/tag/commit as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkou. 'error' and 'unclean' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath, dir, verbose=0):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty'

        status = ""
        longStatus = ""
        try:
            output = self.callGit(workspacePath, 'ls-remote' ,'--get-url').rstrip()
            if output != self.__url:
                status += "S"
                longStatus += colorize("   > URL: configured: '{}'  actual: '{}'\n".format(self.__url, output), "33")
            else:
                if self.__commit:
                    output = self.callGit(workspacePath, 'rev-parse', 'HEAD').rstrip()
                    if output != self.__commit:
                        status += "S"
                        longStatus +=  colorize("   > commitId: configured: {}  actual: {}\n".format(self.__commit, output), "33")
                elif self.__tag:
                    output = self.callGit(workspacePath, 'describe', '--tags', '--always').rstrip()
                    if output != self.__tag:
                        status += "S"
                        longStatus += colorize("    > tag: configured: {} actual: {}\n".format(self.__tag, output), "33")
                elif self.__branch:
                    output = self.callGit(workspacePath, 'rev-parse', '--abbrev-ref', 'HEAD').rstrip()
                    if output != self.__branch:
                        status += "S"
                        longStatus += colorize("    > branch: configured: {} actual: {}\n".format(self.__branch, output), "33")
                    else:
                        output = self.callGit(workspacePath, 'rev-list', 'origin/'+self.__branch+'..HEAD')
                        if len(output):
                            status += "U"
                            # do not print detailed status this point.
                            # git log --branches --not --remotes --decorate will give the same informations.

            output = self.callGit(workspacePath, 'status', '--porcelain')
            if len(output):
                longStatus += colorize("    > modified:\n", "33")
                status += "M"
                if verbose >=2:
                    for line in output.split('\n'):
                        if line != "":
                           longStatus += '      '+line + '\n'

            # the following shows unpushed commits even on local branches. do not mark the SCM as unclean.
            output = self.callGit(workspacePath, 'log', '--branches', '--not', '--remotes', '--decorate')
            if len(output):
                status += "u"
                longStatus += colorize("     > unpushed:\n", "33")
                if verbose >= 2:
                    for line in output.split('\n'):
                       if line != "":
                           longStatus += '      ' + line + '\n'
            ret = 'clean'
            if status == "":
                if verbose >= 3:
                    print(colorize("   STATUS      {0}".format(scmdir), "32"))
            elif status != "u":
                ret = 'unclean'

            if (status != "") and (verbose != 0):
                print(colorize("   STATUS {0: <4} {1}".format(status, scmdir), "33"))
                if (verbose >= 2) and (longStatus != ""):
                    print(longStatus)
        except BuildError as e:
            print(e)
            ret = 'error'

        return ret

class SvnScm:

    SCHEMA = schema.Schema({
        'scm' : 'svn',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('revision') : schema.Or(int, str)
    })

    def __init__(self, spec):
        self.__modules = [{
            "recipe" : spec['recipe'],
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

    def getProperties(self):
        ret = [ m.copy() for m in self.__modules ]
        for m in ret: m['scm'] = "svn"
        return ret

    def asScript(self):
        return joinScripts([ SvnScm.__moduleAsScript(m) for m in self.__modules ])

    def asDigestScript(self):
        """Return forward compatible stable string describing this/these svn module(s).

        Each module has its own line where the module is represented as "url[@rev] > dir".
        """
        return "\n".join([ SvnScm.__moduleAsDigestScript(m) for m in self.__modules ])

    def asJenkins(self, workPath, credentials, options):
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
            credentialsId = xml.etree.ElementTree.SubElement(location, "credentialsId")
            if credentials: credentialsId.text = credentials
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

    def callSubversion(self, workspacePath, *args):
        cmdLine = ['svn']
        cmdLine.extend(args)

        try:
            output = subprocess.check_output(cmdLine, cwd=workspacePath,
                universal_newlines=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise BuildError("svn error:\n Directory: '{}'\n Command: '{}'\n'{}'".format(
                os.path.join(workspacePath, self.__dir), " ".join(cmdLine), e.output.rstrip()))
        return output

    # Get SvnSCM status. The purpose of this function is to return the status of the given directory
    # and if verbose is not zero print additional informations about it.
    #
    # return values:
    #  - error: the scm is in a error state. Use this if svn call returns a error code.
    #  - unclean: SCM is unclean. Could be: modified files, switched to another URL or revision
    #  - clean: same URL and revision as specified in the recipe and no local changes.
    #  - empty: directory is not existing
    #
    # This function is called when build with --clean-checkout. 'error' and 'unclean' scm's are moved to attic,
    # while empty and clean directories are not.
    def status(self, workspacePath, dir, verbose=0):
        scmdir = os.path.join(workspacePath, dir)
        if not os.path.exists(os.path.join(os.getcwd(), scmdir)):
            return 'empty'

        for m in self.__modules:
            if m['dir'] == dir:
                break;

        status = ""
        longStatus = ""
        try:
            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'status')
            if len(svnoutput):
                status += "M"
                longStatus += colorize("    > modified:\n", "33")
                if verbose >= 2:
                    for line in svnoutput.split('\n'):
                        longStatus += '       '+line.rstrip()

            svnoutput = self.callSubversion(os.path.join(os.getcwd(), workspacePath, dir), 'info', '--xml')
            info = xml.etree.ElementTree.fromstring(svnoutput)
            entry = info.find('entry')
            url = entry.find('url').text
            revision = entry.attrib['revision']

            if m['url'] != url:
                status += "S"
                longStatus += colorize("     > URLs do not match!\n     recipe:\t{}\n     svn info:\t{}".format(m['url'], url), "33")
            if m['revision'] is not None and int(revision) != int(m['revision']):
                status += "S"
                longStatus += colorize("    ! wrong revision: recipe: {} svn info: {}".format(m['revision'], revision), "33")

            if status == "":
                if verbose >= 3:
                    print(colorize("   STATUS   {}".format(scmdir), "32"))
                return 'clean'
            else:
                if verbose != 0:
                    print(colorize("   STATUS {0: <4} {1}".format(status, scmdir), "33"))
                if (verbose >= 2) and (longStatus != ""):
                    print(longStatus)
                return 'unclean'
        except BuildError as e:
            print(e)
            ret = 'error'

        return ret

class CvsScm:

    SCHEMA = schema.Schema({
        'scm' : 'cvs',
        'cvsroot' : str,
        'module' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('rev') : str
    })

    # Checkout using CVS
    # - mandatory parameters: cvsroot, module
    # - optional parameters: rev, dir (dir is required if there are multiple checkouts)
    def __init__(self, spec):
        self.__recipe = spec['recipe']
        self.__cvsroot = spec["cvsroot"]
        self.__module = spec["module"]
        self.__rev = spec.get("rev")
        self.__dir = spec.get("dir", ".")

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'cvs',
            'cvsroot' : self.__cvsroot,
            'module' : self.__module,
            'rev' : self.__rev,
            'dir' : self.__dir
        }]

    def asScript(self):
        # If given a ":ssh:" cvsroot, translate that to CVS_RSH using ssh, and ":ext:"
        # (some versions of CVS do that internally)
        m = re.match('^:ssh:(.*)', self.__cvsroot)
        if m:
            prefix="CVS_RSH=ssh "
            rootarg=":ext:" + m.group(1)
        else:
            prefix=""
            rootarg=self.__cvsroot
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"

        # Workaround: CVS 1.12.13 refuses to checkout with '-d .' when using remote access
        #   cvs checkout: existing repository /home/stefan/cvsroot does not match /home/stefan/cvsroot/cxxtest
        #   cvs checkout: ignoring module cxxtest
        # Thus, we have to trick it with a symlink.
        if re.match('^:ext:', rootarg) and self.__dir == '.':
            return """
# Checkout or update
if [ -d CVS ]; then
   {prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} .
else
   ln -s . __tmp$$
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d __tmp$$ '{module}'
   rm __tmp$$
fi
""".format(prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module)
        else:
            return """
# Checkout or update
if [ -d {dir}/CVS ]; then
   {prefix}cvs -qz3 -d '{rootarg}' up -dP {revarg} {dir}
else
   {prefix}cvs -qz3 -d '{rootarg}' co {revarg} -d {dir} '{module}'
fi
""".format(prefix=prefix, rootarg=rootarg, revarg=revarg, module=self.__module, dir=self.__dir)

    def asDigestScript(self):
        # Describe what we do: just all the parameters concatenated.
        revarg = "-r {rev}".format(rev=self.__rev) if self.__rev != None else "-A"
        return "{cvsroot} {revarg} {module} {dir}".format(cvsroot=self.__cvsroot,
                                                          revarg=revarg,
                                                          module=self.__module,
                                                          dir=self.__dir)

    def merge(self, other):
        return False

    def getDirectories(self):
        return {self.__dir: _hashString(self.asDigestScript())}

    def isDeterministic(self):
        # We cannot know whether this step is deterministic because we
        # don't know whether the given revision (if any) refers to a
        # tag or branch.
        return False

    def hasJenkinsPlugin(self):
        return False

    def status(self, workspacePath, dir, verbose = 0):
        print("CVS SCM status not implemented!")
        return 'error'

class UrlScm:

    SCHEMA = schema.Schema({
        'scm' : 'url',
        'url' : str,
        schema.Optional('dir') : str,
        schema.Optional('if') : str,
        schema.Optional('digestSHA1') : str,
        schema.Optional('digestSHA256') : str,
        schema.Optional('extract') : schema.Or(bool, str),
        schema.Optional('fileName') : str
    })

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
        "zip"  : "unzip -o",
    }

    def __init__(self, spec):
        self.__recipe = spec['recipe']
        self.__url = spec["url"]
        self.__digestSha1 = spec.get("digestSHA1")
        if self.__digestSha1:
            # validate digest
            if re.fullmatch("[0-9a-f]{40}", self.__digestSha1) is None:
                raise ParseError("Invalid SHA1 digest: " + str(self.__digestSha1))
        self.__digestSha256 = spec.get("digestSHA256")
        if self.__digestSha256:
            # validate digest
            if re.fullmatch("[0-9a-f]{64}", self.__digestSha256) is None:
                raise ParseError("Invalid SHA256 digest: " + str(self.__digestSha256))
        self.__dir = spec.get("dir", ".")
        self.__fn = spec.get("fileName")
        if not self.__fn:
            self.__fn = self.__url.split("/")[-1]
        self.__extract = spec.get("extract", "auto")

    def getProperties(self):
        return [{
            'recipe' : self.__recipe,
            'scm' : 'url',
            'url' : self.__url,
            'digestSHA1' : self.__digestSha1,
            'digestSHA256' : self.__digestSha256,
            'dir' : self.__dir,
            'fileName' : self.__fn,
            'extract' : self.__extract
        }]

    def asScript(self):
        ret = """
mkdir -p {DIR}
cd {DIR}
if [ -e {FILE} ] ; then
    curl -sSgL -o {FILE} -z {FILE} {URL}
else
    (
        F=$(mktemp)
        trap 'rm -f $F' EXIT
        set -e
        curl -sSgL -o $F {URL}
        mv $F {FILE}
    )
fi
""".format(DIR=quote(self.__dir), URL=quote(self.__url), FILE=quote(self.__fn))

        if self.__digestSha1:
            ret += "echo {DIGEST} {FILE} | sha1sum -c\n".format(DIGEST=self.__digestSha1, FILE=self.__fn)
        if self.__digestSha256:
            ret += "echo {DIGEST} {FILE} | sha256sum -c\n".format(DIGEST=self.__digestSha256, FILE=self.__fn)

        extractor = None
        if self.__extract in ["yes", "auto", True]:
            for (ext, tool) in UrlScm.EXTENSIONS:
                if self.__fn.endswith(ext):
                    extractor = UrlScm.EXTRACTORS[tool]
                    break
            if not extractor and self.__extract != "auto":
                raise ParseError("Don't know how to extract '"+self.__fn+"' automatically.")
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
""".format(FILE=quote(self.__fn), TOOL=extractor)

        return ret

    def asDigestScript(self):
        """Return forward compatible stable string describing this url.

        The format is "digest dir extract" if a SHA checksum was specified.
        Otherwise it is "url dir extract".
        """
        return ( self.__digestSha256 if self.__digestSha256
                 else (self.__digestSha1 if self.__digestSha1 else self.__url)
                    ) + " " + os.path.join(self.__dir, self.__fn) + " " + str(self.__extract)

    def merge(self, other):
        return False

    def getDirectories(self):
        fn = os.path.join(self.__dir, self.__fn)
        return { fn : _hashString(self.asDigestScript()) }

    def isDeterministic(self):
        return (self.__digestSha1 is not None) or (self.__digestSha256 is not None)

    def hasJenkinsPlugin(self):
        return False

    def status(self, workspacePath, dir, verbose = 0):
        return 'clean'

class ScmOverride:
    def __init__(self, override):
        self.__match = override.get("match", {})
        self.__del = override.get("del", [])
        self.__set = override.get("set", {})
        self.__replaceRaw = override.get("replace", {})
        self.__init()

    def __init(self):
        self.__replace = { key : (re.compile(subst["pattern"]), subst["replacement"])
            for (key, subst) in self.__replaceRaw.items() }

    def __getstate__(self):
        return (self.__match, self.__del, self.__set, self.__replaceRaw)

    def __setstate__(self, s):
        (self.__match, self.__del, self.__set, self.__replaceRaw) = s
        self.__init()

    def __doesMatch(self, scm):
        for (key, value) in self.__match.items():
            if key not in scm: return False
            if not fnmatch.fnmatchcase(scm[key], value): return False
        return True

    def mangle(self, scm):
        if self.__doesMatch(scm):
            scm = scm.copy()
            for d in self.__del:
                if d in scm: del scm[d]
            scm.update(self.__set)
            for (key, (pat, repl)) in self.__replace.items():
                if key in scm:
                    scm[key] = re.sub(pat, repl, scm[key])
        return scm

def Scm(spec, env, overrides):
    # resolve with environment
    spec = { k : ( env.substitute(v, "checkoutSCM::"+k) if isinstance(v, str) else v)
        for (k, v) in spec.items() }

    # apply overrides
    for override in overrides:
        spec = override.mangle(spec)

    # create scm instance
    scm = spec["scm"]
    if scm == "git":
        return GitScm(spec)
    elif scm == "svn":
        return SvnScm(spec)
    elif scm == "cvs":
        return CvsScm(spec)
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
        path = env.substitute(self.path, "provideTools::path")
        libs = [ env.substitute(l, "provideTools::libs") for l in self.libs ]
        return Tool(step, path, libs)

class Tool:
    """Representation of a tool.

    A tool is made of the result of a package, a relative path into this result
    and some optional relative library paths.
    """
    def __init__(self, step, path, libs):
        self.step = step
        self.path = path
        self.libs = libs

    def getStep(self):
        """Return package step that produces the result holding the tool
        binaries/scripts.

        :return: :class:`bob.input.Step`
        """
        return self.step

    def getPath(self):
        """Get relative path into the result."""
        return self.path

    def getLibs(self):
        """Get list of relative library paths into the result.

        :return: List[str]
        """
        return self.libs

class Sandbox:
    """Represents a sandbox that is used when executing a step."""

    def __init__(self, step, env, enabled, spec):
        self.step = step
        self.enabled = enabled
        self.paths = spec['paths']
        self.mounts = []
        for mount in spec.get('mount', []):
            m = (env.substitute(mount[0], "provideSandbox::mount-from"),
                 env.substitute(mount[1], "provideSandbox::mount-to"),
                 mount[2])
            # silently drop empty mount lines
            if (m[0] != "") and (m[1] != ""):
                self.mounts.append(m)

    def getStep(self):
        """Get the package step that yields the content of the sandbox image."""
        return self.step

    def getPaths(self):
        """Return list of global search paths.

        This is the base $PATH in the sandbox."""
        return self.paths

    def getMounts(self):
        """Get custom mounts.

        This returns a list of tuples where each tuple has the format
        (hostPath, sandboxPath, options).
        """
        return self.mounts

    def isEnabled(self):
        """Return True if the sandbox is used in the current build configuration."""
        return self.enabled

class Step(metaclass=ABCMeta):
    """Represents the smallest unit of execution of a package.

    A step is what gets actually executed when building packages.

    Steps can be compared and sorted. This is done based on the Variant-Id of
    the step. See :meth:`bob.input.Step.getVariantId` for details.
    """
    def __init__(self, package, pathFormatter, sandbox, label, digestEnv={},
                 env={}, tools={}, args=[]):
        self.__package = package
        self.__pathFormatter = pathFormatter
        self.__sandbox = sandbox
        self.__label = label
        self.__tools = tools
        self.__digestEnv = digestEnv
        self.__env = env
        self.__args = args
        self.__providedEnv = {}
        self.__providedTools = {}
        self.__providedDeps = []
        self.__providedSandbox = None
        self.__shared = False

    def __hash__(self):
        return int.from_bytes(self._getStableVariantId()[0:8], sys.byteorder)

    def __lt__(self, other):
        return self._getStableVariantId() < other._getStableVariantId()

    def __le__(self, other):
        return self._getStableVariantId() <= other._getStableVariantId()

    def __eq__(self, other):
        return self._getStableVariantId() == other._getStableVariantId()

    def __ne__(self, other):
        return self._getStableVariantId() != other._getStableVariantId()

    def __gt__(self, other):
        return self._getStableVariantId() > other._getStableVariantId()

    def __ge__(self, other):
        return self._getStableVariantId() >= other._getStableVariantId()

    @abstractmethod
    def getScript(self):
        """Return a single big script of the whole step.

        Besides considerations of special backends (such as Jenkins) this
        script is what should be executed to build this step."""
        pass

    @abstractmethod
    def getJenkinsScript(self):
        """Return the relevant parts as shell script that have no Jenkins plugin."""
        pass

    @abstractmethod
    def getDigestScript(self):
        """Return a long term stable script.

        The digest script will not be executed but is the basis to calculate if
        the step has changed. In case of the checkout step the involved SCMs will
        return a stable representation of _what_ is checked out and not the real
        script of _how_ this is done.
        """
        pass

    @abstractmethod
    def isDeterministic(self):
        """Return whether the step is deterministic.

        Checkout steps that have a script are considered indeterministic unless
        the recipe declares it otherwise (checkoutDeterministic). Then the SCMs
        are checked if they all consider themselves deterministic.

        Build and package steps are always deterministic.
        """
        pass

    def isValid(self):
        """Returns True if this step is valid, False otherwise."""
        return self.getScript() is not None

    def isCheckoutStep(self):
        """Return True if this is a checkout step."""
        return False

    def isBuildStep(self):
        """Return True if this is a build step."""
        return False

    def isPackageStep(self):
        """Return True if this is a package step."""
        return False

    def getPackage(self):
        """Get Package object that is the parent of this Step."""
        return self.__package

    def getDigest(self, calculate, forceSandbox=False, hasher=hashlib.md5):
        h = hasher()
        if self.__sandbox and (self.__sandbox.isEnabled() or forceSandbox):
            d = calculate(self.__sandbox.getStep())
            if d is None: return None
            h.update(d)
            h.update(struct.pack("<I", len(self.__sandbox.getPaths())))
            for p in self.__sandbox.getPaths():
                h.update(struct.pack("<I", len(p)))
                h.update(p.encode('utf8'))
        else:
            h.update(b'\x00' * 20)
        script = self.getDigestScript()
        if script:
            h.update(struct.pack("<I", len(script)))
            h.update(script.encode("utf8"))
        else:
            h.update(b'\x00\x00\x00\x00')
        h.update(struct.pack("<I", len(self.__tools)))
        for tool in sorted(self.__tools.values(), key=lambda t: (t.step._getStableVariantId(), t.path, t.libs)):
            d = calculate(tool.step)
            if d is None: return None
            h.update(d)
            h.update(struct.pack("<II", len(tool.path), len(tool.libs)))
            h.update(tool.path.encode("utf8"))
            for l in tool.libs:
                h.update(struct.pack("<I", len(l)))
                h.update(l.encode('utf8'))
        h.update(struct.pack("<I", len(self.__digestEnv)))
        for (key, val) in sorted(self.__digestEnv.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        h.update(struct.pack("<I", len(self.__args)))
        for arg in self.__args:
            d = calculate(arg)
            if d is None: return None
            h.update(d)
        return h.digest()

    def getVariantId(self):
        """Return Variant-Id of this Step.

        The Variant-Id is used to distinguish different packages or multiple
        variants of a package. Each Variant-Id need only be built once but
        successive builds might yield different results (e.g. when building
        from branches)."""
        try:
            ret = self.__variantId
        except AttributeError:
            ret = self.__variantId = self.getDigest(lambda step: step.getVariantId())
        return ret

    def _getStableVariantId(self):
        """Return stable Variant-Id of this Step.

        Like getVariantId() but always considering the sandbox. Used for stable
        sorting of steps regardless of the build settings.
        """
        try:
            ret = self.__stableVariantId
        except AttributeError:
            ret = self.__stableVariantId = self.getDigest(
                lambda step: step._getStableVariantId(), True)
        return ret

    def getBuildId(self):
        """Return static Build-Id of this Step.

        The Build-Id represents the expected result of the Step. This method
        will return None if the Build-Id cannot be determined in advance.
        """
        try:
            ret = self.__buildId
        except AttributeError:
            ret = self.__buildId = self.getDigest(lambda step: step.getBuildId(), True) \
                                    if self.isDeterministic() else None
        return ret

    def getSandbox(self):
        """Return Sandbox used in this Step.

        Returns a Sandbox object or None if this Step is built without one.
        """
        if self.__sandbox and self.__sandbox.isEnabled():
            return self.__sandbox
        else:
            return None

    def getLabel(self):
        """Return path label for step.

        This is currently defined as "src", "build" and "dist" for the
        respective steps.
        """
        return self.__label

    def getExecPath(self):
        """Return the execution path of the step.

        The execution path is where the step is actually run. It may be distinct
        from the workspace path if the build is performed in a sandbox.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'exec', self.__package._getStates())
        else:
            return "/invalid/exec/path/of/{}".format(self.__package.getName())

    def getWorkspacePath(self):
        """Return the workspace path of the step.

        The workspace path represents the location of the step in the users
        workspace. When building in a sandbox this path is not passed to the
        script but the one from getExecPath() instead.
        """
        if self.isValid():
            return self.__pathFormatter(self, 'workspace', self.__package._getStates())
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
        """Get list of all inputs for this Step.

        The arguments are passed as absolute paths to the script starting from $1.
        """
        return self.__args

    def getAllDepSteps(self):
        """Get all dependent steps of this Step.

        This includes the direct input to the Step as well as indirect inputs
        such as the used tools or the sandbox.
        """
        return self.__args + sorted([ d.step for d in self.__tools.values() ]) + (
            [self.__sandbox.getStep()] if (self.__sandbox and self.__sandbox.isEnabled()) else [])

    def getEnv(self):
        """Return dict of environment variables."""
        return self.__env

    def _setProvidedEnv(self, provides):
        self.__providedEnv = provides

    def getProvidedEnv(self):
        """Return provided environemt variables for upstream packages."""
        return self.__providedEnv

    def _setProvidedTools(self, provides):
        self.__providedTools = provides

    def getProvidedTools(self):
        """Return provided tools for upstream recipes."""
        return self.__providedTools

    def doesProvideTools(self):
        """Return True if this step provides at least one tool."""
        return self.__providedTools != {}

    def _setProvidedDeps(self, deps):
        self.__providedDeps = deps

    def getProvidedDeps(self):
        """Get provided dependencies for upstream recipes."""
        return self.__providedDeps

    def _setProvidedSandbox(self, sandbox):
        self.__providedSandbox = sandbox

    def getProvidedSandbox(self):
        """Get provided sandbox for upstream recipes."""
        return self.__providedSandbox

    def _setShared(self, shared):
        self.__shared = shared

    def isShared(self):
        """Returns True if the result of the Step should be shared globally.

        The exact behaviour of a shared step/package depends on the build
        backend. In general a shared package means that the result is put into
        some shared location where it is likely that the same result is needed
        again.
        """
        return self.__shared

class CheckoutStep(Step):
    def __init__(self, package, pathFormatter, sandbox=None, checkout=None,
                 fullEnv={}, digestEnv={}, env={}, tools={},
                 deterministic=False):
        if checkout:
            self.__script = checkout[0] if checkout[0] is not None else ""
            self.__digestScript = checkout[1] if checkout[1] is not None else ""
            self.__deterministic = deterministic

            # try to merge compatible SCMs
            overrides = package.getRecipe().getRecipeSet().scmOverrides()
            checkoutSCMs = [ Scm(scm, fullEnv, overrides) for scm in checkout[2]
                if fullEnv.evaluate(scm.get("if"), "checkoutSCM") ]
            mergedCheckoutSCMs = []
            while checkoutSCMs:
                head = checkoutSCMs.pop(0)
                checkoutSCMs = [ s for s in checkoutSCMs if not head.merge(s) ]
                mergedCheckoutSCMs.append(head)
            self.__scmList = mergedCheckoutSCMs

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
            self.__digestScript = None
            self.__scmList = []
            self.__deterministic = True

        super().__init__(package, pathFormatter, sandbox, "src", digestEnv,
                         env, tools)

    def isCheckoutStep(self):
        return True

    def getScript(self):
        if self.__script is not None:
            return joinScripts([s.asScript() for s in self.__scmList] + [self.__script])
        else:
            return None

    def getJenkinsScript(self):
        return joinScripts([ s.asScript() for s in self.__scmList if not s.hasJenkinsPlugin() ]
            + [self.__script])

    def getDigestScript(self):
        if self.__script is not None:
            return "\n".join([s.asDigestScript() for s in self.__scmList] + [self.__digestScript])
        else:
            return None

    def getJenkinsXml(self, credentials, options):
        return [ s.asJenkins(self.getWorkspacePath(), credentials, options)
                 for s in self.__scmList if s.hasJenkinsPlugin() ]

    def getScmList(self):
        return self.__scmList

    def getScmDirectories(self):
        dirs = {}
        for s in self.__scmList:
            dirs.update(s.getDirectories())
        return dirs

    def isDeterministic(self):
        return self.__deterministic and all([ s.isDeterministic() for s in self.__scmList ])

class RegularStep(Step):
    def __init__(self, package, pathFormatter, sandbox, label, script=(None, None),
                 digestEnv={}, env={}, tools={}, args=[]):
        self.__script = script[0]
        self.__digestScript = script[1]
        super().__init__(package, pathFormatter, sandbox, label, digestEnv,
                         env, tools, args)

    def getScript(self):
        return self.__script

    def getJenkinsScript(self):
        return self.__script

    def getDigestScript(self):
        return self.__digestScript

    def isDeterministic(self):
        """Regular steps are assumed to be deterministic."""
        return True

class BuildStep(RegularStep):
    def __init__(self, package, pathFormatter, sandbox=None, script=(None, None),
                 digestEnv={}, env={}, tools={}, args=[]):
        super().__init__(package, pathFormatter, sandbox, "build", script,
                         digestEnv, env, tools, args)

    def isBuildStep(self):
        return True

class PackageStep(RegularStep):
    def __init__(self, package, pathFormatter, sandbox=None, script=(None, None),
                 digestEnv={}, env={}, tools={}, args=[]):
        self.__used = False
        super().__init__(package, pathFormatter, sandbox, "dist", script,
                         digestEnv, env, tools, args)

    def isPackageStep(self):
        return True

    def isUsed(self):
        return self.__used

    def markUsed(self):
        self.__used = True


class Package(object):
    """Representation of a package that was created from a recipe.

    Usually multiple packages will be created from a single recipe. This is
    either due to multiple upstream recipes or different variants of the same
    package. This does not preclude the possibility that multiple Package
    objects describe exactly the same package (read: same Variant-Id). It is
    the responsibility of the build backend to detect this and build only one
    package.
    """
    def __init__(self, name, stack, pathFormatter, recipe, sandbox,
                 directDepSteps, indirectDepSteps, states):
        self.__name = name
        self.__stack = stack
        self.__pathFormatter = pathFormatter
        self.__recipe = recipe
        self.__sandbox = sandbox
        self.__directDepSteps = directDepSteps[:]
        tmp = set(indirectDepSteps)
        if sandbox and sandbox.isEnabled(): tmp.add(sandbox.getStep())
        self.__indirectDepSteps = sorted(tmp)
        self.__states = states
        self.__checkoutStep = CheckoutStep(self, pathFormatter)
        self.__buildStep = BuildStep(self, pathFormatter)
        self.__packageStep = PackageStep(self, pathFormatter)

    def getName(self):
        """Name of the package"""
        return self.__name

    def getStack(self):
        """Returns the recipe processing stack leading to this package.

        The method returns a list of package names. The first entry is a root
        recipe and the last entry is this package."""
        return self.__stack

    def getRecipe(self):
        """Return Recipe object that was the template for this package."""
        return self.__recipe

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
        """Return list of all dependencies of the package.

        This list includes all direct and indirect dependencies."""
        return sorted(set(self.__directDepSteps) | set(self.__indirectDepSteps))

    def _setCheckoutStep(self, script, fullEnv, digestEnv, env, tools, deterministic):
        self.__checkoutStep = CheckoutStep(
            self, self.__pathFormatter, self.__sandbox, script, fullEnv,
            digestEnv, env, tools, deterministic)
        return self.__checkoutStep

    def getCheckoutStep(self):
        """Return the checkout step of this package."""
        return self.__checkoutStep

    def _setBuildStep(self, script, digestEnv, env, tools, args):
        self.__buildStep = BuildStep(
            self, self.__pathFormatter, self.__sandbox, script, digestEnv, env,
            tools, args)
        return self.__buildStep

    def getBuildStep(self):
        """Return the build step of this package."""
        return self.__buildStep

    def _setPackageStep(self, script, digestEnv, env, tools, args):
        self.__packageStep = PackageStep(
            self, self.__pathFormatter, self.__sandbox, script, digestEnv, env,
            tools, args)
        return self.__packageStep

    def getPackageStep(self):
        """Return the package step of this package."""
        return self.__packageStep

    def _getStates(self):
        return self.__states


# FIXME: implement this on our own without the Template class. How to do proper
# escaping?
class IncludeHelper:

    class Resolver:
        def __init__(self, fileLoader, baseDir, varBase, origText):
            self.fileLoader = fileLoader
            self.baseDir = baseDir
            self.varBase = varBase
            self.prolog = []
            self.incDigests = [ asHexStr(hashlib.sha1(origText.encode('utf8')).digest()) ]
            self.count = 0

        def __getitem__(self, item):
            mode = item[0]
            item = item[1:]
            content = []
            try:
                paths = sorted(glob(os.path.join(self.baseDir, item)))
                if not paths:
                    raise ParseError("No files matched in include pattern '{}'!"
                        .format(item))
                for path in paths:
                    content.append(self.fileLoader(path))
            except OSError as e:
                raise ParseError("Error including '"+item+"': " + str(e))
            content = b''.join(content)

            self.incDigests.append(asHexStr(hashlib.sha1(content).digest()))
            if mode == '<':
                var = "_{}{}".format(self.varBase, self.count)
                self.count += 1
                self.prolog.extend([
                    "{VAR}=$(mktemp)".format(VAR=var),
                    "_BOB_TMP_CLEANUP+=( ${VAR} )".format(VAR=var),
                    "base64 -d > ${VAR} <<EOF".format(VAR=var)])
                self.prolog.extend(sliceString(b64encode(content).decode("ascii"), 76))
                self.prolog.append("EOF")
                ret = "${" + var + "}"
            else:
                assert mode == "'"
                ret = quote(content.decode('utf8'))

            return ret

    def __init__(self, fileLoader, baseDir, varBase):
        self.__pattern = re.compile(r"""
            \$<(?:
                (?P<escaped>\$)     |
                (?P<named>[<'][^'>]+)['>]>  |
                (?P<braced>[<'][^'>]+)['>]> |
                (?P<invalid>)
            )
            """, re.VERBOSE)
        self.__baseDir = baseDir
        self.__varBase = re.sub(r'[^a-zA-Z0-9_]', '_', varBase, flags=re.DOTALL)
        self.__fileLoader = fileLoader

    def resolve(self, text):
        if isinstance(text, str):
            resolver = IncludeHelper.Resolver(self.__fileLoader, self.__baseDir, self.__varBase, text)
            t = Template(text)
            t.delimiter = '$<'
            t.pattern = self.__pattern
            ret = t.substitute(resolver)
            return ("\n".join(resolver.prolog + [ret]), "\n".join(resolver.incDigests))
        else:
            return (None, None)

def mergeFilter(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left + right

class ScmValidator:
    def __init__(self, scmSpecs):
        self.__scmSpecs = scmSpecs

    def __validateScm(self, scm):
        if 'scm' not in scm:
            raise schema.SchemaMissingKeyError("Missing 'scm' key in {}".format(scm), None)
        if scm['scm'] not in self.__scmSpecs.keys():
            raise schema.SchemaWrongKeyError('Invalid SCM: {}'.format(scm['scm']), None)
        self.__scmSpecs[scm['scm']].validate(scm)

    def validate(self, data):
        if isinstance(data, dict):
            self.__validateScm(data)
        elif isinstance(data, list):
            for i in data: self.__validateScm(i)
        else:
            raise schema.SchemaUnexpectedTypeError(
                'checkoutSCM must be a SCM spec or a list threreof',
                None)
        return data


RECIPE_NAME_SCHEMA = schema.Regex(r'^[0-9A-Za-z_.+-]+$')

class Recipe(object):
    """Representation of a single recipe

    Multiple instaces of this class will be created if the recipe used the
    ``multiPackage`` keyword.  In this case the getName() method will return
    the name of the original recipe but the getPackageName() method will return
    it with some addition suffix. Without a ``multiPackage`` keyword there will
    only be one Recipe instance.
    """

    class Dependency(object):
        def __init__(self, recipe, env, fwd, use, cond):
            self.recipe = recipe
            self.envOverride = env
            self.provideGlobal = fwd
            self.use = use
            self.useEnv = "environment" in self.use
            self.useTools = "tools" in self.use
            self.useBuildResult = "result" in self.use
            self.useDeps = "deps" in self.use
            self.useSandbox = "sandbox" in self.use
            self.condition = cond

        @staticmethod
        def __parseEntry(dep, env, fwd, use, cond):
            if isinstance(dep, str):
                return [ Recipe.Dependency(dep, env, fwd, use, cond) ]
            else:
                envOverride = dep.get("environment")
                if envOverride:
                    env = env.copy()
                    env.update(envOverride)
                fwd = dep.get("forward", fwd)
                use = dep.get("use", use)
                newCond = dep.get("if")
                if newCond is not None:
                    cond = "$(and,{},{})".format(cond, newCond) if cond is not None else newCond
                name = dep.get("name")
                if name:
                    if "depends" in dep:
                        raise ParseError("A dependency must not use 'name' and 'depends' at the same time!")
                    return [ Recipe.Dependency(name, env, fwd, use, cond) ]
                dependencies = dep.get("depends")
                if dependencies is None:
                    raise ParseError("Either 'name' or 'depends' required for dependencies!")
                return Recipe.Dependency.parseEntries(dependencies, env, fwd, use, cond)

        @staticmethod
        def parseEntries(deps, env={}, fwd=False, use=["result", "deps"], cond=None):
            """Returns an iterator yielding all dependencies as flat list"""
            # return flattened list of dependencies
            return chain.from_iterable(
                Recipe.Dependency.__parseEntry(dep, env, fwd, use, cond)
                for dep in deps )

    class InjectedDep:
        def __init__(self, packageStep):
            self.provideGlobal = False
            self.use = ["result"]
            self.useEnv = False
            self.useTools = False
            self.useBuildResult = True
            self.useSandbox = False
            self.packageStep = packageStep

    @staticmethod
    def loadFromFile(recipeSet, fileName, properties, schema):
        recipe = recipeSet.loadYaml(fileName, schema)

        # MultiPackages are handled as separate recipes with an anonymous base
        # class. Ignore first dir in path, which is 'recipes' by default.
        # Following dirs are treated as categories separated by '::'.
        baseName = os.path.splitext( fileName )[0].split( os.sep )[1:]
        for n in baseName: RECIPE_NAME_SCHEMA.validate(n)
        baseName = "::".join( baseName )
        baseDir = os.path.dirname(fileName)
        if "multiPackage" in recipe:
            anonBaseClass = Recipe(recipeSet, recipe, fileName, baseDir, baseName, baseName, properties)
            return [
                Recipe(recipeSet, subSpec, fileName, baseDir, baseName+"-"+subName, baseName, properties, anonBaseClass)
                for (subName, subSpec) in recipe["multiPackage"].items() ]
        else:
            return [ Recipe(recipeSet, recipe, fileName, baseDir, baseName, baseName, properties) ]

    def __init__(self, recipeSet, recipe, sourceFile, baseDir, packageName, baseName, properties, anonBaseClass=None):
        self.__recipeSet = recipeSet
        self.__sources = [ sourceFile ] if anonBaseClass is None else []
        self.__classesResolved = False
        self.__inherit = recipe.get("inherit", [])
        self.__anonBaseClass = anonBaseClass
        self.__deps = list(Recipe.Dependency.parseEntries(recipe.get("depends", [])))
        filt = recipe.get("filter", {})
        if filt: warnFilter.warn(baseName)
        self.__filterEnv = maybeGlob(filt.get("environment"))
        self.__filterTools = maybeGlob(filt.get("tools"))
        self.__filterSandbox = maybeGlob(filt.get("sandbox"))
        self.__packageName = packageName
        self.__baseName = baseName
        self.__root = recipe.get("root")
        self.__provideTools = { name : AbstractTool(spec)
            for (name, spec) in recipe.get("provideTools", {}).items() }
        self.__provideVars = recipe.get("provideVars", {})
        self.__provideDeps = set(recipe.get("provideDeps", []))
        self.__provideSandbox = recipe.get("provideSandbox")
        self.__varSelf = recipe.get("environment", {})
        self.__varPrivate = recipe.get("privateEnvironment", {})
        self.__checkoutVars = set(maybeGlob(recipe.get("checkoutVars", [])))
        if "checkoutConsume" in recipe:
            warnCheckoutConsume.warn(baseName)
            self.__checkoutVars |= set(maybeGlob(recipe["checkoutConsume"]))
        self.__checkoutVarsWeak = set(maybeGlob(recipe.get("checkoutVarsWeak", [])))
        self.__buildVars = set(maybeGlob(recipe.get("buildVars", [])))
        if "buildConsume" in recipe:
            warnBuildConsume.warn(baseName)
            self.__buildVars |= set(maybeGlob(recipe["buildConsume"]))
        self.__buildVars |= self.__checkoutVars
        self.__buildVarsWeak = set(maybeGlob(recipe.get("buildVarsWeak", [])))
        self.__buildVarsWeak |= self.__checkoutVarsWeak
        self.__packageVars = set(maybeGlob(recipe.get("packageVars", [])))
        if "packageConsume" in recipe:
            warnPackageConsume.warn(baseName)
            self.__packageVars |= set(maybeGlob(recipe["packageConsume"]))
        self.__packageVars |= self.__buildVars
        self.__packageVarsWeak = set(maybeGlob(recipe.get("packageVarsWeak", [])))
        self.__packageVarsWeak |= self.__buildVarsWeak
        self.__toolDepCheckout = set(maybeGlob(recipe.get("checkoutTools", [])))
        self.__toolDepBuild = set(maybeGlob(recipe.get("buildTools", [])))
        self.__toolDepBuild |= self.__toolDepCheckout
        self.__toolDepPackage = set(maybeGlob(recipe.get("packageTools", [])))
        self.__toolDepPackage |= self.__toolDepBuild
        self.__shared = recipe.get("shared", False)
        self.__properties = {
            n : p(n in recipe, recipe.get(n))
            for (n, p) in properties.items()
        }

        incHelper = IncludeHelper(recipeSet.loadBinary, baseDir, packageName)

        (checkoutScript, checkoutDigestScript) = incHelper.resolve(recipe.get("checkoutScript"))
        checkoutSCMs = recipe.get("checkoutSCM", [])
        if isinstance(checkoutSCMs, dict):
            checkoutSCMs = [checkoutSCMs]
        elif not isinstance(checkoutSCMs, list):
            raise ParseError("checkoutSCM must be a dict or a list")
        i = 0
        for scm in checkoutSCMs:
            scm["recipe"] = "{}#{}".format(sourceFile, i)
            i += 1
        self.__checkout = (checkoutScript, checkoutDigestScript, checkoutSCMs)
        self.__build = incHelper.resolve(recipe.get("buildScript"))
        self.__package = incHelper.resolve(recipe.get("packageScript"))

        # Consider checkout deterministic by default if no checkout script is
        # involved.
        self.__checkoutDeterministic = recipe.get("checkoutDeterministic", checkoutScript is None)

    def resolveClasses(self):
        # must be done only once
        if self.__classesResolved: return
        self.__classesResolved = True

        # calculate order of classes (depth first)
        visited = set()
        backlog = [ self.__recipeSet.getClass(c) for c in self.__inherit ]
        if self.__anonBaseClass: backlog.insert(0, self.__anonBaseClass)
        inherit = []
        while backlog:
            next = backlog.pop(0)
            if next.getName() in visited: continue
            subInherit = [ self.__recipeSet.getClass(c) for c in next.__inherit if c not in visited ]
            if subInherit:
                # prepend and re-insert current class
                backlog[0:0] = subInherit + [next]
            else:
                inherit.append(next)
                visited.add(next.getName())

        # inherit classes
        inherit.reverse()
        for cls in inherit:
            self.__sources.extend(cls.__sources)
            self.__deps[0:0] = cls.__deps
            self.__filterEnv = mergeFilter(self.__filterEnv, cls.__filterEnv)
            self.__filterTools = mergeFilter(self.__filterTools, cls.__filterTools)
            self.__filterSandbox = mergeFilter(self.__filterSandbox, cls.__filterSandbox)
            if self.__root is None: self.__root = cls.__root
            tmp = cls.__provideTools.copy()
            tmp.update(self.__provideTools)
            self.__provideTools = tmp
            tmp = cls.__provideVars.copy()
            tmp.update(self.__provideVars)
            self.__provideVars = tmp
            self.__provideDeps |= cls.__provideDeps
            if self.__provideSandbox is None: self.__provideSandbox = cls.__provideSandbox
            tmp = cls.__varSelf.copy()
            tmp.update(self.__varSelf)
            self.__varSelf = tmp
            tmp = cls.__varPrivate.copy()
            tmp.update(self.__varPrivate)
            self.__varPrivate = tmp
            self.__checkoutVars |= cls.__checkoutVars
            self.__checkoutVarsWeak |= cls.__checkoutVarsWeak
            self.__buildVars |= cls.__buildVars
            self.__buildVarsWeak |= cls.__buildVarsWeak
            self.__packageVars |= cls.__packageVars
            self.__packageVarsWeak |= cls.__packageVarsWeak
            self.__toolDepCheckout |= cls.__toolDepCheckout
            self.__toolDepBuild |= cls.__toolDepBuild
            self.__toolDepPackage |= cls.__toolDepPackage
            (checkoutScript, checkoutDigestScript, checkoutSCMs) = self.__checkout
            self.__checkoutDeterministic = self.__checkoutDeterministic and cls.__checkoutDeterministic
            # merge scripts
            checkoutScript = joinScripts([cls.__checkout[0], checkoutScript])
            checkoutDigestScript = joinScripts([cls.__checkout[1], checkoutDigestScript], "\n")
            # merge SCMs
            scms = cls.__checkout[2][:]
            scms.extend(checkoutSCMs)
            checkoutSCMs = scms
            # store result
            self.__checkout = (checkoutScript, checkoutDigestScript, checkoutSCMs)
            self.__build = (
                joinScripts([cls.__build[0], self.__build[0]]),
                joinScripts([cls.__build[1], self.__build[1]], "\n")
            )
            self.__package = (
                joinScripts([cls.__package[0], self.__package[0]]),
                joinScripts([cls.__package[1], self.__package[1]], "\n")
            )
            for (n, p) in self.__properties.items():
                p.inherit(cls.__properties[n])

        # the package step must always be valid
        if self.__package[0] is None:
            self.__package = ("", 'da39a3ee5e6b4b0d3255bfef95601890afd80709')

        # check provided dependencies
        availDeps = [ d.recipe for d in self.__deps ]
        providedDeps = set()
        for pattern in self.__provideDeps:
            l = set(d for d in availDeps if fnmatch.fnmatchcase(d, pattern))
            if not l:
                raise ParseError("Unknown dependency '{}' in provideDeps".format(pattern))
            providedDeps |= l
        self.__provideDeps = providedDeps

    def getRecipeSet(self):
        return self.__recipeSet

    def getSources(self):
        return self.__sources

    def getPackageName(self):
        """Get the name of the package that is drived from this recipe.

        Usually the package name is the same as the recipe name. But in case of
        a ``multiPackage`` the package name has an additional suffix.
        """
        return self.__packageName

    def getName(self):
        """Get plain recipe name.

        In case of a ``multiPackage`` multiple packages may be derived from the
        same recipe. This method returns the plain recipe name.
        """
        return self.__baseName

    def isRoot(self):
        """Returns True if this is a root recipe."""
        return self.__root == True

    def prepare(self, pathFormatter, inputEnv, sandboxEnabled, states, sandbox=None,
                inputTools=Env(), inputStack=[]):
        if self.__packageName in inputStack:
            raise ParseError("Recipes are cyclic (1st package in cylce)")
        stack = inputStack + [self.__packageName]

        # make copies because we will modify them
        tools = inputTools.prune(self.__filterTools)
        inputEnv = inputEnv.derive()
        inputEnv.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
            "tools" : inputTools, "stack" : stack })
        varSelf = {}
        for (key, value) in self.__varSelf.items():
            varSelf[key] = inputEnv.substitute(value, "environment::"+key)
        env = inputEnv.prune(self.__filterEnv).derive(varSelf)
        if sandbox is not None:
            if not checkGlobList(sandbox.getStep().getPackage().getName(), self.__filterSandbox):
                sandbox = None
        states = { n : s.copy() for (n,s) in states.items() }

        # update plugin states
        for s in states.values(): s.onEnter(env, tools, self.__properties)

        # traverse dependencies
        directPackages = []
        indirectPackages = []
        results = []
        depEnv = env.derive()
        depTools = tools.derive()
        depSandbox = sandbox
        depStates = { n : s.copy() for (n,s) in states.items() }
        allDeps = self.__deps[:]
        thisDeps = {}
        i = 0
        while i < len(allDeps):
            dep = allDeps[i]
            i += 1
            env.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
                "tools" : tools, "stack" : stack })

            if isinstance(dep, Recipe.Dependency):
                if not env.evaluate(dep.condition, "dependency "+dep.recipe): continue
                r = self.__recipeSet.getRecipe(dep.recipe)
                try:
                    p = r.prepare(pathFormatter, depEnv.derive(dep.envOverride),
                                  sandboxEnabled, depStates, depSandbox, depTools,
                                  stack).getPackageStep()
                except ParseError as e:
                    e.pushFrame(r.getPackageName())
                    raise e

                thisDeps[dep.recipe] = p
                if dep.useDeps:
                    # add provided dependencies at the end
                    providedDeps = p.getProvidedDeps()
                    allDeps.extend(Recipe.InjectedDep(d) for d in providedDeps)
                directPackages.append(p)
            else:
                p = dep.packageStep
                indirectPackages.append(p)

            for (n, s) in states.items():
                if n in dep.use:
                    s.onUse(p.getPackage()._getStates()[n])
                    if dep.provideGlobal: depStates[n].onUse(p.getPackage()._getStates()[n])
            if dep.useBuildResult:
                results.append(p)
            if dep.useTools:
                tools.update(p.getProvidedTools())
                if dep.provideGlobal: depTools.update(p.getProvidedTools())
            if dep.useEnv:
                env.update(p.getProvidedEnv())
                if dep.provideGlobal: depEnv.update(p.getProvidedEnv())
            if dep.useSandbox:
                sandbox = p.getProvidedSandbox()
                if dep.provideGlobal: depSandbox = p.getProvidedSandbox()

        # apply private environment
        env.setFunArgs({ "recipe" : self, "sandbox" : sandbox,
            "tools" : tools, "stack" : stack })
        varPrivate = {}
        for (key, value) in self.__varPrivate.items():
            varPrivate[key] = env.substitute(value, "privateEnvironment::"+key)
        env.update(varPrivate)

        # filter duplicate results, fail on different variants of same package
        i = 0
        while i < len(results):
            j = i+1
            r = results[i]
            while j < len(results):
                if r.getPackage().getName() == results[j].getPackage().getName():
                    if r.getVariantId() != results[j].getVariantId():
                        raise ParseError("Incompatibe variants of package: {} vs. {}"
                            .format("/".join(r.getPackage().getStack()),
                                    "/".join(results[j].getPackage().getStack())),
                            help=
"""This error is caused by '{PKG}' that is passed upwards via 'provideDeps' from multiple dependencies of '{CUR}'.
These dependencies constitute different variants of '{PKG}' and can therefore not be used in '{CUR}'."""
    .format(PKG=r.getPackage().getName(), CUR=self.__packageName))
                    del results[j]
                else:
                    j += 1
            i += 1

        # mark actually used steps as such
        if sandbox and sandbox.isEnabled(): sandbox.getStep().markUsed()
        toolPackages = [ t.step for t in tools.prune(self.__toolDepPackage).values() ]
        for p in toolPackages: p.markUsed()
        for p in results: p.markUsed()
        indirectPackages.extend(toolPackages)

        # create package
        directPackages = [ p for p in directPackages if p.isUsed() ]
        indirectPackages = [ p for p in indirectPackages if p.isUsed() ]
        p = Package(self.__packageName, stack, pathFormatter, self, sandbox,
                    directPackages, indirectPackages, states)

        # optional checkout step
        if self.__checkout != (None, None, []):
            checkoutDigestEnv = env.prune(self.__checkoutVars)
            checkoutEnv = ( env.prune(self.__checkoutVars | self.__checkoutVarsWeak)
                if self.__checkoutVarsWeak else checkoutDigestEnv )
            srcStep = p._setCheckoutStep(self.__checkout, env, checkoutDigestEnv,
                checkoutEnv, tools.prune(self.__toolDepCheckout),
                self.__checkoutDeterministic)
        else:
            srcStep = p.getCheckoutStep() # return invalid step

        # optional build step
        if self.__build:
            buildDigestEnv = env.prune(self.__buildVars)
            buildEnv = ( env.prune(self.__buildVars | self.__buildVarsWeak)
                if self.__buildVarsWeak else buildDigestEnv )
            buildStep = p._setBuildStep(self.__build, buildDigestEnv,
                buildEnv, tools.prune(self.__toolDepBuild), [srcStep] + results)
        else:
            buildStep = p.getBuildStep() # return invalid step

        # mandatory package step
        packageDigestEnv = env.prune(self.__packageVars)
        packageEnv = ( env.prune(self.__packageVars | self.__packageVarsWeak)
            if self.__packageVarsWeak else packageDigestEnv )
        p._setPackageStep(self.__package, packageDigestEnv, packageEnv,
            tools.prune(self.__toolDepPackage), [buildStep])
        packageStep = p.getPackageStep()

        # provide environment
        provideEnv = {}
        for (key, value) in self.__provideVars.items():
            provideEnv[key] = env.substitute(value, "provideVars::"+key)
        packageStep._setProvidedEnv(provideEnv)

        # provide tools
        provideTools = { name : tool.prepare(packageStep, env)
            for (name, tool) in self.__provideTools.items() }
        packageStep._setProvidedTools(provideTools)

        # provide deps (direct and indirect deps)
        provideDeps = []
        for dep in self.__deps:
            if dep.recipe not in self.__provideDeps: continue
            subDep = thisDeps.get(dep.recipe)
            if subDep is not None:
                provideDeps.append(subDep)
                for d in subDep.getProvidedDeps(): provideDeps.append(d)
        packageStep._setProvidedDeps(provideDeps)

        # provide Sandbox
        if self.__provideSandbox:
            packageStep._setProvidedSandbox(Sandbox(packageStep, env, sandboxEnabled,
                                                    self.__provideSandbox))

        # update plugin states
        for s in states.values(): s.onFinish(env, tools, self.__properties, p)

        if self.__shared:
            if packageStep.getBuildId() is None:
                raise ParseError("Shared packages must be deterministic!")
            packageStep._setShared(True)

        return p


def funEqual(args, **options):
    if len(args) != 2: raise ParseError("eq expects two arguments")
    return "true" if (args[0] == args[1]) else "false"

def funNotEqual(args, **options):
    if len(args) != 2: raise ParseError("ne expects two arguments")
    return "true" if (args[0] != args[1]) else "false"

def funNot(args, **options):
    if len(args) != 1: raise ParseError("not expects one argument")
    return "true" if _isFalse(args[0].strip().lower()) else "false"

def funOr(args, **options):
    for arg in args:
        if not _isFalse(arg):
            return "true"
    return "false"

def funAnd(args, **options):
    for arg in args:
        if _isFalse(arg):
            return "false"
    return "true"

def funMatch(args, **options):
    try:
        [2, 3].index(len(args))
    except ValueError:
        raise ParseError("match expects either two or three arguments")

    flags = 0
    if len(args) == 3:
        if args[2] == 'i':
            flags = re.IGNORECASE
        else:
            raise ParseError('match only supports the ignore case flag "i"')

    if re.search(args[1],args[0],flags):
        return "true"
    else:
        return "false"

def funIfThenElse(args, **options):
    if len(args) != 3: raise ParseError("if-then-else expects three arguments")
    if _isFalse(args[0].strip().lower()):
        return args[2]
    else:
        return args[1]

def funSubst(args, **options):
    if len(args) != 3: raise ParseError("subst expects three arguments")
    return args[2].replace(args[0], args[1])

def funStrip(args, **options):
    if len(args) != 1: raise ParseError("strip expects one argument")
    return args[0].strip()

def funSandboxEnabled(args, sandbox, **options):
    if len(args) != 0: raise ParseError("is-sandbox-enabled expects no arguments")
    return "true" if ((sandbox is not None) and sandbox.isEnabled()) else "false"

def funToolDefined(args, tools, **options):
    if len(args) != 1: raise ParseError("is-tool-defined expects one argument")
    return "true" if (args[0] in tools) else "false"


class ArchiveValidator:
    def __init__(self):
        self.__validTypes = schema.Schema({'backend': schema.Or('none', 'file', 'http')},
            ignore_extra_keys=True)
        self.__backends = {
            'none' : schema.Schema({'backend' : 'none'}),
            'file' : schema.Schema({'backend' : 'file', 'path' : str}),
            'http' : schema.Schema({'backend' : 'http', 'url' : str})
        }

    def validate(self, data):
        self.__validTypes.validate(data)
        return self.__backends[data['backend']].validate(data)

class MountValidator:
    def __init__(self):
        self.__options = schema.Schema(
            ["nolocal", "nojenkins", "nofail", "rw"],
            error="Invalid mount option specified!")

    def validate(self, data):
        if isinstance(data, str):
            return (data, data, [])
        elif isinstance(data, list) and (len(data) in [2, 3]):
            if not isinstance(data[0], str):
                raise schema.SchemaError(None, "Expected string as first mount argument!")
            if not isinstance(data[1], str):
                raise schema.SchemaError(None, "Expected string as second mount argument!")
            if len(data) == 3:
                self.__options.validate(data[2])
                return data
            else:
                return (data[0], data[1], [])

        raise schema.SchemaError(None, "Mount entry must be a string or a two/three items list!")

class RecipeSet:

    USER_CONFIG_SCHEMA = schema.Schema(
        {
            schema.Optional('environment') : schema.Schema({
                schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$') : str
            }),
            schema.Optional('alias') : schema.Schema({
                schema.Regex(r'^[0-9A-Za-z_-]+$') : str
            }),
            schema.Optional('whitelist') : schema.Schema([
                schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$')
            ]),
            schema.Optional('archive') : ArchiveValidator(),
            schema.Optional('include') : schema.Schema([str]),
            schema.Optional('scmOverrides') : [ schema.Schema({
                schema.Optional('match') : schema.Schema({ str: str }),
                schema.Optional('del') : [
                    "branch", "commit", "digestSHA1", "digestSHA256", "dir",
                    "extract", "fileName", "if", "rev", "revision", "tag"
                ],
                schema.Optional('set') : schema.Schema({ str : str }),
                schema.Optional('replace') : schema.Schema({
                    str : schema.Schema({
                        'pattern' : str,
                        'replacement' : str
                    })
                })
            }) ],
        })

    STATIC_CONFIG_SCHEMA = schema.Schema({
        schema.Optional('bobMinimumVersion') : schema.Regex(r'^[0-9]+(\.[0-9]+){0,2}$'),
        schema.Optional('plugins') : [str]
    })

    def __init__(self):
        self.__defaultEnv = {}
        self.__aliases = {}
        self.__rootRecipes = []
        self.__recipes = {}
        self.__classes = {}
        self.__whiteList = set(["TERM", "SHELL", "USER", "HOME"])
        self.__archive = { "backend" : "none" }
        self.__scmOverrides = []
        self.__hooks = {}
        self.__projectGenerators = {}
        self.__configFiles = []
        self.__properties = {}
        self.__states = {}
        self.__cache = YamlCache()
        self.__stringFunctions = {
            "eq" : funEqual,
            "or" : funOr,
            "and" : funAnd,
            "if-then-else" : funIfThenElse,
            "is-sandbox-enabled" : funSandboxEnabled,
            "is-tool-defined" : funToolDefined,
            "ne" : funNotEqual,
            "not" : funNot,
            "strip" : funStrip,
            "subst" : funSubst,
            "match" : funMatch,
        }
        self.__plugins = {}

    def __addRecipe(self, recipe):
        name = recipe.getPackageName()
        if name in self.__recipes:
            raise ParseError("Package "+name+" already defined")
        self.__recipes[name] = recipe

    def __addClass(self, recipe):
        name = recipe.getPackageName()
        if name in self.__classes:
            raise ParseError("Class "+name+" already defined")
        self.__classes[name] = recipe

    def __loadPlugins(self, plugins):
        for p in plugins:
            name = os.path.join("plugins", p+".py")
            if not os.path.exists(name):
                raise ParseError("Plugin '"+name+"' not found!")
            mangledName = "__bob_plugin_"+p
            self.__plugins[mangledName] = self.__loadPlugin(mangledName, name)

    def __loadPlugin(self, mangledName, name):
        # dummy load file to hash state
        self.loadBinary(name)
        try:
            from importlib.machinery import SourceFileLoader
            loader = SourceFileLoader(mangledName, name)
            mod = loader.load_module()
        except SyntaxError as e:
            import traceback
            raise ParseError("Error loading plugin "+name+": "+str(e),
                             help=traceback.format_exc())
        except Exception as e:
            raise ParseError("Error loading plugin "+name+": "+str(e))

        try:
            manifest = mod.manifest
        except AttributeError:
            raise ParseError("Plugin '"+name+"' did not define 'manifest'!")
        apiVersion = manifest.get('apiVersion')
        if apiVersion is None:
            raise ParseError("Plugin '"+name+"' did not define 'apiVersion'!")
        if compareVersion(BOB_VERSION, apiVersion) < 0:
            raise ParseError("Your Bob is too old. Plugin '"+name+"' requires at least version "+apiVersion+"!")

        hooks = manifest.get('hooks', {})
        if not isinstance(hooks, dict):
            raise ParseError("Plugin '"+name+"': 'hooks' has wrong type!")
        for (hook, fun) in hooks.items():
            if not isinstance(hook, str):
                raise ParseError("Plugin '"+name+"': hook name must be a string!")
            if not callable(fun):
                raise ParseError("Plugin '"+name+"': "+hook+": hook must be callable!")
            self.__hooks.setdefault(hook, []).append(fun)

        projectGenerators = manifest.get('projectGenerators', {})
        if not isinstance(projectGenerators, dict):
            raise ParseError("Plugin '"+name+"': 'projectGenerators' has wrong type!")
        self.__projectGenerators.update(projectGenerators)

        properties = manifest.get('properties', {})
        if not isinstance(properties, dict):
            raise ParseError("Plugin '"+name+"': 'properties' has wrong type!")
        for (i,j) in properties.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': property name must be a string!")
            if not issubclass(j, PluginProperty):
                raise ParseError("Plugin '"+name+"': property '" +i+"' has wrong type!")
            if i in self.__properties:
                raise ParseError("Plugin '"+name+"': property '" +i+"' already defined by other plugin!")
        self.__properties.update(properties)

        states = manifest.get('state', {})
        if not isinstance(states, dict):
            raise ParseError("Plugin '"+name+"': 'states' has wrong type!")
        for (i,j) in states.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': state tracker name must be a string!")
            if i in ["environment", "tools", "result", "deps", "sandbox"]:
                raise ParseError("Plugin '"+name+"': state tracker has reserved name!")
            if not issubclass(j, PluginState):
                raise ParseError("Plugin '"+name+"': state tracker '" +i+"' has wrong type!")
            if i in self.__states:
                raise ParseError("Plugin '"+name+"': state tracker '" +i+"' already defined by other plugin!")
        self.__states.update(states)

        funs = manifest.get('stringFunctions', {})
        if not isinstance(funs, dict):
            raise ParseError("Plugin '"+name+"': 'stringFunctions' has wrong type!")
        for (i,j) in funs.items():
            if not isinstance(i, str):
                raise ParseError("Plugin '"+name+"': string function name must be a string!")
            if i in self.__stringFunctions:
                raise ParseError("Plugin '"+name+"': string function '" +i+"' already defined by other plugin!")
        self.__stringFunctions.update(funs)

        return mod

    def defineHook(self, name, value):
        self.__hooks[name] = [value]

    def setConfigFiles(self, configFiles):
        self.__configFiles = configFiles

    def getHook(self, name):
        return self.__hooks[name][-1]

    def getHookStack(self, name):
        return self.__hooks.get(name, [])

    def getProjectGenerators(self):
        return self.__projectGenerators

    def envWhiteList(self):
        return set(self.__whiteList)

    def archiveSpec(self):
        return self.__archive

    def defaultEnv(self):
        return self.__defaultEnv

    def scmOverrides(self):
        return self.__scmOverrides

    def loadBinary(self, path):
        return self.__cache.loadBinary(path)

    def loadYaml(self, path, schema, default={}):
        if os.path.exists(path):
            return self.__cache.loadYaml(path, schema, default)
        else:
            return default

    def parse(self):
        if not os.path.isdir("recipes"):
            raise ParseError("No recipes directory found.")
        self.__cache.open()
        try:
            self.__parse()

            # config files overrule everything else
            for c in self.__configFiles:
                c = str(c) + ".yaml"
                if not os.path.isfile(c):
                    raise ParseError("Config file {} does not exist!".format(c))
                self.__parseUserConfig(c)
        finally:
            self.__cache.close()

    def __parse(self):
        config = self.loadYaml("config.yaml", RecipeSet.STATIC_CONFIG_SCHEMA)
        minVer = config.get("bobMinimumVersion", "0.1")
        if compareVersion(BOB_VERSION, minVer) < 0:
            raise ParseError("Your Bob is too old. At least version "+minVer+" is required!")
        self.__extStrings = compareVersion(minVer, "0.3") >= 0
        self.__loadPlugins(config.get("plugins", []))
        self.__createSchemas()

        # user config(s)
        self.__parseUserConfig("default.yaml")

        # finally parse recipes
        for root, dirnames, filenames in os.walk('classes'):
            for path in fnmatch.filter(filenames, "*.yaml"):
                try:
                    [r] = Recipe.loadFromFile(self, os.path.join(root, path),
                        self.__properties, self.__classSchema)
                    self.__addClass(r)
                except ParseError as e:
                    e.pushFrame(path)
                    raise

        for root, dirnames, filenames in os.walk('recipes'):
            for path in fnmatch.filter(filenames, "*.yaml"):
                try:
                    for r in Recipe.loadFromFile(self,  os.path.join(root, path),
                                                 self.__properties, self.__recipeSchema):
                        self.__addRecipe(r)
                except ParseError as e:
                    e.pushFrame(path)
                    raise

        # resolve recipes and their classes
        for recipe in self.__recipes.values():
            try:
                recipe.resolveClasses()
            except ParseError as e:
                e.pushFrame(recipe.getPackageName())
                raise
            if recipe.isRoot():
                self.__rootRecipes.append(recipe)

    def __parseUserConfig(self, fileName):
        cfg = self.loadYaml(fileName, RecipeSet.USER_CONFIG_SCHEMA)
        self.__defaultEnv.update(cfg.get("environment", {}))
        self.__whiteList |= set(cfg.get("whitelist", []))
        if "archive" in cfg:
            self.__archive = cfg["archive"]
        self.__scmOverrides.extend([ ScmOverride(o) for o in cfg.get("scmOverrides", []) ])
        self.__aliases.update(cfg.get("alias", {}))

        for p in cfg.get("include", []):
            self.__parseUserConfig(str(p) + ".yaml")

    def __createSchemas(self):
        varNameSchema = schema.Regex(r'^[A-Za-z_][A-Za-z0-9_]*$')
        varGlobSchema = schema.Regex(r'^[][A-Za-z_*?][][A-Za-z0-9_*?]*$')
        toolGlobSchema = schema.Regex(r'^[][0-9A-Za-z_.+:*?-]+$')
        varFilterSchema = schema.Regex(r'^!?[][A-Za-z_*?][][A-Za-z0-9_*?]*$')
        recipeFilterSchema = schema.Regex(r'^!?[][0-9A-Za-z_.+:*?-]+$')

        useClauses = ['deps', 'environment', 'result', 'tools', 'sandbox']
        useClauses.extend(self.__states.keys())

        # construct recursive depends clause
        dependsInnerClause = {
            schema.Optional('name') : str,
            schema.Optional('use') : useClauses,
            schema.Optional('forward') : bool,
            schema.Optional('environment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('if') : str
        }
        dependsClause = schema.Schema([
            schema.Or(
                str,
                schema.Schema(dependsInnerClause)
            )
        ])
        dependsInnerClause[schema.Optional('depends')] = dependsClause

        classSchemaSpec = {
            schema.Optional('checkoutScript') : str,
            schema.Optional('buildScript') : str,
            schema.Optional('packageScript') : str,
            schema.Optional('checkoutTools') : [ toolGlobSchema ],
            schema.Optional('buildTools') : [ toolGlobSchema ],
            schema.Optional('packageTools') : [ toolGlobSchema ],
            schema.Optional('checkoutVars') : [ varGlobSchema ],
            schema.Optional('buildVars') : [ varGlobSchema ],
            schema.Optional('packageVars') : [ varGlobSchema ],
            schema.Optional('checkoutVarsWeak') : [ varGlobSchema ],
            schema.Optional('buildVarsWeak') : [ varGlobSchema ],
            schema.Optional('packageVarsWeak') : [ varGlobSchema ],
            schema.Optional('checkoutDeterministic') : bool,
            schema.Optional('checkoutSCM') : ScmValidator({
                'git' : GitScm.SCHEMA,
                'svn' : SvnScm.SCHEMA,
                'cvs' : CvsScm.SCHEMA,
                'url' : UrlScm.SCHEMA
            }),
            schema.Optional('depends') : dependsClause,
            schema.Optional('environment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('filter') : schema.Schema({
                schema.Optional('environment') : [ varFilterSchema ],
                schema.Optional('tools') : [ recipeFilterSchema ],
                schema.Optional('sandbox') : [ recipeFilterSchema ]
            }),
            schema.Optional('inherit') : [str],
            schema.Optional('privateEnvironment') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('provideDeps') : [str],
            schema.Optional('provideTools') : schema.Schema({
                str: schema.Or(
                    str,
                    schema.Schema({
                        'path' : str,
                        schema.Optional('libs') : [str]
                    })
                )
            }),
            schema.Optional('provideVars') : schema.Schema({
                varNameSchema : str
            }),
            schema.Optional('provideSandbox') : schema.Schema({
                'paths' : [str],
                schema.Optional('mount') : schema.Schema([ MountValidator() ],
                    error="provideSandbox: invalid 'mount' property")
            }),
            schema.Optional('root') : bool,
            schema.Optional('shared') : bool
        }
        for (name, prop) in self.__properties.items():
            classSchemaSpec[schema.Optional(name)] = schema.Schema(prop.validate,
                error="property '"+name+"' has an invalid type")

        self.__classSchema = schema.Schema(classSchemaSpec)

        recipeSchemaSpec = classSchemaSpec.copy()
        recipeSchemaSpec[schema.Optional('multiPackage')] = schema.Schema({
            RECIPE_NAME_SCHEMA : self.__classSchema
        })
        self.__recipeSchema = schema.Schema(recipeSchemaSpec)

    def getRecipe(self, packageName):
        if packageName not in self.__recipes:
            raise ParseError("Package {} requested but not found.".format(packageName))
        return self.__recipes[packageName]

    def getClass(self, className):
        if className not in self.__classes:
            raise ParseError("Class {} requested but not found.".format(className))
        return self.__classes[className]

    def generatePackages(self, nameFormatter, envOverrides={}, sandboxEnabled=False):
        def makePred(p):
            return lambda prev, elem: True if elem == p else prev

        # calculate start environment
        env = Env(os.environ).prune([ makePred(pred) for pred in self.__whiteList ])
        env.setLegacy(not self.__extStrings)
        env.setFuns(self.__stringFunctions)
        env.update(self.__defaultEnv)
        env.update(envOverrides)

        # calculate cache key for persisted packages
        h = hashlib.sha1()
        h.update(struct.pack("<I", CACHE_VERSION))
        h.update(self.__cache.getDigest())
        h.update(struct.pack("<I", len(env)))
        for (key, val) in sorted(env.items()):
            h.update(struct.pack("<II", len(key), len(val)))
            h.update((key+val).encode('utf8'))
        h.update(b'\x01' if sandboxEnabled else b'\x00')
        cacheKey = h.digest()

        # try to load the persisted packages
        try:
            with open(".bob-packages.pickle", "rb") as f:
                persistedCacheKey = f.read(len(cacheKey))
                if cacheKey == persistedCacheKey:
                    return PackageUnpickler(f, self.getRecipe, self.__plugins,
                                            nameFormatter).load()
        except (EOFError, OSError, pickle.UnpicklingError):
            pass

        # not cached -> calculate packages
        result = {}
        states = { n:s() for (n,s) in self.__states.items() }
        try:
            BobState().setAsynchronous()
            for root in self.__rootRecipes:
                try:
                    result[root.getPackageName()] = root.prepare(nameFormatter, env,
                                                                 sandboxEnabled,
                                                                 states)
                except ParseError as e:
                    e.pushFrame(root.getPackageName())
                    raise e
            tmp = result.copy()
            for i in self.__aliases:
                try:
                    p = walkPackagePath(tmp, self.__aliases[i])
                    result[i] = p
                except BuildError as e:
                    print(colorize("Bad alias '{}': {}".format(i, str(e)), "33"), file=sys.stderr)
        finally:
            BobState().setSynchronous()

        # save package tree for next invocation
        try:
            with open(".bob-packages.pickle", "wb") as f:
                f.write(cacheKey)
                PackagePickler(f, nameFormatter).dump(result)
        except OSError as e:
            print("Error saving internal state:", str(e), file=sys.stderr)

        return result


class YamlCache:

    def open(self):
        self.__shelve = shelve.open(".bob-cache.shelve")
        self.__files = {}

    def close(self):
        self.__shelve.close()
        h = hashlib.sha1()
        for (name, data) in sorted(self.__files.items()):
            h.update(struct.pack("<I", len(name)))
            h.update(name.encode('utf8'))
            h.update(data)
        self.__digest = h.digest()

    def getDigest(self):
        return self.__digest

    def loadYaml(self, name, yamlSchema, default):
        binStat = binLstat(name)
        if name in self.__shelve:
            cached = self.__shelve[name]
            if ((cached['lstat'] == binStat) and
                (cached.get('vsn') == CACHE_VERSION)):
                self.__files[name] = cached['digest']
                return cached['data']

        with open(name, "r") as f:
            try:
                rawData = f.read()
                data = yaml.safe_load(rawData)
                digest = hashlib.sha1(rawData.encode('utf8')).digest()
            except Exception as e:
                raise ParseError("Error while parsing {}: {}".format(name, str(e)))

        if data is None: data = default
        try:
            data = yamlSchema.validate(data)
        except schema.SchemaError as e:
            raise ParseError("Error while validating {}: {}".format(name, str(e)))

        self.__files[name] = digest
        self.__shelve[name] = {
            'lstat' : binStat,
            'data' : data,
            'vsn' : CACHE_VERSION,
            'digest' : digest
        }
        return data

    def loadBinary(self, name):
        with open(name, "rb") as f:
            result = f.read()
        self.__files[name] = hashlib.sha1(result).digest()
        return result


class PackagePickler(pickle.Pickler):
    def __init__(self, file, pathFormatter):
        super().__init__(file, -1, fix_imports=False)
        self.__pathFormatter = pathFormatter

    def persistent_id(self, obj):
        if obj is self.__pathFormatter:
            return ("pathfmt", None)
        elif isinstance(obj, Recipe):
            return ("recipe", obj.getPackageName())
        else:
            return None

class PackageUnpickler(pickle.Unpickler):
    def __init__(self, file, recipeGetter, plugins, pathFormatter):
        super().__init__(file)
        self.__recipeGetter = recipeGetter
        self.__plugins = plugins
        self.__pathFormatter = pathFormatter

    def persistent_load(self, pid):
        (tag, key) = pid
        if tag == "pathfmt":
            return self.__pathFormatter
        elif tag == "recipe":
            return self.__recipeGetter(key)
        else:
            raise pickle.UnpicklingError("unsupported object")

    def find_class(self, module, name):
        if module.startswith("__bob_plugin_"):
            return getattr(self.__plugins[module], name)
        else:
            return super().find_class(module, name)


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

