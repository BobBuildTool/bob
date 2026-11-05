# Bob build tool
# SPDX-License-Identifier: GPL-3.0-or-later

"""A transport-independent, recipe-local unified-diff overlay."""

from ..errors import ParseError
from .scm import Scm, ScmStatus, ScmTaint, overlayFingerprint
import base64
import glob
import hashlib
import os
import re
import schema
import stat
import tempfile


# Changing this means the checkout identity changes too.  Keep it separate
# from Bob's general version: it describes precisely the patch language Bob
# promises to interpret.
_PATCH_FORMAT_VERSION = 'unified-v1'
_HUNK_HEADER = re.compile(br'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\n?$')
_MAX_HUNK_OFFSET = 100


class _PatchFormatError(Exception):
    pass


class PatchApplyError(ParseError):
    """Application failure, optionally with a verified retained base tree."""

    def __init__(self, message, directory, baseFingerprint=None):
        super().__init__(message)
        self.directory = directory
        self.baseFingerprint = baseFingerprint


class _PatchTransaction:
    """Prepare the whole overlay before writing, and journal writes for rollback."""

    def __init__(self, root, protected=()):
        self.root = os.path.realpath(root)
        self.protected = {os.path.normcase(os.path.realpath(os.path.join(self.root, p)))
                          for p in protected}
        self.originals = {}
        self.results = {}
        self.restored = True
        self.messages = []

    def path(self, target):
        if target is None:
            return None
        path = os.path.join(self.root, target)
        resolved = os.path.realpath(path)
        if os.path.commonpath((self.root, resolved)) != self.root:
            raise _PatchFormatError("target '{}' escapes checkout".format(target))
        relative = os.path.relpath(resolved, self.root)
        if (os.path.normcase(resolved) in self.protected or
                any(p in ('.git', '.svn', 'CVS') for p in relative.split(os.sep))):
            raise _PatchFormatError("target '{}' modifies protected SCM path".format(target))
        # Reject symlinks rather than replacing their referents or leaving
        # dangling links behind when a patch deletes a file.
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(resolved):
            raise _PatchFormatError("symlink target '{}' is not supported".format(target))
        if path not in self.originals:
            try:
                mode = os.lstat(path).st_mode
                if not stat.S_ISREG(mode):
                    raise _PatchFormatError("target '{}' is not a regular file".format(target))
                with open(path, 'rb') as f:
                    data = f.read()
                self.originals[path] = (data, stat.S_IMODE(mode))
            except FileNotFoundError:
                self.originals[path] = (None, None)
            except OSError as e:
                raise _PatchFormatError("cannot read '{}': {}".format(target, e))
            self.results[path] = self.originals[path][0]
        return path

    @staticmethod
    def _write(path, data, mode):
        if data is None:
            os.unlink(path)
            return
        fd, temporary = tempfile.mkstemp(prefix='.bob-patch-', dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            # SCM invocations use umask 022; text additions are nonexecutable.
            os.chmod(temporary, 0o644 if mode is None else mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def commit(self):
        written = []
        try:
            for path, data in self.results.items():
                original, mode = self.originals[path]
                if data == original:
                    continue
                self._write(path, data, mode)
                written.append(path)
        except OSError as e:
            errors = []
            for path in reversed(written):
                try:
                    self._write(path, *self.originals[path])
                except OSError as rollback:
                    errors.append(str(rollback))
            self.restored = not errors
            raise _PatchFormatError("cannot commit overlay: {}{}".format(
                e, '; rollback failed: ' + '; '.join(errors) if errors else ''))


def _header_path(line):
    """Return a unified-diff header path, excluding its optional timestamp."""
    path = line[4:].split(b'\t', 1)[0].rstrip(b'\r\n')
    if not path:
        raise _PatchFormatError("empty file name")
    try:
        return path.decode('utf-8')
    except UnicodeDecodeError:
        raise _PatchFormatError("file names must be UTF-8")


def _target_path(name, strip):
    if name == '/dev/null':
        return None
    parts = name.replace('\\', '/').split('/')
    target = '/'.join(parts[strip:])
    if not target or target.startswith('/') or '..' in parts[strip:]:
        raise _PatchFormatError("invalid target '{}'".format(name))
    target = os.path.normpath(target)
    if target == '.' or os.path.isabs(target) or os.path.splitdrive(target)[0]:
        raise _PatchFormatError("invalid target '{}'".format(name))
    return target


def _parse_patch(data, strip):
    """Parse the deliberately small, deterministic unified-diff dialect."""
    if b'\0' in data:
        raise _PatchFormatError("binary patches are not supported")
    lines = data.splitlines(keepends=True)
    files = []
    i = 0
    # A human-readable mail-style description before the first file is common
    # for recipe patches.  It has no effect on application, but is retained in
    # the patch digest.  Do not mistake known non-unified patch formats for a
    # description.
    while i < len(lines) and not lines[i].startswith(b'--- '):
        if lines[i].startswith((b'GIT binary patch', b'Binary files ', b'diff ',
                                b'Index: ', b'*** ')):
            raise _PatchFormatError("unsupported patch format at line {}".format(i + 1))
        i += 1
    while i < len(lines):
        if not lines[i].startswith(b'--- '):
            if lines[i].startswith((b'GIT binary patch', b'Binary files ')):
                raise _PatchFormatError("binary patches are not supported")
            raise _PatchFormatError("expected '---' file header at line {}".format(i + 1))
        old_name = _header_path(lines[i])
        i += 1
        if i == len(lines) or not lines[i].startswith(b'+++ '):
            raise _PatchFormatError("expected '+++' file header at line {}".format(i + 1))
        new_name = _header_path(lines[i])
        i += 1
        old_target = _target_path(old_name, strip)
        new_target = _target_path(new_name, strip)
        if old_target is None and new_target is None:
            raise _PatchFormatError("both file names are /dev/null")
        if old_target is not None and new_target is not None and old_target != new_target:
            # diff -u foo.orig foo is a conventional way to distribute a
            # modification.  It is not a rename: apply against foo itself.
            if old_target == new_target + '.orig':
                old_target = new_target
            else:
                raise _PatchFormatError("renames are not supported")
        hunks = []
        while i < len(lines) and lines[i].startswith(b'@@ '):
            match = _HUNK_HEADER.match(lines[i])
            if match is None:
                raise _PatchFormatError("malformed hunk header at line {}".format(i + 1))
            old_start, old_count, new_start, new_count = match.groups()
            old_start, new_start = int(old_start), int(new_start)
            old_count = int(old_count) if old_count is not None else 1
            new_count = int(new_count) if new_count is not None else 1
            if (old_count and not old_start) or (new_count and not new_start):
                raise _PatchFormatError("nonempty hunk has invalid location at line {}"
                                        .format(i + 1))
            i += 1
            records = []
            actual_old = actual_new = 0
            while i < len(lines):
                line = lines[i]
                if line == b'\\ No newline at end of file\n' or line == b'\\ No newline at end of file':
                    if not records or not records[-1][1].endswith(b'\n'):
                        raise _PatchFormatError("misplaced no-newline marker at line {}".format(i + 1))
                    records[-1] = (records[-1][0], records[-1][1][:-1])
                    i += 1
                    continue
                if actual_old == old_count and actual_new == new_count:
                    break
                if not line or line[:1] not in (b' ', b'+', b'-'):
                    raise _PatchFormatError("invalid hunk line at line {}".format(i + 1))
                records.append((line[:1], line[1:]))
                actual_old += line[:1] in (b' ', b'-')
                actual_new += line[:1] in (b' ', b'+')
                if actual_old > old_count or actual_new > new_count:
                    raise _PatchFormatError("hunk line count does not match header")
                i += 1
            if actual_old != old_count or actual_new != new_count:
                raise _PatchFormatError("hunk line count does not match header")
            hunks.append((old_start, old_count, new_start, new_count, records))
        if not hunks:
            raise _PatchFormatError("file '{}' has no hunks".format(old_name))
        files.append((old_target, new_target, hunks))
    if not files:
        raise _PatchFormatError("empty patch")
    return files


class _Patch:
    """One resolved patch and the metadata needed to replay it safely."""

    def __init__(self, path, name, strip, digest, data):
        self.path = path
        self.name = name
        self.strip = strip
        self.digest = digest
        self.data = data
        self.__parsed = None

    @classmethod
    def fromSpec(cls, root, name, spec, loadBinary=None):
        data = spec.get('__data')
        if data is not None:
            try:
                data = base64.b64decode(data, validate=True)
            except (TypeError, ValueError) as e:
                raise ParseError("Invalid persisted patch '{}': {}".format(name, e))
            digest = hashlib.sha256(data).hexdigest()
            if digest != spec.get('digestSHA256', digest):
                raise ParseError("Persisted patch digest does not match '{}'".format(name))
            path = None
        else:
            path = os.path.join(root, name)
            try:
                if loadBinary is None:
                    with open(path, 'rb') as f:
                        data = f.read()
                else:
                    # Besides reading the data this registers the patch with
                    # RecipeSet's package-cache digest.
                    data = loadBinary(path)
            except OSError as e:
                raise ParseError("Cannot read patch '{}': {}".format(name, e))
            digest = hashlib.sha256(data).hexdigest()

        strip = spec.get('strip', 1)
        if not isinstance(strip, int) or isinstance(strip, bool) or strip < 0:
            raise ParseError("Invalid patch strip level: '{}'".format(strip))
        return cls(path, name, strip, digest, data)

    def asProperty(self):
        return {'file': self.name, 'strip': self.strip,
                'digestSHA256': self.digest,
                '__data': base64.b64encode(self.data).decode('ascii')}

    def asManifestEntry(self):
        return {'path': self.name, 'strip': self.strip,
                'digestSHA256': self.digest}

    def withStrip(self, strip):
        """Replay these bytes using an updated target-path configuration."""
        return _Patch(self.path, self.name, strip, self.digest, self.data)

    def validateTargets(self, protected):
        try:
            parsed = self.__parse()
        except _PatchFormatError as e:
            raise ParseError("Unsupported patch '{}': {}".format(self.name, e))
        protected = {os.path.normcase(os.path.normpath(p)) for p in protected}
        for old_target, new_target, hunks in parsed:
            for target in (old_target, new_target):
                if target is None:
                    continue
                if (any(p in ('.git', '.svn', 'CVS') for p in target.replace('\\', '/').split('/')) or
                        os.path.normcase(target) in protected):
                    raise ParseError("Patch modifies protected SCM path: '{}'".format(target))

    def __parse(self):
        if self.__parsed is None:
            self.__parsed = _parse_patch(self.data, self.strip)
        return self.__parsed

    async def run(self, invoker, directory, reverse=False, transaction=None):
        """Apply the stored patch directly, without relying on host tools."""
        try:
            ownTransaction = transaction is None
            if ownTransaction:
                transaction = _PatchTransaction(invoker.joinPath(directory))
            parsed = self.__parse()
            for old_target, new_target, hunks in reversed(parsed) if reverse else parsed:
                self.__apply_file(transaction, old_target, new_target, hunks, reverse)
            if ownTransaction:
                transaction.commit()
                for message in transaction.messages:
                    invoker.info(message)
        except _PatchFormatError as e:
            raise ParseError("Cannot apply patch '{}': {}".format(self.name, e))

    @staticmethod
    def __apply_file(transaction, old_target, new_target, hunks, reverse):
        source, destination = (new_target, old_target) if reverse else (old_target, new_target)
        source_path = transaction.path(source)
        destination_path = transaction.path(destination)
        if source_path is None:
            if transaction.results[destination_path] is not None:
                raise _PatchFormatError("creation target '{}' already exists".format(destination))
            contents = []
        else:
            data = transaction.results[source_path]
            if data is None:
                raise _PatchFormatError("source file '{}' does not exist".format(source))
            contents = data.splitlines(keepends=True)
        offset = 0
        for old_start, old_count, new_start, new_count, records in hunks:
            source_start = new_start if reverse else old_start
            source_count = new_count if reverse else old_count
            declared_at = source_start - (1 if source_count else 0) + offset
            expected, replacement = [], []
            for kind, value in records:
                if reverse:
                    kind = {b'+': b'-', b'-': b'+'}.get(kind, kind)
                if kind in (b' ', b'-'):
                    expected.append(value)
                if kind in (b' ', b'+'):
                    replacement.append(value)
            at = declared_at
            matches_declared_location = (
                0 <= at <= len(contents) - len(expected) and
                contents[at:at + len(expected)] == expected)
            if not matches_declared_location:
                # An insertion has no source-side text to identify another
                # location.  It must remain anchored at its declared point.
                if not expected:
                    raise _PatchFormatError(
                        "hunk does not match declared location in '{}' at line {}"
                        .format(source or destination, source_start))
                lower = max(0, declared_at - _MAX_HUNK_OFFSET)
                upper = min(len(contents) - len(expected),
                            declared_at + _MAX_HUNK_OFFSET)
                matches = [candidate for candidate in range(lower, upper + 1)
                           if contents[candidate:candidate + len(expected)] == expected]
                if not matches:
                    raise _PatchFormatError(
                        "hunk does not match declared location in '{}' at line {}"
                        .format(source or destination, source_start))
                if len(matches) > 1:
                    raise _PatchFormatError(
                        "hunk matches multiple locations in '{}' near line {}"
                        .format(source or destination, source_start))
                at = matches[0]
                transaction.messages.append("Applied patch hunk in '{}' at offset {:+d}".format(
                    source or destination, at - declared_at))
            contents[at:at + len(expected)] = replacement
            offset += len(replacement) - len(expected)
        if destination_path is None:
            if contents:
                raise _PatchFormatError("deletion of '{}' left content behind".format(source))
            transaction.results[source_path] = None
            return
        if not os.path.isdir(os.path.dirname(destination_path)):
            raise _PatchFormatError("destination directory for '{}' does not exist".format(destination))
        transaction.results[destination_path] = b''.join(contents)


class PatchScm(Scm):
    PATCHES_SHEMA = [schema.Schema({
        schema.Or('file', 'files', 'series') : str,
        schema.Optional('strip') : int,
    })]

    def __init__(self, scm, patches, overlay=None, patchOnly=False, overlayFailed=False,
                 recipeSet=None, failedBase=None):
        self.__scm = scm
        self.__patches = self.__resolve(patches, recipeSet)
        self.__overlay = overlay
        self.__patchOnly = patchOnly
        self.__overlayFailed = overlayFailed
        self.__failedBase = failedBase

    def __resolve(self, patches, recipeSet):
        root = os.path.dirname(self.__scm._getRecipe().split('#', 1)[0])
        names = self.__resolvePaths(root, patches)
        loadBinary = getattr(recipeSet, 'loadBinary', None)
        return [_Patch.fromSpec(root, name, patch,
                                loadBinary)
                for name, patch in names]

    @classmethod
    def trackPatchFiles(cls, recipe, patches, loadBinary):
        """Include recipe patch files in the persistent package-cache key."""
        root = os.path.dirname(recipe.split('#', 1)[0])
        for patch in patches:
            if 'series' in patch:
                cls.__validateRecipePath(patch['series'])
                loadBinary(os.path.join(root, patch['series']))
        for name, patch in cls.__resolvePaths(root, patches):
            loadBinary(os.path.join(root, name))

    @classmethod
    def __resolvePaths(cls, root, patches):
        ret = []
        for patch in patches:
            if 'file' in patch:
                files = [patch['file']]
            elif 'files' in patch:
                pattern = patch['files']
                cls.__validateRecipePath(pattern)
                if '**' in pattern:
                    raise ParseError("Recursive patch glob is not supported: '{}'".format(pattern))
                files = [os.path.relpath(path, root) for path in
                         sorted(glob.glob(os.path.join(root, pattern)))
                         if os.path.isfile(path)]
                if not files:
                    raise ParseError("Patch glob '{}' did not match any files".format(pattern))
            elif 'series' in patch:
                cls.__validateRecipePath(patch['series'])
                series = os.path.join(root, patch['series'])
                try:
                    with open(series, encoding='utf-8') as f:
                        files = []
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            fields = line.split()
                            if len(fields) != 1:
                                raise ParseError("Patch series '{}' has unsupported options"
                                                 .format(patch['series']))
                            files.append(fields[0])
                except OSError as e:
                    raise ParseError("Cannot read patch series '{}': {}".format(patch['series'], e))
                files = [os.path.join(os.path.dirname(patch['series']), f) for f in files]
            else:
                raise ParseError("Patch entry needs 'file', 'files', or 'series'")
            for name in files:
                cls.__validateRecipePath(name)
                ret.append((name, patch))
        return ret

    @staticmethod
    def __validateRecipePath(name):
        if os.path.isabs(name) or '..' in os.path.normpath(name).split(os.sep):
            raise ParseError("Patch path escapes recipe directory: '{}'".format(name))

    def getProperties(self, isJenkins, pretty=False):
        ret = self.__scm.getProperties(isJenkins, pretty)
        ret['patches'] = [patch.asProperty() for patch in self.__patches]
        return ret

    async def invoke(self, invoker, workspaceCreated):
        if not self.__patchOnly:
            await self.__scm.invoke(invoker, workspaceCreated)
        await self.__apply(invoker)

    async def __apply(self, invoker):
        protected = set(self.__scm.getProtectedPaths())
        directory = self.__scm.getDirectory()
        transaction = _PatchTransaction(invoker.joinPath(directory), protected)
        base = overlayFingerprint(transaction.root)
        try:
            for patch in self.__patches:
                patch.validateTargets(protected)
                await patch.run(invoker, directory, transaction=transaction)
            transaction.commit()
            for message in transaction.messages:
                invoker.info(message)
        except (ParseError, _PatchFormatError) as e:
            raise PatchApplyError(e.slogan if isinstance(e, ParseError) else str(e), directory,
                                  base if transaction.restored else None) from e

    async def __unapply(self, invoker, stripOverrides=None):
        if self.__overlay is not None:
            path = invoker.joinPath(self.__scm.getDirectory())
            fingerprint = overlayFingerprint(path)
            if fingerprint != self.__overlay.get('fingerprint'):
                raise ParseError("Patched checkout was modified; refusing to reverse managed overlay")
        protected = set(self.__scm.getProtectedPaths())
        transaction = _PatchTransaction(invoker.joinPath(self.__scm.getDirectory()), protected)
        for patch in reversed(self.__patches):
            if stripOverrides is not None and patch.name in stripOverrides:
                patch = patch.withStrip(stripOverrides[patch.name])
            patch.validateTargets(protected)
            await patch.run(invoker, self.__scm.getDirectory(), reverse=True,
                            transaction=transaction)
        try:
            transaction.commit()
            for message in transaction.messages:
                invoker.info(message)
        except _PatchFormatError as e:
            raise ParseError("Cannot reverse overlay: {}".format(e)) from e

    def canSwitch(self, oldScm):
        """A patch-only recipe change is an inline SCM transition.

        The old patch contents and expected workspace fingerprint are persisted
        with the checkout state, so a changed stack can be reversed safely.
        """
        return (isinstance(oldScm, PatchScm) and
                (not oldScm.__overlayFailed or oldScm.__failedBase is not None) and
                self.__scm.canSwitch(oldScm.__scm))

    async def switch(self, invoker, oldScm):
        if oldScm.__overlayFailed:
            if (oldScm.__failedBase is None or
                    overlayFingerprint(invoker.joinPath(self.getDirectory())) != oldScm.__failedBase):
                raise ParseError("Failed overlay has no verified unchanged base; fresh checkout required")
            return await self.__scm.switch(invoker, oldScm.__scm)
        if oldScm.__overlay is None:
            # Legacy state without a managed-overlay fingerprint cannot
            # establish that the old patch was applied.
            # Leave the base checkout in place for the corrected overlay.
            return await self.__scm.switch(invoker, oldScm.__scm)
        # Keep old bytes for a safe reverse, but use the current path mapping.
        # A corrected strip level must be able to recover an existing checkout.
        strips = {patch.name: patch.strip for patch in self.__patches}
        await oldScm.__unapply(invoker, strips)
        # The normal checkout invocation following a successful inline switch
        # refreshes the base SCM and applies this overlay exactly once.
        return await self.__scm.switch(invoker, oldScm.__scm)

    def asDigestScript(self):
        return self.__scm.asDigestScript() + ' patch-format {}'.format(_PATCH_FORMAT_VERSION) + ''.join(
            ' patch {} p{}'.format(patch.digest, patch.strip)
            for patch in self.__patches)

    def getOverlayState(self):
        """Return serializable manifest data for Bob's checkout state."""
        return [patch.asManifestEntry() for patch in self.__patches]

    def getDirectory(self): return self.__scm.getDirectory()
    def getSource(self): return self.__scm.getSource()
    def hasJenkinsPlugin(self):
        return self.__scm.hasJenkinsPlugin()
    def asJenkins(self, workPath, config):
        return self.__scm.asJenkins(workPath, config)
    def getJenkinsPreRunProperties(self):
        if self.__scm.hasJenkinsPlugin():
            props = self.getProperties(True)
            props['__patchOnly'] = True
            return props
        return None
    def isDeterministic(self): return self.__scm.isDeterministic()
    def isLocal(self): return self.__scm.isLocal()
    def hasLiveBuildId(self): return self.__scm.hasLiveBuildId()
    async def predictLiveBuildId(self, step): return await self.__scm.predictLiveBuildId(step)
    def calcLiveBuildId(self, workspacePath): return self.__scm.calcLiveBuildId(workspacePath)
    def getAuditSpec(self):
        spec = self.__scm.getAuditSpec()
        if spec is None:
            return None
        typ, directory, extra = spec
        extra = extra.copy()
        extra['patches'] = self.getOverlayState()
        extra['patchDirectory'] = self.getDirectory()
        return typ, directory, extra
    def status(self, workspacePath, overlay=None):
        return self.statusWithOverlay(workspacePath, overlay)

    def statusWithOverlay(self, workspacePath, overlay=None):
        status = self.__scm.status(workspacePath)
        if overlay is not None:
            path = os.path.join(workspacePath, self.__scm.getDirectory())
            fingerprint = overlayFingerprint(path)
            if fingerprint == overlay.get('fingerprint'):
                status.remove(ScmTaint.modified)
        status.add(ScmTaint.patched, 'Declared patch overlay')
        return status
    def getActiveOverrides(self): return self.__scm.getActiveOverrides()
    def postAttic(self, workspace): return self.__scm.postAttic(workspace)
