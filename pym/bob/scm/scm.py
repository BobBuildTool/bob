# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import ParseError
from ..utils import joinLines
from abc import ABCMeta, abstractmethod
from enum import Enum
from shlex import quote
import fnmatch
import re

SYNTHETIC_SCM_PROPS = frozenset(('__source', 'recipe', 'overridden'))

class ScmOverride:
    def __init__(self, override):
        self.__match = override.get("match", {})
        self.__del = override.get("del", [])
        self.__set = override.get("set", {})
        self.__if = override.get("if", None)
        self.__replaceRaw = override.get("replace", {})
        self.__init()

    def __init(self):
        try:
            self.__replace = { key : (re.compile(subst["pattern"]), subst["replacement"])
                for (key, subst) in self.__replaceRaw.items() }
        except re.error as e:
            raise ParseError("Invalid scmOverrides replace pattern: '{}': {}"
                .format(e.pattern, str(e)))

    def __getstate__(self):
        # We don't persis the if-condition because it has already been
        # evaluated after the initial SCM instance was created. It's also not
        # json-serializable becuase of the IfExpression object.
        return (self.__match, self.__del, self.__set, self.__replaceRaw)

    def __setstate__(self, s):
        (self.__match, self.__del, self.__set, self.__replaceRaw) = s
        self.__if = None
        self.__init()

    def __doesMatch(self, scm, env):
        if self.__if is not None and not env.evaluate(self.__if, "scmOverride::if"): return False
        for (key, value) in self.__match.items():
            if key not in scm: return False
            if type(scm[key]) != type(value): return False
            if isinstance(value, str):
                value = env.substitute(value, "svmOverride::match")
                if not fnmatch.fnmatchcase(scm[key], value): return False
            else:
                if scm[key] != value: return False
        return True

    def __hash__(self):
        return hash((frozenset(self.__match.items()), frozenset(self.__del),
            frozenset(self.__set.items()), frozenset(self.__replace.items())))

    def __eq__(self, other):
        return ((self.__match, self.__del, self.__set, self.__replace) ==
            (other.__match, other.__del, other.__set, other.__replace))

    def __applyEnv(self, env):
        rm = [ env.substitute(d, "svmOverrides::del") for d in self.__del ]
        set = {
            k : env.substitute(v, "svmOverrides::set: "+k) if isinstance(v, str) else v
            for (k,v) in self.__set.items()
        }
        return rm, set

    def mangle(self, scm, env):
        ret = False
        if self.__doesMatch(scm, env):
            rm, set = self.__applyEnv(env)

            ret = True
            scm = scm.copy()
            for d in rm:
                if d in scm: del scm[d]
            scm.update(set)
            for (key, (pat, repl)) in self.__replace.items():
                if key in scm:
                    scm[key] = re.sub(pat, repl, scm[key])
        return ret, scm

    def __str__(self):
        import yaml
        spec = {}
        if self.__match: spec['match'] = self.__match
        if self.__del: spec['del'] = self.__del
        if self.__set: spec['set'] = self.__set
        if self.__replaceRaw: spec['replace'] = self.__replaceRaw
        return yaml.dump(spec, default_flow_style=False).rstrip()


class ScmTaint(Enum):
    """
    The taint flags are single letter flags that indicate certain states of the
    SCM.

    Their meaning is as follows:
     - attic - Recipe changed. Will be moved to attic.
     - collides - New checkout but obstructed by existing file.
     - error - Something went really wrong when getting status.
     - modified -  The SCM has been modified wrt. checked out commit.
     - new - New checkout.
     - overridden - A scmOverrides entry applies.
     - switched - The SCM branch/tag/commit was changed by the user.
     - unknown - Not enough information to get further status.
     - unpushed_main - Configured branch with commits not in remote.
     - unpushed_local - Some local branch with unpushed commits exists.
    """
    attic = 'A'
    collides = 'C'
    error = 'E'
    modified = 'M'
    new = 'N'
    overridden = 'O'
    switched = 'S'
    unknown = '?'
    unpushed_main = 'U'
    unpushed_local = 'u'

