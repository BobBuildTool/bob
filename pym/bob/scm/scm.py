# Bob build tool
# Copyright (C) 2017  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..utils import joinLines
from abc import ABCMeta, abstractmethod
from enum import Enum
from pipes import quote
import fnmatch
import re

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

    def __doesMatch(self, scm, env):
        for (key, value) in self.__match.items():
            if key not in scm: return False
            value = env.substitute(value, "svmOverride::match")
            if not fnmatch.fnmatchcase(scm[key], value): return False
        return True

    def __hash__(self):
        return hash((frozenset(self.__match.items()), frozenset(self.__del),
            frozenset(self.__set.items()), frozenset(self.__replace.items())))

    def __eq__(self, other):
        return ((self.__match, self.__del, self.__set, self.__replace) ==
            (other.__match, other.__del, other.__set, other.__replace))

    def __applyEnv(self, env):
        rm = [ env.substitute(d, "svmOverride::del") for d in self.__del ]
        set = { k : env.substitute(v, "svmOverride::set"+k) for (k,v) in self.__set.items() }
        replace = { k : env.substitute(v, "svmOverride::replace"+k) for (k,v) in self.__replace.items() }
        return rm, set, replace

    def mangle(self, scm, env):
        ret = False
        if self.__doesMatch(scm, env):
            rm, set, replace = self.__applyEnv(env)

            ret = True
            scm = scm.copy()
            for d in rm:
                if d in scm: del scm[d]
            scm.update(set)
            for (key, (pat, repl)) in replace.items():
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

    def __init__(self, *flags, description=""):
        self.__flags = set(flags)
        self.__description = description

    def __str__(self):
        return "".join(sorted(f.value for f in self.__flags))

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
        return bool(self.__flags & {ScmTaint.modified, ScmTaint.error,
            ScmTaint.switched, ScmTaint.unpushed_main})

    @property
    def error(self):
        """
        Check if SCM is in an error state.

        Set if the SCM command returned a error code or something unexpected
        happened while gathering the status.
        """
        return ScmTaint.error in self.__flags

    @property
    def expendable(self):
        """
        Could the SCM be deleted without loosing user data?

        This is more strict than 'dirty' because it includes unrelated local
        branches that the user might have created.
        """
        return self.dirty or (ScmTaint.unpushed_local in self.__flags)

    @property
    def flags(self):
        return frozenset(self.__flags)

    @property
    def description(self):
        return self.__description

    def add(self, flag, description=""):
        self.__flags.add(flag)
        if description:
            self.__description = joinLines(self.__description, description)

    def merge(self, other):
        self.__flags |= other.__flags
        self.__description = joinLines(self.__description, other.__description)


class Scm(metaclass=ABCMeta):
    def __init__(self, spec, overrides):
        # Recipe foobar, checkoutSCM dir:., url:asdf
        self.__source = spec.get("__source", "<unknown>") + " in checkoutSCM: dir:" + \
            spec.get("dir", ".") + ", url:" + spec.get("url", "?")
        self.__recipe = spec["recipe"]
        self.__overrides = overrides

    def getProperties(self):
        return {
            "recipe" : self.__recipe
        }

    def asScript(self):
        """Return bash script fragment that does the checkout.

        The base class returns just the header. The deriving class has to
        append the acutal script.
        """
        return "_BOB_SOURCES[$LINENO]=" + quote(self.__source)

    @abstractmethod
    def asDigestScript(self):
        """Return forward compatible stable string describing this SCM.

        The string should represent what the SCM checks out. This is different
        from the actual actions that are returned by asScript() or asJenkins()
        which might evolve in future versions. The returned string is used to
        compute the various IDs and to detect changes to the SDM.
        """
        return ""

    def asJenkins(self, workPath, credentials, options):
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

    def statusOverrides(self, workspacePath):
        """Return user visible status about SCM overrides.

        Returns a tuple of three elements:

          overridden, taintFlags, longStatus

        were 'overridden' is a boolean that is True if at least one override
        matched. The 'taintFlags' are single letters that indicate certain
        overrides. Only 'O' for 'overridded' is defined at the moment. Each SCM
        might define further flags. The 'longStatus' is shown in very verbose
        output modes and should contain the gory details.
        """
        overrides = self.getActiveOverrides()
        if len(overrides):
            status = "O"
            longStatus = ""
            for o in overrides:
                overrideText = str(o).rstrip().replace('\n', '\n       ')
                longStatus += "    > Overridden by:\n       {}\n".format(overrideText)
            return True, status, longStatus
        return False, '', ''

    def getAuditSpec(self):
        """Return spec for audit trail generation.

        Must return a tuple of two elements. The first element is a string that
        is used to find the right Audit class (see bob.audit.Artifact.SCMS).
        The second element is a relative directory in the workspace that must
        be audited.

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

    def getLiveBuildIdSpec(self, workspacePath):
        """Generate spec lines for bob-hash-engine."""
        return None

class ScmAudit(metaclass=ABCMeta):
    @classmethod
    def fromDir(cls, workspace, dir):
        """Create SCM audit record by scanning a directory"""
        scm = cls()
        scm._scanDir(workspace, dir)
        return scm

    @classmethod
    def fromData(cls, data):
        """Restore SCM audit from serialized record"""
        scm = cls()
        scm._load(data)
        return scm

    @abstractmethod
    def _scanDir(self, workspace, dir):
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
