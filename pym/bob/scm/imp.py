# Bob build tool
# Copyright (C) 2020  Jan Klötzke
#
# SPDX-License-Identifier: GPL-3.0-or-later

from ..errors import BuildError, ParseError
from ..stringparser import IfExpression
from ..tty import stepAction, INFO, TRACE, WARNING
from ..utils import asHexStr, hashDirectory, emptyDirectory, tarfileOpen
from .scm import Scm, ScmAudit
import base64
import functools
import io
import os, os.path
import posixpath
import re
import schema
import shutil
import stat
import tempfile

def _translateGlob(pattern):
    r"""Translate rsync wildcard pattern into regular expression.

    The following wildcards are supported:

    * ``*`` matches any path component, but it stops at slashes.
    * ``**`` matches anything, including slashes.
    * ``?`` matches any character except a slash.
    * ``[...]`` matches a character in the set. A leading ``!`` or ``^``
      negates the set. Never matches a slash.
    * ``\`` escapes the next character.
    """
    i, n = 0, len(pattern)
    res = []
    while i < n:
        c = pattern[i]
        i += 1
        if c == '*':
            if i < n and pattern[i] == '*':
                while i < n and pattern[i] == '*':
                    i += 1
                res.append('.*')
            else:
                res.append('[^/]*')
        elif c == '?':
            res.append('[^/]')
        elif c == '[':
            j = i
            if j < n and pattern[j] in '!^': j += 1
            if j < n and pattern[j] == ']': j += 1
            while j < n and pattern[j] != ']': j += 1
            if j >= n:
                res.append(re.escape(c))
            else:
                chars = pattern[i:j]
                i = j+1
                negate = chars[:1] in ('!', '^')
                if negate: chars = chars[1:]
                chars = "".join(('\\' + x) if x in '\\[]^&~|' else x for x in chars)
                res.append('(?!/)[' + ('^' if negate else '') + chars + ']')
        elif c == '\\' and i < n:
            res.append(re.escape(pattern[i]))
            i += 1
        else:
            res.append(re.escape(c))
    return "".join(res)

class FilterRule:
    """A single rsync-like filter rule.

    Rules are either given in their short ("+ PATTERN", "- PATTERN") or long
    form ("include PATTERN", "exclude PATTERN"). The pattern is matched like
    rsync does:

    * A leading ``/`` anchors the pattern at the root of the imported
      directory. Otherwise the pattern may match at any depth.
    * A trailing ``/`` matches only directories.
    * A pattern without ``/`` or ``**`` is matched against the final path
      component only.
    * A trailing ``dir/***`` matches the directory and everything inside it.
    """

    # Like in rsync filter files, rules cannot span multiple lines.
    RULE_RE = re.compile(r'^(\+|-|include|exclude) ([^\n]+)\Z')

    def __init__(self, rule):
        m = self.RULE_RE.match(rule)
        if m is None:
            raise ParseError("Invalid import filter rule: '{}'".format(rule),
                             help="Rules must be of the form '+ PATTERN', '- PATTERN', "
                                  "'include PATTERN' or 'exclude PATTERN'.")
        self.include = m.group(1) in ('+', 'include')
        pattern = m.group(2)

        self.dirOnly = pattern.endswith('/') and pattern != '/'
        if self.dirOnly:
            pattern = pattern[:-1]

        try:
            # Trailing "/***" matches the directory itself and everything below.
            self.dirItself = None
            if pattern.endswith('/***'):
                self.dirItself = self.__compile(pattern[:-4])
            self.regex = self.__compile(pattern)
        except re.error as e:
            raise ParseError("Invalid import filter rule '{}': {}".format(rule, str(e)))

    @staticmethod
    def __compile(pattern):
        """Compile pattern to (regex, prependSlash) tuple."""
        if pattern.startswith('/'):
            # Anchored at root.
            return (re.compile(_translateGlob(pattern[1:]), re.DOTALL), False)
        elif pattern.startswith('**'):
            # Like rsync, let "**/" also match at the start of the path by
            # matching against the path with a leading slash.
            return (re.compile(_translateGlob(pattern), re.DOTALL), True)
        else:
            # Match against the trailing path components.
            return (re.compile('(?:.*/)?' + _translateGlob(pattern), re.DOTALL), False)

    def matches(self, path, isDir):
        if self.dirOnly and not isDir:
            return False
        if isDir and self.dirItself is not None and self.__match(self.dirItself, path):
            return True
        return self.__match(self.regex, path)

    @staticmethod
    def __match(regex, path):
        regex, prependSlash = regex
        if prependSlash:
            path = '/' + path
        return regex.fullmatch(path) is not None