class ScmStatus:
    """"
    Describes an SCM status wrt. recipe.

    The important status is stored as a set of ScmTaint flags. Additionally the
    'description' field holds any output from the SCM tool that is interesting
    to the user to judge the SCM status. This is only shown in verbose output
    mode.
    """

    def __init__(self, flag=None, description=""):
        self.__flags = {}
        if flag is not None:
            self.__flags[flag] = description

    def __str__(self):
        return "".join(sorted(f.value for f in self.flags))

    @property
    def clean(self):
        """
        Is SCM branch/tag/commit the same as specified in the recipe and no
        local changes?
        """
        return not self.dirty

    @property
    def dirty(self):
        """
        Is SCM is dirty?

        Could be: errors, modified files or switched to another
        branch/tag/commit/repo.  Unpushed commits on the configured branch also
        count as dirty because they are locally commited changes that are not
        visible upstream. On the other hand unpushed changes on unrelated
        branches (unpushed_local) do not count.
        """
        return bool(self.flags & {ScmTaint.modified, ScmTaint.error,
            ScmTaint.switched, ScmTaint.unpushed_main})

    @property
    def error(self):
        """
        Check if SCM is in an error state.

        Set if the SCM command returned a error code or something unexpected
        happened while gathering the status.
        """
        return ScmTaint.error in self.flags

    @property
    def expendable(self):
        """
        Could the SCM be deleted without loosing user data?

        This is more strict than 'dirty' because it includes unrelated local
        branches that the user might have created.
        """
        return not self.dirty and self.flags.isdisjoint(
            {ScmTaint.unpushed_local, ScmTaint.unknown})

    @property
    def flags(self):
        return frozenset(self.__flags.keys())

    def description(self, subset=None):
        if subset:
            flags = {
                flag : description for flag,description in self.__flags.items()
                if flag in subset
            }
        else:
            flags = self.__flags

        # join active descriptions sorted by flag value
        return joinLines(*(d for f,d in
            sorted(flags.items(), key=lambda x: x[0].value)))

    def add(self, flag, description=""):
        if flag in self.__flags:
            self.__flags[flag] = joinLines(self.__flags[flag], description)
        else:
            self.__flags[flag] = description

    def merge(self, other):
        for flag,description in other.__flags.items():
            self.add(flag, description)