@functools.lru_cache(maxsize=None)
def parseFilterRule(rule):
    return FilterRule(rule)

def selectTree(src, rules, pruneEmptyDirs):
    """Compute the set of imported paths.

    Returns the set of paths (relative to src, using '/' as separator) that
    are imported. Returns None if no filtering is requested, i.e. everything
    is imported.

    The rules are evaluated in order for each entry. The first matching rule
    determines whether the entry is included or excluded. Entries that do not
    match any rule are included. Like rsync, excluded directories are not
    traversed. Optionally, directories that do not contain any imported
    entries are skipped too.
    """

    if not rules and not pruneEmptyDirs:
        return None

    rules = [ parseFilterRule(r) for r in rules ]
    selected = set()

    def isIncluded(path, isDir):
        for r in rules:
            if r.matches(path, isDir):
                return r.include
        return True

    def scan(srcDir, relDir):
        found = False
        with os.scandir(srcDir) as it:
            for entry in it:
                path = posixpath.join(relDir, entry.name)
                isDir = entry.is_dir(follow_symlinks=False)
                if not isIncluded(path, isDir):
                    continue
                if isDir:
                    if scan(entry.path, path) or not pruneEmptyDirs:
                        selected.add(path)
                        found = True
                else:
                    selected.add(path)
                    found = True
        return found

    try:
        scan(src, "")
    except OSError as e:
        raise BuildError("Error gathering files: {}".format(str(e)))

    if rules and not selected:
        raise BuildError("Cannot import '{}': filter rules exclude everything!".format(src))

    return selected

def copyTree(src, dst, selection=None, relDir=""):
    """Recursively copy directory tree.

    The src and dst directories must already exist. The items in the source
    directory are copied to the destination only if it does not exist yet or if
    it is newer than the destination. The copy operation is aborted if the
    source and destination file types differ (file vs. directory vs. symlink).

    If a selection is given (see selectTree()), only the paths in the set are
    copied.
    """

    changed = False
    try:
        todo = []
        for name in os.listdir(src):
            relName = posixpath.join(relDir, name)
            if (selection is not None) and (relName not in selection):
                continue

            srcName = os.path.join(src, name)
            dstName = os.path.join(dst, name)

            srcStat = os.lstat(srcName)
            try:
                dstStat = os.lstat(dstName)
            except OSError:
                dstStat = None

            if dstStat is not None:
                if stat.S_IFMT(srcStat.st_mode ^ dstStat.st_mode) != 0:
                    raise BuildError("Copy failed: destination has different type: " + srcName)

            todo.append((srcName, srcStat, dstName, dstStat, relName))

        # First handle everything *except* symlinks. On MSYS the destination
        # must exist when creating the symlink, probably because the target
        # type must be determined on creation.
        for srcName, srcStat, dstName, dstStat, relName in todo:
            if stat.S_ISLNK(srcStat.st_mode):
                pass
            elif stat.S_ISDIR(srcStat.st_mode):
                if dstStat is None:
                    os.mkdir(dstName)
                    changed = True
                if copyTree(srcName, dstName, selection, relName):
                    shutil.copystat(srcName, dstName)
            else:
                if (dstStat is not None) and (srcStat.st_mtime_ns <= dstStat.st_mtime_ns):
                    continue
                shutil.copy2(srcName, dstName)
                changed = True

        # Now handle all symlinks.
        for srcName, srcStat, dstName, dstStat, relName in todo:
            if stat.S_ISLNK(srcStat.st_mode):
                linkTo = os.readlink(srcName)
                if dstStat is not None:
                    oldLink = os.readlink(dstName)
                    if linkTo == oldLink:
                        continue
                    os.unlink(dstName)
                os.symlink(linkTo, dstName)
                changed = True
    except OSError as e:
        raise BuildError("Copy failed: " + str(e))

    return changed

def packTree(src, rules=[], pruneEmptyDirs=False):
    if not os.path.isdir(src):
        raise BuildError("Cannot import '{}': not a directory!".format(src))

    selection = selectTree(src, rules, pruneEmptyDirs)
    try:
        f = io.BytesIO()
        with tarfileOpen(fileobj=f, mode="w:xz") as tar:
            # Special handling for MSYS. Symlinks fail if the target does not
            # exist and Python will fall back to create a copy on extraction.
            # To prevent this first everything *except* symlinks is archived
            # and then the symlinks are added.
            symlinks = []
            def filterSymlinks(ti):
                if selection is not None:
                    name = posixpath.normpath(ti.name)
                    if (name != ".") and (name not in selection):
                        return None
                if ti.issym():
                    symlinks.append(ti)
                    return None
                else:
                    return ti

            tar.add(src, arcname=".", filter=filterSymlinks)
            for ti in symlinks:
                tar.addfile(ti)
    except OSError as e:
        raise BuildError("Error gathering files: {}".format(str(e)))
    return base64.b85encode(f.getvalue()).decode('ascii')

def unpackTree(data, dest):
    try:
        f = io.BytesIO(base64.b85decode(data))
        with tarfileOpen(fileobj=f, mode="r:xz") as tar:
            tar.extractall(dest)
    except OSError as e:
        raise BuildError("Error unpacking files: {}".format(str(e)))