class Scm(metaclass=ABCMeta):
    def __init__(self, spec, overrides):
        # Recipe foobar, checkoutSCM dir:., url:asdf
        self.__source = spec.get("__source", "<unknown>") + " in checkoutSCM: dir:" + \
            spec.get("dir", ".") + ", url:" + spec.get("url", "?")
        self.__recipe = spec["recipe"]
        self.__overrides = overrides

    def _diffSpec(self, oldScm):
        oldSpec = oldScm.getProperties(False)
        newSpec = self.getProperties(False)
        ret = set()
        for k in sorted(set(oldSpec.keys()) | set(newSpec.keys())):
            if oldSpec.get(k) != newSpec.get(k):
                ret.add(k)

        ret -= SYNTHETIC_SCM_PROPS
        ret -= {"if"}
        return ret

    def _getRecipe(self):
        return self.__recipe

    def getSource(self):
        return self.__source

    def getProperties(self, isJenkins, pretty):
        # XXX: keep in sync with SYNTHETIC_SCM_PROPS
        ret = {
            "recipe" : self.__recipe,
            "overridden" : bool(self.__overrides),
        }
        if not pretty:
            ret["__source"] = self.__source
        return ret

    @abstractmethod
    async def invoke(self, invoker):
        """Execute the SCM checkout with the passed invoker instance.

        Everything must be done with the passed invoker instance. It will be
        configured for the right workspace and will do the logging, error
        handling and so on...
        """

    def canSwitch(self, oldScm):
        """Determine if an inline switch of a checkout from oldScm is
        possible.

        The judgement is purely done on the specification of this SCM and
        oldScm. If the SCM supports a switch from oldScm then this method
        may return True. It must return False in any other case. In case it
        returns True the Scm.switch() method will be invoked to do the acutal
        switch in the workspace. This might still fail if the workspace is in
        an unexpected state.
        """
        return False

    async def switch(self, workspacePath, oldScm):
        """Try to switch the checkout in the workspace from oldScm.

        If the switch succeeds then the checkout won't be moved to the attic.
        The SCM has to make sure that the result is the same as if the SCM was
        moved to the attic and a fresh checkout would have been done.
        """
        return False

    @abstractmethod
    def asDigestScript(self):
        """Return forward compatible stable string describing this SCM.

        The string should represent what the SCM checks out. This is different
        from the actual actions that are returned by asScript() or asJenkins()
        which might evolve in future versions. The returned string is used to
        compute the various IDs and to detect changes to the SDM.
        """
        return ""

    def asJenkins(self, workPath, config):
        """Return Jenkins xml.etree.ElementTree fragment that does the checkout.

        This is only called if hasJenkinsPlugin() returns True. In this case
        asScript() is not used on Jenkins.
        """
        return None

    def hasJenkinsPlugin(self):
        """Does this SCM use a Jenins plugin?"""
        return False

    @abstractmethod
    def getDirectory(self):
        """Return relative directory that this SCM owns in the workspace."""
        return ""

    @abstractmethod
    def isDeterministic(self):
        """Return whether the SCM is deterministic."""
        return False

    def isLocal(self):
        """Return true if the SCM does not use any remote repository.

        Such SCMs are treated special because there is no notion of
        checkout/checkin."""
        return False

    def status(self, workspacePath):
        """Get SCM work-space status.

        The purpose of this method is to return the status of the given
        directory in the work-space. The returned value is used for 'bob
        status' and to implement --clean-checkout. Shall return a ScmStatus()
        object.

        This method is called when building with --clean-checkout. If the
        returned ScmStatus objects 'error' or 'dirty' properties are True then
        the SCM is moved to the attic, while clean directories are not.
        """

        return ScmStatus()

    def getActiveOverrides(self):
        """Return list of ScmOverride objects that matched this SCM."""
        return self.__overrides

    def getAuditSpec(self):
        """Return spec for audit trail generation.

        Must return a tuple of three elements. The first element is a string that
        is used to find the right Audit class (see bob.audit.Artifact.SCMS).
        The second element is a relative directory in the workspace that must
        be audited. The third element is a dict with additional meta information
        that is passed to the audit scanner.

        If the SCM does not support audit trail generation then None shall be
        returned.
        """
        return None

    def hasLiveBuildId(self):
        """Check if live build-ids are supported."""
        return False

    async def predictLiveBuildId(self, step):
        """Query server to predict live build-id."""
        return None

    def calcLiveBuildId(self, workspacePath):
        """Calculate live build-id from workspace."""
        return None

    def postAttic(self, workspace):
        pass

class ScmAudit(metaclass=ABCMeta):
    @classmethod
    async def fromDir(cls, workspace, dir, extra):
        """Create SCM audit record by scanning a directory"""
        scm = cls()
        await scm._scanDir(workspace, dir, extra)
        return scm

    @classmethod
    def fromData(cls, data):
        """Restore SCM audit from serialized record"""
        scm = cls()
        scm._load(data)
        return scm

    @abstractmethod
    async def _scanDir(self, workspace, dir):
        """Scan directory for SCM"""
        pass

    @abstractmethod
    def _load(self, data):
        """Load from persisted record"""
        pass

    @abstractmethod
    def dump(self):
        """Serialize state into an ElementTree.Element"""
        pass

    def getStatusLine(self):
        return "unknown"