class ImportScm(Scm):

    DEFAULTS = {
        schema.Optional('dir') : str,
        schema.Optional('prune') : bool,
        schema.Optional('recipeRelative') : bool,
        schema.Optional('pruneEmptyDirs') : bool,
    }

    __SCHEMA = {
        'scm' : 'import',
        'url' : str,
        schema.Optional('if') : schema.Or(str, IfExpression),
        schema.Optional('filter') : [schema.Regex(FilterRule.RULE_RE.pattern,
            error="Invalid import filter rule '{}'. Expected '+ PATTERN', '- PATTERN', 'include PATTERN' or 'exclude PATTERN'.")],
    }

    SCHEMA = schema.Schema({**__SCHEMA, **DEFAULTS})

    DEFAULT_VALUES = {
        "dir" : ".",
        "recipeRelative" : False,
        "filter" : [],
        "pruneEmptyDirs" : False,
    }

    def __init__(self, spec, overrides=[], pruneDefault=None, fixDigestBug=False, projectRoot=""):
        super().__init__(spec, overrides)
        self.__url = spec["url"]
        self.__dir = spec.get("dir", ".")
        self.__prune = spec.get("prune", pruneDefault or False)
        self.__data = spec.get("__data")
        self.__projectRoot = spec.get("__projectRoot", projectRoot)
        self.__fixDigestBug = fixDigestBug
        self.__recipeRelative = spec.get("recipeRelative", False)
        self.__filter = spec.get("filter", [])
        self.__pruneEmptyDirs = spec.get("pruneEmptyDirs", False)
        # Validate rules early
        for r in self.__filter: parseFilterRule(r)

    def _getSrcDir(self):
        rootDir = os.path.dirname(self._getRecipe()) if self.__recipeRelative else self.__projectRoot
        return os.path.join(rootDir, self.__url)

    def getProperties(self, isJenkins, pretty=False):
        ret = super().getProperties(isJenkins, pretty)
        ret.update({
            'scm' : 'import',
            'url' : self.__url,
            'dir' : self.__dir,
            'prune' : self.__prune,
            'recipeRelative' : self.__recipeRelative,
            'filter' : self.__filter,
            'pruneEmptyDirs' : self.__pruneEmptyDirs,
        })
        if isJenkins:
            ret['__data'] = packTree(self._getSrcDir(), self.__filter, self.__pruneEmptyDirs)
        elif not pretty:
            ret['__projectRoot'] = self.__projectRoot

        if pretty:
            ret = { k : v for k, v in ret.items()
                    if v is not None and v != self.DEFAULT_VALUES.get(k) }

        return ret

    async def invoke(self, invoker, workspaceCreated):
        dest = invoker.joinPath(self.__dir)
        os.makedirs(dest, exist_ok=True)
        if self.__prune: emptyDirectory(dest)
        if self.__data is None:
            src = self._getSrcDir()
            if not os.path.isdir(src):
                invoker.fail("Cannot import '{}': not a directory!".format(src))
            try:
                copyTree(src, dest, selectTree(src, self.__filter, self.__pruneEmptyDirs))
            except BuildError as e:
                invoker.fail(e.slogan)
        else:
            unpackTree(self.__data, dest)

    def asDigestScript(self):
        if self.__fixDigestBug:
            ret = self.__url + " " + self.__dir
        else:
            ret = self.__url
        # Only add new options if used to keep the variant-id of existing
        # recipes stable. Each one is put on a separate line. Rules cannot
        # contain newlines so this is unambiguous.
        if self.__pruneEmptyDirs:
            ret += "\npruneEmptyDirs"
        for rule in self.__filter:
            ret += "\nfilter " + rule
        return ret

    def getDirectory(self):
        return self.__dir

    def isDeterministic(self):
        return False

    def isLocal(self):
        return True

    def hasLiveBuildId(self):
        return True

    async def predictLiveBuildId(self, step):
        with stepAction(step, "HASH", self.__url, (INFO, TRACE)) as a:
            src = self._getSrcDir()
            if not self.__filter and not self.__pruneEmptyDirs:
                return hashDirectory(src)

            # The workspace will only hold the selected files. Make a
            # temporary copy of them to calculate the expected hash.
            try:
                selection = selectTree(src, self.__filter, self.__pruneEmptyDirs)
                with tempfile.TemporaryDirectory() as tmp:
                    copyTree(src, tmp, selection)
                    return hashDirectory(tmp)
            except BuildError as e:
                a.fail(e.slogan, WARNING)
                return None

    def calcLiveBuildId(self, workspacePath):
        return hashDirectory(os.path.join(workspacePath, self.__dir))

    def getAuditSpec(self):
        extra = {"url" : self.__url}
        if self.__filter: extra["filter"] = self.__filter
        if self.__pruneEmptyDirs: extra["pruneEmptyDirs"] = True
        return ("import", self.__dir, extra)


class ImportAudit(ScmAudit):

    SCHEMA = schema.Schema({
        'type' : 'import',
        'dir' : str,
        'digest' : {
            'algorithm' : 'sha1',
            'value' : str
        },
        'url' : str,
        schema.Optional('filter') : [str],
        schema.Optional('pruneEmptyDirs') : bool,
    })

    async def _scanDir(self, workspace, dir, extra):
        self.__dir = dir
        self.__hash = asHexStr(hashDirectory(os.path.join(workspace, dir)))
        self.__url = extra.get("url")
        self.__filter = extra.get("filter", [])
        self.__pruneEmptyDirs = extra.get("pruneEmptyDirs", False)

    def _load(self, data):
        self.__dir = data["dir"]
        self.__hash = data["digest"]["value"]
        self.__url = data["url"]
        self.__filter = data.get("filter", [])
        self.__pruneEmptyDirs = data.get("pruneEmptyDirs", False)

    def dump(self):
        ret = {
            "type" : "import",
            "dir" : self.__dir,
            "digest" : {
                "algorithm" : "sha1",
                "value" : self.__hash
            },
            "url" : self.__url,
        }
        if self.__filter: ret["filter"] = self.__filter
        if self.__pruneEmptyDirs: ret["pruneEmptyDirs"] = True
        return ret
